"""The documents a member chose together in the library, which its bulk routes act on at once:
indexing them again (app/api/documents.py) and exporting them (app/api/exports.py).

A choice is files alone, at most a page of the library's listing (`PAGE_SIZE` in
app/api/documents.py): the page offers these only when every chosen item is a file, and a
folder's files are chosen by opening it. Each route checks every id before it changes anything,
so a choice made stale by another member's deletion or move to another project is refused whole
rather than half applied.
"""

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Document

MAX_CHOSEN = 100
NOT_CHOSEN = (
    "One of the chosen documents is not in this project any more, or is in the trash. Choose "
    "them again."
)


def chosen_documents(
    session: Session, project_id: int, ids: Iterable[int], *, lock: bool = False
) -> list[Document] | None:
    """The project's documents out of the trash with these ids, each once, in id order, their
    rows locked for the caller's change when `lock`; None when one is not, which the caller
    refuses with `NOT_CHOSEN`."""
    wanted = set(ids)
    query = (
        select(Document)
        .where(
            Document.project_id == project_id,
            Document.deleted_at.is_(None),
            Document.id.in_(wanted),
        )
        .order_by(Document.id)
    )
    if lock:
        # In id order, so two changes of overlapping choices wait for each other rather than
        # deadlock.
        query = query.with_for_update()
    documents = list(session.scalars(query))
    return documents if len(documents) == len(wanted) else None
