"""A project's tags: the labels its members put on its documents, shown on them in the library,
which lists a tag's documents wherever they are, and which smart folders (app/smart_folders.py)
and filing rules (app/filing.py) match and put on documents.

A document holds the ids of its tags on its own row (`Document.tag_ids`), so that tagging it
changes the row whose version the library's pages are tagged by, and so the page of a folder
asked again shows the tag. Every id there is one of the document's project's, which the routes
check as they write; a tag deleted is taken out of every document of its project in the same
transaction. Tags are a way of reading the library, as names and folders are, so making,
changing and deleting them is not audited, as a rename or a move is not.
"""

from collections.abc import Iterable

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import Document, Tag

# How many tags a project may have, and a document carry: more would be a list nobody reads.
MAX_TAGS = 100
MAX_DOCUMENT_TAGS = 20

NAME_TAKEN = "The project has a tag of that name already. Choose another name."
TOO_MANY = f"A project has at most {MAX_TAGS} tags. Delete one before making another."
NOT_THE_PROJECTS = "That tag is not one of this project's. Choose one of its tags."


def project_tags(session: Session, project_id: int) -> list[Tag]:
    """The project's tags, by name whatever its case."""
    return list(
        session.scalars(
            select(Tag).where(Tag.project_id == project_id).order_by(func.lower(Tag.name), Tag.id)
        )
    )


def tag_counts(session: Session, project_id: int) -> dict[int, int]:
    """How many of the project's documents out of the trash carry each tag, by its id; a tag
    no document carries is left out."""
    tag_id = func.unnest(Document.tag_ids).label("tag_id")
    carried = (
        select(tag_id)
        .where(Document.project_id == project_id, Document.deleted_at.is_(None))
        .subquery()
    )
    return dict(
        session.execute(select(carried.c.tag_id, func.count()).group_by(carried.c.tag_id)).all()
    )


def name_taken(session: Session, project_id: int, name: str, exclude_id: int | None = None) -> bool:
    """Whether another tag of the project carries the name, whatever its case."""
    query = select(Tag.id).where(Tag.project_id == project_id, func.lower(Tag.name) == name.lower())
    if exclude_id is not None:
        query = query.where(Tag.id != exclude_id)
    return session.scalar(query) is not None


def the_projects(session: Session, project_id: int, tag_ids: Iterable[int]) -> list[int] | None:
    """The ids, each once in the order given, when every one is a tag of the project's; None
    when one is not, which the caller refuses."""
    wanted = list(dict.fromkeys(tag_ids))
    if not wanted:
        return wanted
    found = set(
        session.scalars(select(Tag.id).where(Tag.project_id == project_id, Tag.id.in_(wanted)))
    )
    return wanted if found == set(wanted) else None


def add_tag(document: Document, tag_id: int) -> bool:
    """Put the tag on the document unless it carries it already; whether it did."""
    if tag_id in document.tag_ids or len(document.tag_ids) >= MAX_DOCUMENT_TAGS:
        return False
    document.tag_ids = [*document.tag_ids, tag_id]
    return True


def delete_tag(session: Session, tag: Tag) -> None:
    """Delete the tag, taking it off every document of its project, those in the trash too, so
    that none is left carrying an id that names nothing. Its filing rules go with it by the
    foreign key; a smart folder that names it then matches nothing by it (app/smart_folders.py)."""
    session.execute(
        update(Document)
        .where(Document.project_id == tag.project_id, Document.tag_ids.contains([tag.id]))
        .values(tag_ids=func.array_remove(Document.tag_ids, tag.id))
        .execution_options(synchronize_session=False)
    )
    session.delete(tag)
