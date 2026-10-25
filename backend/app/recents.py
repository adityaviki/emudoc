"""Each member's recent documents: the ones of a project they opened lately, which the library
lists newest first. The page tells the server as the member opens a document in the middle pane
(`PUT /api/documents/{id}/visit`), and a visit replaces the member's earlier one of the same
document, so a row is the last time they opened it.

Only the newest `RECENT` of a member's visits in a project are kept: each new one lets the older
go, so the table holds no history of what a member read beyond what the list shows. A visit goes
with its document and its member's account, and the account export lists the member's.
"""

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import Document, DocumentVisit

# How many of a project's documents a member's recent ones list.
RECENT = 20


def record_visit(session: Session, document: Document, user_id: int) -> None:
    """Note that the member opened the document now, and let their visits of its project past
    the newest RECENT go."""
    visit = insert(DocumentVisit).values(
        document_id=document.id, user_id=user_id, opened_at=func.now()
    )
    session.execute(
        visit.on_conflict_do_update(
            index_elements=[DocumentVisit.document_id, DocumentVisit.user_id],
            set_={"opened_at": visit.excluded.opened_at},
        )
    )
    older = (
        select(DocumentVisit.document_id)
        .join(Document, Document.id == DocumentVisit.document_id)
        .where(DocumentVisit.user_id == user_id, Document.project_id == document.project_id)
        .order_by(DocumentVisit.opened_at.desc(), DocumentVisit.document_id.desc())
        .offset(RECENT)
    )
    session.execute(
        delete(DocumentVisit).where(
            DocumentVisit.user_id == user_id, DocumentVisit.document_id.in_(older)
        )
    )
