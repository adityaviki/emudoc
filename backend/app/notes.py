"""A document's note: one text its project's members share.

Whoever may change the project writes it and everyone who reads the document reads it, with the
name of whoever changed it last and when (`GET|PUT /api/documents/{id}/note`, app/api/notes.py).
It is the organisation's, as the document is: it goes with the document into the trash and out
of it, and with its purge, travels in the project's export (app/export.py), and outlives its
writer's account without their name. Neither the agent nor the search reads it, and a change of
it is not audited, as a rename is not. Of two members saving at once the last one's stays, and
the page names who saved it.
"""

from sqlalchemy import delete, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import Document, DocumentNote


def write_note(
    session: Session, document: Document, text: str, user_id: int
) -> DocumentNote | None:
    """Save the document's note as the member wrote it, or delete it when it is blank, and return
    what it holds now; the caller commits."""
    # Postgres refuses a NUL in text; the note's own lines and spaces are the member's.
    text = text.replace("\x00", "").strip()
    if not text:
        session.execute(delete(DocumentNote).where(DocumentNote.document_id == document.id))
        return None
    written = {"text": text, "changed_by": user_id, "changed_at": func.now()}
    session.execute(
        insert(DocumentNote)
        .values(document_id=document.id, **written)
        .on_conflict_do_update(index_elements=[DocumentNote.document_id], set_=written)
    )
    return session.get(DocumentNote, document.id, populate_existing=True)
