"""A project's smart folders: saved views of its documents, each the documents its filter
(`DocumentFilter` in app/schemas.py) matches wherever they are, which the library lists as it
lists a folder's (`list_documents` in app/api/documents.py), in the order the reader chose.

Everyone who reads the project sees its smart folders, as its folders, and a member who may
change the project makes, changes and deletes them, as they do its folders; like a folder, a
smart folder is a way of reading the library, so its changes are not audited.

A filter names tags and a folder by id. One of them gone (a tag deleted, the folder in the trash
or deleted) leaves the smart folder matching nothing by that part rather than dropping the part,
so that it never lists more than it was saved to: `problem` says why, and its form offers the
way back.
"""

from sqlalchemy import ColumnElement, false, func, select
from sqlalchemy.orm import Session

from app.folders import folder_paths, subtree_ids
from app.models import Document, Folder, SmartFolder, Tag, User
from app.schemas import DocumentFilter, SmartFolderOut

# How many smart folders a project may have.
MAX_SMART_FOLDERS = 50

TOO_MANY = (
    f"A project has at most {MAX_SMART_FOLDERS} smart folders. Delete one before making another."
)
TAGS_GONE = "A tag it lists by was deleted, so it lists nothing by it. Choose its tags again."
FOLDER_GONE = (
    "Its folder is in the trash or was deleted, so it lists nothing. Restore the folder, or "
    "choose another."
)


def matching(session: Session, project_id: int, chosen: DocumentFilter) -> list[ColumnElement]:
    """The conditions on `documents` of the filter, for a query of the project's documents out of
    the trash."""
    conditions: list[ColumnElement] = []
    if chosen.tags:
        held = Document.tag_ids.contains if chosen.every_tag else Document.tag_ids.overlap
        conditions.append(held(chosen.tags))
    if chosen.types:
        conditions.append(Document.doc_type.in_(chosen.types))
    if chosen.after is not None:
        conditions.append(Document.doc_date >= chosen.after)
    if chosen.before is not None:
        conditions.append(Document.doc_date <= chosen.before)
    if chosen.reviews:
        conditions.append(Document.review.in_(chosen.reviews))
    if chosen.folder_id is not None:
        folder = _live_folder(session, project_id, chosen.folder_id)
        conditions.append(
            Document.folder_id.in_(subtree_ids(session, folder)) if folder else false()
        )
    return conditions


def problem_of(session: Session, project_id: int, chosen: DocumentFilter) -> str | None:
    """Why the filter matches nothing by one of its parts as the project stands now, or None."""
    if chosen.folder_id is not None and _live_folder(session, project_id, chosen.folder_id) is None:
        return FOLDER_GONE
    if chosen.tags:
        found = set(
            session.scalars(
                select(Tag.id).where(Tag.project_id == project_id, Tag.id.in_(chosen.tags))
            )
        )
        # A tag gone narrows "every tag" to nothing, and "any tag" only by what it held.
        if (found != set(chosen.tags) and chosen.every_tag) or not found:
            return TAGS_GONE
    return None


def smart_folder_out(session: Session, smart_folder: SmartFolder) -> SmartFolderOut:
    chosen = DocumentFilter.model_validate(smart_folder.filter)
    folder = None
    if chosen.folder_id is not None:
        folder = folder_paths(session, smart_folder.project_id).get(chosen.folder_id)
    made_by = None
    if smart_folder.created_by is not None:
        made_by = session.scalar(select(User.name).where(User.id == smart_folder.created_by))
    return SmartFolderOut(
        id=smart_folder.id,
        name=smart_folder.name,
        filter=chosen,
        folder=folder,
        problem=problem_of(session, smart_folder.project_id, chosen),
        made_by=made_by,
        updated_at=smart_folder.updated_at,
    )


def project_smart_folders(session: Session, project_id: int) -> list[SmartFolder]:
    """The project's smart folders, by name whatever its case."""
    return list(
        session.scalars(
            select(SmartFolder)
            .where(SmartFolder.project_id == project_id)
            .order_by(func.lower(SmartFolder.name), SmartFolder.id)
        )
    )


def _live_folder(session: Session, project_id: int, folder_id: int) -> Folder | None:
    return session.scalar(
        select(Folder).where(
            Folder.id == folder_id, Folder.project_id == project_id, Folder.deleted_at.is_(None)
        )
    )
