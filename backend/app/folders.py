"""The folder tree of a project's library, as paths and subtrees."""

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Folder

PATH_SEPARATOR = "/"


def folder_paths(session: Session, project_id: int) -> dict[int, str]:
    """Each folder's path from the root, like "Invoices/2024"."""
    folders = {
        folder.id: folder
        for folder in session.scalars(select(Folder).where(Folder.project_id == project_id))
    }
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
    """The folder's id and the ids of every folder below it."""
    children: dict[int | None, list[int]] = defaultdict(list)
    for folder_id, parent_id in session.execute(
        select(Folder.id, Folder.parent_id).where(Folder.project_id == folder.project_id)
    ):
        children[parent_id].append(folder_id)
    ids: set[int] = set()
    stack = [folder.id]
    while stack:
        current = stack.pop()
        ids.add(current)
        stack.extend(children[current])
    return ids
