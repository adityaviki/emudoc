"""A document's note, which its project's members share (app/notes.py)."""

from fastapi import APIRouter

from app.api.deps import AccountDep, DocumentDep, EditableDocumentDep, SessionDep
from app.models import DocumentNote
from app.notes import write_note
from app.schemas import DocumentNoteIn, DocumentNoteOut

router = APIRouter(tags=["documents"])


@router.get("/documents/{document_id}/note", response_model=DocumentNoteOut)
def get_note(document: DocumentDep, session: SessionDep):
    return _out(session.get(DocumentNote, document.id))


@router.put("/documents/{document_id}/note", response_model=DocumentNoteOut)
def put_note(
    document: EditableDocumentDep, body: DocumentNoteIn, session: SessionDep, account: AccountDep
):
    """Save the document's note whole, a blank one deleting it."""
    out = _out(write_note(session, document, body.text, account.user.id))
    session.commit()
    return out


def _out(note: DocumentNote | None) -> DocumentNoteOut:
    if note is None:
        return DocumentNoteOut(text="", changed_by=None, changed_at=None)
    return DocumentNoteOut(text=note.text, changed_by=note.changer, changed_at=note.changed_at)
