"""A file's copies in an organisation's other projects, and the report of them all
(app/duplicates.py)."""

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import AccountDep, DocumentDep, SessionDep
from app.duplicates import PAGE_SIZE, copies_of, duplicates
from app.schemas import DocumentCopiesOut, DuplicatesOut

router = APIRouter(tags=["documents"])


@router.get("/documents/{document_id}/copies", response_model=DocumentCopiesOut)
def document_copies(document: DocumentDep, session: SessionDep, account: AccountDep):
    return DocumentCopiesOut(copies=copies_of(session, account, document))


@router.get("/duplicates", response_model=DuplicatesOut)
def list_duplicates(
    session: SessionDep,
    account: AccountDep,
    after: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = PAGE_SIZE,
):
    """The files held in more than one of the projects the member sees, a page at a time."""
    files, copy_bytes, found, latest = duplicates(session, account, after, limit)
    return DuplicatesOut(files=files, copy_bytes=copy_bytes, duplicates=found, next=latest)
