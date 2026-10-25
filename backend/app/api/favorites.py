"""Each member's own way back to documents: the ones they starred, and the ones they opened
lately (app/recents.py), which the library lists (`list_documents` in app/api/documents.py,
`favorites=` and `recent=`).

Both are the reader's alone, as a chat's pin is: a viewer stars and is remembered too, nobody
else sees them, and nothing here is audited. The account export lists them.
"""

from fastapi import APIRouter
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from app.api.deps import AccountDep, DocumentDep, ProjectDep, SessionDep
from app.models import Document, DocumentFavorite
from app.recents import record_visit
from app.schemas import FavoritesOut

router = APIRouter(tags=["favorites"])


@router.get("/projects/{project_id}/favorites", response_model=FavoritesOut)
def list_favorites(project: ProjectDep, session: SessionDep, account: AccountDep):
    """The ids of the project's documents the reader starred, out of the trash, the newest star
    first: what the library marks with a star."""
    ids = session.scalars(
        select(DocumentFavorite.document_id)
        .join(Document, Document.id == DocumentFavorite.document_id)
        .where(
            DocumentFavorite.user_id == account.user.id,
            Document.project_id == project.id,
            Document.deleted_at.is_(None),
        )
        .order_by(DocumentFavorite.starred_at.desc(), DocumentFavorite.document_id.desc())
    )
    return FavoritesOut(document_ids=list(ids))


@router.put("/documents/{document_id}/favorite", status_code=204)
def star_document(document: DocumentDep, session: SessionDep, account: AccountDep):
    """Star the document for the reader; starring it again keeps its place."""
    session.execute(
        insert(DocumentFavorite)
        .values(document_id=document.id, user_id=account.user.id)
        .on_conflict_do_nothing()
    )
    session.commit()


@router.delete("/documents/{document_id}/favorite", status_code=204)
def unstar_document(document: DocumentDep, session: SessionDep, account: AccountDep):
    """Take the reader's star off the document."""
    session.execute(
        delete(DocumentFavorite).where(
            DocumentFavorite.document_id == document.id,
            DocumentFavorite.user_id == account.user.id,
        )
    )
    session.commit()


@router.put("/documents/{document_id}/visit", status_code=204)
def visit_document(document: DocumentDep, session: SessionDep, account: AccountDep):
    """Note that the reader opened the document now, for their recent documents."""
    record_visit(session, document, account.user.id)
    session.commit()
