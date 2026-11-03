"""A document's details as its members set them: its title, its kind, its date and its language.

Indexing reads all four (the parse stage the file's own title and the language of its words, the
structure stage its opening heading, the enrich stage the model's reading), and whoever may change
the project may set any of them in its place (`PATCH /api/documents/{id}/details`). What
everything reads, the library and its date order, the search's filter, filing rules, search hits
and the agent's catalogue, is the value in force in `documents.title`, `doc_type`, `doc_date` and
`language`; `read_title`, `read_doc_type`, `read_doc_date` and `read_language` keep what the last
reading found, and `edited_details` names the fields a member set, which every later reading
leaves as they are, "Index again" and a new version alike. A member takes the reading's again by
naming the field, which then follows each reading as it did before.

A document's outline paths start with its title (app/indexing/structure.py), and its passages are
searched with their path, so a title changed starts them with the new one (`retitle`). Its
passages are stemmed in its language, null for its organisation's, so a language changed moves
them by the organisation's language job (app/languages.py), which a long document's request
could not wait for. A change of a detail is not audited, as a rename is not.
"""

from pathlib import Path

from sqlalchemy import func, literal, select, update
from sqlalchemy.orm import Session

from app.db import unbounded
from app.indexing.structure import PATH_SEPARATOR
from app.languages import queue_language
from app.models import DOCUMENT_DETAILS, Document, Passage, Section
from app.parsing.types import clean_text

# The column of what the reading found beside each detail.
READ_COLUMNS = {
    "title": "read_title",
    "doc_type": "read_doc_type",
    "doc_date": "read_doc_date",
    "language": "read_language",
}
assert tuple(READ_COLUMNS) == DOCUMENT_DETAILS


def hold_details(session: Session, document: Document) -> None:
    """Read the document's details again under a lock on its row, so that a member's change while
    it was being read holds and is not written back over by what this session read before; the
    caller then changes them and commits, which a member's change waits for."""
    session.refresh(document, ["edited_details", *DOCUMENT_DETAILS], with_for_update=True)


def settle_details(document: Document) -> None:
    """Give each detail no member set what the reading found."""
    for name, read in READ_COLUMNS.items():
        if name not in document.edited_details:
            setattr(document, name, getattr(document, read))


def root_of(document: Document) -> str:
    """What the document's outline paths start with: its title, else its file's name without the
    extension, as the structure stage begins them."""
    return document.title or Path(document.filename).stem


def retitle(session: Session, document: Document) -> None:
    """Start the paths of the document's outline, and the passages searched with them, with its
    title as it is now (`root_of`). What they start with is read from the first section's own
    path rather than from the title before, since a file renamed since its reading changed that
    without changing them."""
    first = session.execute(
        select(Section.path, Section.title)
        .where(Section.document_id == document.id, Section.parent_id.is_(None))
        .order_by(Section.ordinal)
        .limit(1)
    ).first()
    if first is None:
        return
    own = PATH_SEPARATOR + first.title
    if not first.path.endswith(own):
        # A title longer than a section keeps: its path cannot be split where it begins.
        return
    old = first.path[: -len(own)]
    root = root_of(document)
    if old == root:
        return
    # Work as long as the document's outline is, which a long one's request may take.
    unbounded(session)
    for model, column in ((Section, Section.path), (Passage, Passage.context)):
        session.execute(
            update(model)
            .where(
                model.document_id == document.id,
                func.starts_with(column, old + PATH_SEPARATOR),
            )
            .values({column: literal(root) + func.substr(column, len(old) + 1)})
            .execution_options(synchronize_session=False)
        )


def edit_details(
    session: Session, document: Document, given: dict[str, object], as_read: set[str]
) -> None:
    """Set the details `given` as a member's, each None for none (no title shows the file's name,
    no language follows the organisation's), and take the reading's again for those `as_read`;
    the caller commits."""
    hold_details(session, document)
    language = document.language
    edited = set(document.edited_details)
    for name, value in given.items():
        edited.add(name)
        setattr(document, name, _cleaned(name, value))
    edited -= as_read
    document.edited_details = sorted(edited)
    settle_details(document)
    retitle(session, document)
    if document.language != language:
        queue_language(session, document.project.org_id)


def _cleaned(name: str, value: object) -> object:
    """The member's value as indexing would have written it: a title and a kind on one line, the
    kind in lower case as the search's filter and filing rules compare it."""
    if not isinstance(value, str):
        return value
    text = " ".join(clean_text(value).split())
    if name == "doc_type":
        return text.lower()[:100] or None
    return text[:500] or None
