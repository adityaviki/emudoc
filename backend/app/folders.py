"""The folder tree of a project's library, as paths and subtrees, and the names its folders and
files take. Each reads the live tree alone unless it says otherwise: what is in the trash
(app/trash.py) holds no place and no name."""

from collections import defaultdict
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db import MIGRATION_LOCK
from app.models import Document, Folder

PATH_SEPARATOR = "/"
# The longest name a folder takes (`folders.name`, FolderIn in app/schemas.py).
NAME_LENGTH = 200
# How many folders deep below the one it goes into an upload may file a document (`folder_names`):
# deeper than any folder a member drops, and a bound on the folders one request can make.
MAX_DEPTH = 32
# The advisory lock an upload makes a project's folders under, the project's id the second key.
FOLDERS_LOCK = MIGRATION_LOCK + 4


def folder_paths(session: Session, project_id: int, trashed: bool = False) -> dict[int, str]:
    """Each folder's path from the root, like "Invoices/2024"; with `trashed`, the folders in
    the trash too, for saying where something in it was."""
    query = select(Folder).where(Folder.project_id == project_id)
    if not trashed:
        query = query.where(Folder.deleted_at.is_(None))
    folders = {folder.id: folder for folder in session.scalars(query)}
    paths: dict[int, str] = {}

    def path(folder: Folder) -> str:
        if folder.id not in paths:
            parent = folders.get(folder.parent_id) if folder.parent_id else None
            paths[folder.id] = (
                f"{path(parent)}{PATH_SEPARATOR}{folder.name}" if parent else folder.name
            )
        return paths[folder.id]

    for folder in folders.values():
        path(folder)
    return paths


def subtree_ids(session: Session, folder: Folder) -> set[int]:
    """The folder's id and the ids of every live folder below it."""
    children: dict[int | None, list[int]] = defaultdict(list)
    for folder_id, parent_id in session.execute(
        select(Folder.id, Folder.parent_id).where(
            Folder.project_id == folder.project_id, Folder.deleted_at.is_(None)
        )
    ):
        children[parent_id].append(folder_id)
    ids: set[int] = set()
    stack = [folder.id]
    while stack:
        current = stack.pop()
        ids.add(current)
        stack.extend(children[current])
    return ids


def unique_filename(session: Session, project_id: int, folder_id: int | None, name: str) -> str:
    """The name, or "name (2).ext" and so on when a live sibling already carries it."""
    taken = {
        existing.lower()
        for existing in session.scalars(
            select(Document.filename).where(
                Document.project_id == project_id,
                Document.folder_id == folder_id,
                Document.deleted_at.is_(None),
            )
        )
    }
    return _untaken(name, taken, Path(name).stem, Path(name).suffix)


def unique_folder_name(session: Session, project_id: int, parent_id: int | None, name: str) -> str:
    """The name, or "name (2)" and so on when a live sibling folder already carries it."""
    taken = {
        existing.lower()
        for existing in session.scalars(
            select(Folder.name).where(
                Folder.project_id == project_id,
                Folder.parent_id == parent_id,
                Folder.deleted_at.is_(None),
            )
        )
    }
    return _untaken(name, taken, name, "")


def _untaken(name: str, taken: set[str], stem: str, suffix: str) -> str:
    if name.lower() not in taken:
        return name
    for number in range(2, 10_000):
        candidate = f"{stem} ({number}){suffix}"
        if candidate.lower() not in taken:
            return candidate
    return name


def folder_name_taken(
    session: Session,
    project_id: int,
    parent_id: int | None,
    name: str,
    exclude_id: int | None = None,
) -> bool:
    """Whether a live sibling folder carries the name, whatever its case."""
    query = select(Folder.id).where(
        Folder.project_id == project_id,
        Folder.parent_id == parent_id,
        Folder.deleted_at.is_(None),
        func.lower(Folder.name) == name.lower(),
    )
    if exclude_id is not None:
        query = query.where(Folder.id != exclude_id)
    return session.scalar(query) is not None


def folder_names(path: str) -> list[str] | None:
    """The folders a path such as "Invoices/2024" names below the one an upload goes into, from
    the top, each name's spaces tidied as a new folder's are and the empty ones left out; None
    when the path cannot be made: a name that is "." or "..", or longer than NAME_LENGTH, or
    more than MAX_DEPTH of them."""
    names = [" ".join(part.split()) for part in path.split(PATH_SEPARATOR)]
    names = [name for name in names if name]
    if len(names) > MAX_DEPTH:
        return None
    if any(name in (".", "..") or len(name) > NAME_LENGTH for name in names):
        return None
    return names


def folder_at(
    session: Session, project_id: int, parent_id: int | None, names: list[str]
) -> int | None:
    """The live folder that `names` lead to below `parent_id`, each made where no live sibling
    carries its name, whatever its case, so that a folder uploaded again goes into the one it
    made before. A folder is looked for first, then again and made under the project's
    FOLDERS_LOCK, held until the caller's commit, so that two uploads of one folder at once make
    it once."""
    held = False
    for name in names:
        found = _child(session, project_id, parent_id, name)
        if found is None and not held:
            session.execute(
                text("SELECT pg_advisory_xact_lock(:key, :project)"),
                {"key": FOLDERS_LOCK, "project": project_id},
            )
            held = True
            found = _child(session, project_id, parent_id, name)
        if found is None:
            folder = Folder(project_id=project_id, parent_id=parent_id, name=name)
            session.add(folder)
            session.flush()
            found = folder.id
        parent_id = found
    return parent_id


def _child(session: Session, project_id: int, parent_id: int | None, name: str) -> int | None:
    return session.scalar(
        select(Folder.id)
        .where(
            Folder.project_id == project_id,
            Folder.parent_id == parent_id,
            Folder.deleted_at.is_(None),
            func.lower(Folder.name) == name.lower(),
        )
        .order_by(Folder.id)
        .limit(1)
    )
