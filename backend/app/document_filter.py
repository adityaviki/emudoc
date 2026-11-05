"""What narrows the library's search: a project's documents by types, dates, reviews, a folder
and the words they mention (`DocumentFilter` in app/schemas.py), as the conditions on
`documents` that `searched` in app/api/query.py resolves before it ranks a passage, and the
search of every project (app/api/search.py) by the parts that mean the same in any project.

A filter names a folder by id. A folder in the trash or deleted leaves it matching
nothing by that part rather than dropping the part, so that a search never runs wider than the
member asked for.
"""

from sqlalchemy import ColumnElement, and_, false, func, literal, or_, select
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.orm import Session

from app.folders import subtree_ids
from app.models import Document, Folder, Passage
from app.schemas import DocumentFilter

BACKWARDS = "Its earliest date is after its latest. Choose dates the other way round."


def matching(session: Session, project_id: int, chosen: DocumentFilter) -> list[ColumnElement]:
    """The conditions on `documents` of the filter, for a query of the project's documents out of
    the trash."""
    conditions = anywhere(chosen)
    if chosen.reviews:
        conditions.append(Document.review.in_(chosen.reviews))
    if chosen.folder_id is not None:
        folder = _live_folder(session, project_id, chosen.folder_id)
        conditions.append(
            Document.folder_id.in_(subtree_ids(session, folder)) if folder else false()
        )
    if chosen.mentions:
        languages = session.scalars(
            select(Passage.language).where(Passage.project_id == project_id).distinct()
        ).all()
        conditions.extend(_mentioning(project_id, languages, words) for words in chosen.mentions)
    return conditions


def anywhere(chosen: DocumentFilter) -> list[ColumnElement]:
    """The conditions on `documents` of the filter's parts that mean the same in any project, its
    types and its dates, which the search across projects narrows by (app/api/search.py); the
    others name what one project holds, or probe its own languages."""
    conditions: list[ColumnElement] = []
    if chosen.types:
        conditions.append(Document.doc_type.in_(chosen.types))
    if chosen.after is not None:
        conditions.append(Document.doc_date >= chosen.after)
    if chosen.before is not None:
        conditions.append(Document.doc_date <= chosen.before)
    return conditions


def _mentioning(project_id: int, languages: list[str], words: str) -> ColumnElement[bool]:
    """The documents with a passage that holds every one of the words, as a search matches them:
    in the language each passage was indexed with, its text index probed once per language.
    Words too common to be indexed ("the") narrow nothing, since no passage can be told by them."""
    if not languages:
        return false()
    queries = {
        language: func.plainto_tsquery(literal(language, REGCONFIG), words)
        for language in languages
    }
    holding = select(Passage.document_id).where(
        Passage.project_id == project_id,
        or_(
            *(
                and_(Passage.language == literal(language, REGCONFIG), Passage.tsv.op("@@")(query))
                for language, query in queries.items()
            )
        ),
    )
    common = and_(*(func.numnode(query) == 0 for query in queries.values()))
    return or_(common, Document.id.in_(holding))


def _live_folder(session: Session, project_id: int, folder_id: int) -> Folder | None:
    return session.scalar(
        select(Folder).where(
            Folder.id == folder_id, Folder.project_id == project_id, Folder.deleted_at.is_(None)
        )
    )
