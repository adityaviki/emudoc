"""The trash: what members delete waits there for TRASH_DAYS, to be restored or purged.

Deleting a project, a folder, a document or a chat marks its row (`deleted_at`, and on the first
three `deleted_by`) instead of removing it. A folder takes with it the subfolders and documents
that are live under it, marked with its own moment, which is how its restore finds exactly what
went with it: what was deleted from it before keeps a moment of its own and stays in the trash
by itself. A project is marked alone, since nothing reaches inside a project in the trash
(`visible_to` in app/access.py leaves it out), and everything in it comes back with it. A chat is
its starter's, in the trash as out of it.

Nothing in the trash is used: the library, the search, the facts, the agent's tools, the counts
and the plan's figures read live rows alone, so a deletion frees what the plan holds at once, as
it did before there was a trash, and a restore is checked against the plan like an upload
(`check_restore` in app/entitlements.py). A document waits as it was: its indexing stops at its
next run (`process_document` in app/indexing/pipeline.py), and a restore queues what is left.
Its bytes keep their place under the unique constraint on project and hash, so an upload of the
same file is told to restore it instead (`already_present` in app/uploads.py).

A restore puts each item back where it was, or, when that folder is in the trash itself, in the
nearest folder above it that is not, renamed as an upload would be when a live sibling took its
name meanwhile. Deleting for good, one item or a project's whole trash, removes the rows and then
the files, as deleting did before the trash; the housekeeper (app/maintenance.py) does the same
each round to what has been in the trash for TRASH_DAYS (`purge_trash`). A chat in the trash is
still a chat to its plan's retention (app/retention.py), so it goes at whichever comes first.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, Connection, Select, func, select, text, update
from sqlalchemy.orm import Session, aliased

from app.db import unbounded
from app.folders import folder_paths, subtree_ids, unique_filename, unique_folder_name
from app.jobs import queue_index_by_id
from app.models import Chat, Document, Folder, Project, User
from app.storage import project_prefix

# How long what a member deletes stays in the trash before the housekeeper deletes it for good.
TRASH_DAYS = 30
TRASH_KEPT = timedelta(days=TRASH_DAYS)
# How many of a project's newest items its trash lists at once; it says how many it holds.
SHOWN = 200

# The 404 of what is in the trash when it is asked for as if it were not, so that a tab or a
# citation left open says where it went.
DOCUMENT_IN_TRASH = (
    "This document is in the project's trash. Restore it from the library's trash to open it."
)
PASSAGE_IN_TRASH = (
    "The document this passage is from is in the project's trash. Restore it from the "
    "library's trash to read it."
)
NOT_IN_TRASH = (
    "This is not in the trash: it was restored or deleted for good meanwhile. Reload the page."
)

# Each round deletes for good what has been in the trash long enough: the projects first, with
# everything in them, then the documents, those that went by themselves and every one under a
# folder that goes (one live there would be lost to the folder's cascade without its files),
# then the folders, which take their subfolders, and the chats, leaving one still answering as
# the retention does.
_PURGE_PROJECTS = text(
    "DELETE FROM projects WHERE deleted_at < now() - make_interval(days => :days) "
    "RETURNING org_id, id"
)
_PURGE_DOCUMENTS = text(
    """
    WITH RECURSIVE gone (id) AS (
        SELECT id FROM folders WHERE deleted_at < now() - make_interval(days => :days)
        UNION
        SELECT f.id FROM folders AS f JOIN gone ON f.parent_id = gone.id
    )
    DELETE FROM documents
    WHERE deleted_at < now() - make_interval(days => :days)
       OR folder_id IN (SELECT id FROM gone)
    RETURNING storage_key
    """
)
_PURGE_FOLDERS = text("DELETE FROM folders WHERE deleted_at < now() - make_interval(days => :days)")
_PURGE_CHATS = text(
    """
    DELETE FROM chats
    WHERE deleted_at < now() - make_interval(days => :days)
      AND NOT EXISTS (
          SELECT 1 FROM chat_messages
          WHERE chat_messages.chat_id = chats.id AND chat_messages.status = 'streaming'
      )
    """
)


@dataclass(frozen=True)
class Item:
    """One thing in a project's trash, as it is listed: a folder with what went with it, a
    document deleted by itself, or one of the asker's own chats."""

    kind: str
    id: int
    name: str
    place: str | None
    documents: int
    mime_type: str | None
    deleted_at: datetime
    deleted_by: str | None

    @property
    def purged_at(self) -> datetime:
        return self.deleted_at + TRASH_KEPT


@dataclass(frozen=True)
class Emptied:
    """What a round of the housekeeper deleted for good, and the files to discard after its
    commit."""

    projects: int
    documents: int
    folders: int
    chats: int
    prefixes: list[str]


def trash_project(session: Session, project: Project, user_id: int) -> None:
    """Put the project in the trash with everything in it. The caller commits."""
    project.deleted_at = func.now()
    project.deleted_by = user_id


def trash_folder(session: Session, folder: Folder, user_id: int) -> int:
    """Put the folder in the trash with every live folder and document under it, at one
    moment; returns how many documents went with it. The caller commits."""
    ids = subtree_ids(session, folder)
    went = {"deleted_at": func.now(), "deleted_by": user_id}
    session.execute(update(Folder).where(Folder.id.in_(ids)).values(went))
    return len(
        session.scalars(
            update(Document)
            .where(Document.folder_id.in_(ids), Document.deleted_at.is_(None))
            .values(went)
            .returning(Document.id)
        ).all()
    )


def trash_document(session: Session, document: Document, user_id: int) -> None:
    document.deleted_at = func.now()
    document.deleted_by = user_id


def trash_chat(session: Session, chat: Chat) -> None:
    chat.deleted_at = func.now()


def went_with(session: Session, folder: Folder) -> tuple[set[int], list[Document]]:
    """The folders and documents that went into the trash with this one: those under it marked
    with its moment, reached through folders that went with it too."""
    ids = _subtree(_children(session, folder.project_id, folder.deleted_at), folder.id)
    documents = session.scalars(
        select(Document).where(
            Document.folder_id.in_(ids), Document.deleted_at == folder.deleted_at
        )
    ).all()
    return ids, list(documents)


def restore_project(session: Session, project: Project) -> None:
    """Take the project out of the trash, and queue what was left of its documents' indexing.
    The caller has checked the plan (`check_restore`) and commits."""
    project.deleted_at = None
    project.deleted_by = None
    _requeue(
        session,
        session.scalars(
            select(Document.id).where(
                Document.project_id == project.id,
                Document.deleted_at.is_(None),
                Document.status == "queued",
            )
        ).all(),
    )


def restore_folder(
    session: Session, folder: Folder, ids: set[int], documents: Sequence[Document]
) -> None:
    """Take the folder out of the trash with what went with it (`went_with`), into its parent or
    the nearest live folder above it, under a name no live sibling carries. The caller commits."""
    parent_id = live_ancestor(session, folder.project_id, folder.parent_id)
    name = unique_folder_name(session, folder.project_id, parent_id, folder.name)
    session.execute(
        update(Folder).where(Folder.id.in_(ids)).values(deleted_at=None, deleted_by=None)
    )
    folder.parent_id = parent_id
    folder.name = name
    for document in documents:
        document.deleted_at = None
        document.deleted_by = None
    _requeue(session, [document.id for document in documents if document.status == "queued"])


def restore_document(session: Session, document: Document) -> None:
    """Take the document out of the trash, into its folder or the nearest live one above it,
    under a name no live sibling carries. The caller commits."""
    folder_id = live_ancestor(session, document.project_id, document.folder_id)
    document.filename = unique_filename(session, document.project_id, folder_id, document.filename)
    document.folder_id = folder_id
    document.deleted_at = None
    document.deleted_by = None
    if document.status == "queued":
        _requeue(session, [document.id])


def restore_chat(session: Session, chat: Chat) -> None:
    chat.deleted_at = None


def live_ancestor(session: Session, project_id: int, folder_id: int | None) -> int | None:
    """The folder, when it is live, else the nearest live folder above it; None for the root."""
    if folder_id is None or session.get(Folder, folder_id).deleted_at is None:
        return folder_id
    tree = {
        row.id: row
        for row in session.execute(
            select(Folder.id, Folder.parent_id, Folder.deleted_at).where(
                Folder.project_id == project_id
            )
        )
    }
    while folder_id is not None and tree[folder_id].deleted_at is not None:
        folder_id = tree[folder_id].parent_id
    return folder_id


def _requeue(session: Session, document_ids: Sequence[int]) -> None:
    """Queue the indexing a document's run left for when it came out of the trash: a run that
    found it there stopped before its first stage (app/indexing/pipeline.py)."""
    queue_index_by_id(session.connection(), list(document_ids))


def purge_project(session: Session, project: Project) -> list[str]:
    """Delete the project for good with everything in it; returns the prefix whose files to
    discard after the caller's commit."""
    # One statement that deletes every passage, section and fact of every document with the
    # project, which for a large one takes longer than the role lets a request's statement run.
    unbounded(session)
    session.delete(project)
    return [project_prefix(project.org_id, project.id)]


def purge_folder(session: Session, folder: Folder) -> list[str]:
    """Delete the folder for good with everything under it, whatever went into the trash before
    it; returns the prefixes of the documents' files."""
    unbounded(session)
    ids = _subtree(_children(session, folder.project_id), folder.id)
    prefixes = [
        key.rpartition("/")[0] + "/"
        for key in session.scalars(select(Document.storage_key).where(Document.folder_id.in_(ids)))
    ]
    # The database's cascade takes the subfolders and the documents with it.
    session.delete(folder)
    return prefixes


def purge_document(session: Session, document: Document) -> list[str]:
    prefix = document.storage_prefix
    session.delete(document)
    return [prefix]


def purge_chat(session: Session, chat: Chat) -> None:
    session.delete(chat)


def _top_folders(project_id: int) -> Select:
    """The project's folders in the trash that went by themselves, not with a folder above
    them, with the name of who deleted each."""
    parent, by = aliased(Folder), aliased(User)
    return (
        select(Folder, by.name)
        .outerjoin(parent, parent.id == Folder.parent_id)
        .outerjoin(by, by.id == Folder.deleted_by)
        .where(
            Folder.project_id == project_id,
            Folder.deleted_at.is_not(None),
            parent.deleted_at.is_distinct_from(Folder.deleted_at),
        )
    )


def _top_documents(project_id: int) -> Select:
    """The project's documents in the trash that went by themselves, not with their folder."""
    by = aliased(User)
    return (
        select(Document, by.name)
        .outerjoin(Folder, Folder.id == Document.folder_id)
        .outerjoin(by, by.id == Document.deleted_by)
        .where(
            Document.project_id == project_id,
            Document.deleted_at.is_not(None),
            Folder.deleted_at.is_distinct_from(Document.deleted_at),
        )
    )


def _own_chats(project_id: int, user_id: int) -> Select:
    return select(Chat).where(
        Chat.project_id == project_id, Chat.user_id == user_id, Chat.deleted_at.is_not(None)
    )


def trash_of(session: Session, project: Project, user_id: int) -> tuple[list[Item], int]:
    """What the project's trash holds for the user, the newest SHOWN of it first, and how much
    it holds: the folders deleted with what went with them, the documents deleted by
    themselves, and the user's own chats."""
    queries = (
        _top_folders(project.id),
        _top_documents(project.id),
        _own_chats(project.id, user_id),
    )
    total = sum(
        session.scalar(select(func.count()).select_from(query.subquery())) for query in queries
    )
    newest = [
        *session.execute(queries[0].order_by(Folder.deleted_at.desc()).limit(SHOWN)),
        *session.execute(queries[1].order_by(Document.deleted_at.desc()).limit(SHOWN)),
    ]
    paths = folder_paths(session, project.id, trashed=True)
    counts = _went_with_counts(session, project.id) if newest else {}
    items = [
        Item(
            "folder" if isinstance(row, Folder) else "document",
            row.id,
            row.name if isinstance(row, Folder) else row.filename,
            paths.get(row.parent_id if isinstance(row, Folder) else row.folder_id),
            counts.get(row.id, 0) if isinstance(row, Folder) else 0,
            None if isinstance(row, Folder) else row.mime_type,
            row.deleted_at,
            name,
        )
        for row, name in newest
    ]
    starter = session.get(User, user_id).name
    items += [
        Item("chat", chat.id, chat.title, None, 0, None, chat.deleted_at, starter)
        for chat in session.scalars(queries[2].order_by(Chat.deleted_at.desc()).limit(SHOWN))
    ]
    items.sort(key=lambda item: (item.deleted_at, item.kind, item.id), reverse=True)
    return items[:SHOWN], total


@dataclass(frozen=True)
class Purged:
    """What deleting a project's trash for good removed, as the trash listed it, and the
    prefixes of the files to discard after the commit."""

    folders: list[Folder]
    documents: list[Document]
    chats: list[Chat]
    prefixes: list[str]


def empty_trash(session: Session, project: Project, user_id: int) -> Purged:
    """Delete for good everything the project's trash holds for the user: its folders and
    documents, and the user's own chats. The caller writes the audit rows and commits."""
    unbounded(session)
    folders = list(
        session.scalars(_top_folders(project.id).order_by(Folder.deleted_at.desc(), Folder.id))
    )
    documents = list(
        session.scalars(
            _top_documents(project.id).order_by(Document.deleted_at.desc(), Document.id)
        )
    )
    chats = list(
        session.scalars(_own_chats(project.id, user_id).order_by(Chat.deleted_at.desc(), Chat.id))
    )
    children = _children(session, project.id)
    # What sits under a folder that goes, deleted before it or with it, goes by the database's
    # cascade with it, so it is not deleted again by itself.
    below = {folder.id: _subtree(children, folder.id) - {folder.id} for folder in folders}
    gone = set().union(*below.values()) if below else set()
    in_gone = gone | {folder.id for folder in folders}
    prefixes = [
        key.rpartition("/")[0] + "/"
        for key in session.scalars(
            select(Document.storage_key).where(Document.folder_id.in_(in_gone))
        )
    ]
    for folder in folders:
        if folder.id not in gone:
            session.delete(folder)
    for document in documents:
        if document.folder_id not in in_gone:
            prefixes.append(document.storage_prefix)
            session.delete(document)
    for chat in chats:
        session.delete(chat)
    return Purged(folders, documents, chats, prefixes)


def _children(
    session: Session, project_id: int, moment: datetime | None = None
) -> dict[int | None, list[int]]:
    """Each folder's subfolders in the project, by id: all of them, or with `moment` those that
    went into the trash at it."""
    query = select(Folder.id, Folder.parent_id).where(Folder.project_id == project_id)
    if moment is not None:
        query = query.where(Folder.deleted_at == moment)
    children: dict[int | None, list[int]] = defaultdict(list)
    for folder_id, parent_id in session.execute(query):
        children[parent_id].append(folder_id)
    return children


def _subtree(children: dict[int | None, list[int]], folder_id: int) -> set[int]:
    ids, stack = set(), [folder_id]
    while stack:
        current = stack.pop()
        ids.add(current)
        stack.extend(children[current])
    return ids


def _went_with_counts(session: Session, project_id: int) -> dict[int, int]:
    """For each folder of the project in the trash, how many documents went with it."""
    folders = session.execute(
        select(Folder.id, Folder.parent_id, Folder.deleted_at).where(
            Folder.project_id == project_id, Folder.deleted_at.is_not(None)
        )
    ).all()
    by_id = {folder.id: folder for folder in folders}
    held: dict[int, int] = defaultdict(int)
    for folder_id, moment, count in session.execute(
        select(Document.folder_id, Document.deleted_at, func.count())
        .where(Document.project_id == project_id, Document.deleted_at.is_not(None))
        .group_by(Document.folder_id, Document.deleted_at)
    ):
        # Each count climbs to every folder above it that went at the same moment.
        while folder_id in by_id and by_id[folder_id].deleted_at == moment:
            held[folder_id] += count
            folder_id = by_id[folder_id].parent_id
    return held


def trashed_projects(
    session: Session, condition: ColumnElement[bool]
) -> list[tuple[Project, str | None, int]]:
    """The projects in the trash that meet the condition (which the account sees), the latest
    deleted first, with who deleted each and how many documents it holds."""
    by = aliased(User)
    held = (
        select(func.count())
        .where(Document.project_id == Project.id, Document.deleted_at.is_(None))
        .correlate(Project)
        .scalar_subquery()
    )
    return [
        tuple(row)
        for row in session.execute(
            select(Project, by.name, held)
            .outerjoin(by, by.id == Project.deleted_by)
            .where(condition, Project.deleted_at.is_not(None))
            .order_by(Project.deleted_at.desc(), Project.id.desc())
        )
    ]


def purge_trash(connection: Connection) -> Emptied:
    """Delete for good everything that has been in the trash for TRASH_DAYS, in the caller's
    transaction, which lifts the role's timeouts (the housekeeping round); the caller discards
    the files once it has committed."""
    days = {"days": TRASH_DAYS}
    projects = connection.execute(_PURGE_PROJECTS, days).all()
    keys = connection.scalars(_PURGE_DOCUMENTS, days).all()
    folders = connection.execute(_PURGE_FOLDERS, days).rowcount
    chats = connection.execute(_PURGE_CHATS, days).rowcount
    return Emptied(
        projects=len(projects),
        documents=len(keys),
        folders=folders,
        chats=chats,
        prefixes=[project_prefix(org_id, project_id) for org_id, project_id in projects]
        + [key.rpartition("/")[0] + "/" for key in keys],
    )
