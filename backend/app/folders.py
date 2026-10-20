"""The folder tree of a project's library, as paths and subtrees, and the names its folders and
files take. Each reads the live tree alone unless it says otherwise: what is in the trash
(app/trash.py) holds no place and no name."""

from collections import defaultdict
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Document, Folder

PATH_SEPARATOR = "/"


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
