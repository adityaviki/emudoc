"""Copies of a file across an organisation's projects.

A project holds a file's bytes once (`already_present` in app/uploads.py refuses a second), but
another project may hold them too: the same contract uploaded into two clients' projects, an
invoice mailed into one and scanned into another. A document's copies (`copies_of`) are the
documents of the same SHA-256 in the other projects of its organisation that the member sees
(`visible_to`), which the document tab lists; the duplicates report (`duplicates`) is every file
held in more than one of them, which the projects page leads to. Only the bytes are compared, so
a file saved again and a scan of a printed one are not copies. What is in the trash is left out,
and so are the earlier files of documents given a new version.
"""

from collections.abc import Sequence

from sqlalchemy import Row, func, select, text
from sqlalchemy.orm import Session

from app.access import visible_to
from app.accounts import Account
from app.folders import folder_paths
from app.models import Document, Project
from app.schemas import DocumentCopyOut, DuplicateOut

# How many files a page of the report holds.
PAGE_SIZE = 50
_COPY = (
    Document.id,
    Document.project_id,
    Project.name,
    Document.folder_id,
    Document.filename,
    Document.sha256,
)


def _seen(account: Account):
    """The account's documents out of the trash, in the projects it sees."""
    return (
        select(*_COPY)
        .join(Project, Project.id == Document.project_id)
        .where(visible_to(account), Document.deleted_at.is_(None))
    )


def copies_of(session: Session, account: Account, document: Document) -> list[DocumentCopyOut]:
    """The documents of the same bytes as this one in the other projects the account sees, by
    project name."""
    rows = session.execute(
        _seen(account)
        .where(Document.sha256 == document.sha256, Document.project_id != document.project_id)
        .order_by(Project.name, Project.id)
    ).all()
    return _copies(session, rows)


def duplicates(
    session: Session, account: Account, after: int | None, limit: int = PAGE_SIZE
) -> tuple[int, int, list[DuplicateOut], int | None]:
    """How many files the projects the account sees hold more than once, the bytes their copies
    past the first take, a page of them, the one whose latest copy came last first (after the
    file whose latest copy is `after`), and the cursor of the page after it."""
    # The plan follows how many documents the organisation holds (database.md).
    session.execute(text("SET LOCAL plan_cache_mode = force_custom_plan"))
    seen = (
        select(Document.id, Document.sha256, Document.size_bytes, Document.mime_type)
        .join(Project, Project.id == Document.project_id)
        .where(visible_to(account), Document.deleted_at.is_(None))
        .cte("seen")
    )
    # A project holds a file's bytes once, so each copy is in a project of its own.
    held = (
        select(
            seen.c.sha256,
            func.max(seen.c.id).label("latest"),
            func.count().label("copies"),
            func.max(seen.c.size_bytes).label("size_bytes"),
            func.max(seen.c.mime_type).label("mime_type"),
        )
        .group_by(seen.c.sha256)
        .having(func.count() > 1)
        .cte("held")
    )
    files, copy_bytes = session.execute(
        select(
            func.count(),
            func.coalesce(func.sum(held.c.size_bytes * (held.c.copies - 1)), 0),
        )
    ).one()
    query = select(held.c.sha256, held.c.latest, held.c.size_bytes, held.c.mime_type)
    if after is not None:
        query = query.where(held.c.latest < after)
    page = session.execute(query.order_by(held.c.latest.desc()).limit(limit + 1)).all()
    more = len(page) > limit
    page = page[:limit]
    rows = session.execute(
        _seen(account)
        .where(Document.sha256.in_([file.sha256 for file in page]))
        .order_by(Project.name, Project.id)
    ).all()
    copies = _copies(session, rows)
    by_file: dict[str, list[DocumentCopyOut]] = {}
    for row, copy in zip(rows, copies, strict=True):
        by_file.setdefault(row.sha256, []).append(copy)
    found = [
        DuplicateOut(
            size_bytes=file.size_bytes, mime_type=file.mime_type, copies=by_file[file.sha256]
        )
        for file in page
    ]
    return files, int(copy_bytes), found, page[-1].latest if more else None


def _copies(session: Session, rows: Sequence[Row]) -> list[DocumentCopyOut]:
    """The rows as copies, each with its folder's path in its project."""
    paths = {
        project_id: folder_paths(session, project_id) for project_id in {r.project_id for r in rows}
    }
    return [
        DocumentCopyOut(
            id=row.id,
            project_id=row.project_id,
            project_name=row.name,
            folder=paths[row.project_id].get(row.folder_id, "") if row.folder_id else "",
            folder_id=row.folder_id,
            filename=row.filename,
        )
        for row in rows
    ]
