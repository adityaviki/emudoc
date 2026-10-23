"""The facts table: a project's documents of one kind side by side, one row per document read for
the kind's template (app/extraction.py) and one column per field, which the library's facts
view shows (app/api/templates.py).

The columns are the template in force now, so a document read for an earlier version of it shows
the facts of the names the template still has. A field may hold several facts, each line of an
invoice for one: its cell lists them in the document's order, a row is sorted by the first, the
one the cell shows first, and a condition holds for a row when any of them meets it.

A row is sorted by the first fact's typed value, a number or an amount by its number (whatever
its currency), a date by its date and a text by its words in the library's natural order; a row
without the value goes last whichever way. Pages are keyset pages over the sort's value, the
document's name and its id, all three in the cursor, so that a row added or removed between two
pages moves none.
"""

import base64
import datetime as dt
import json
import operator
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import (
    ColumnElement,
    Lateral,
    and_,
    exists,
    func,
    literal,
    or_,
    select,
    text,
    true,
    tuple_,
)
from sqlalchemy.orm import Session

from app.extraction import Template, TemplateField
from app.models import NATURAL_ORDER, Document, Fact

Order = Literal["asc", "desc"]

# What a condition on a field asks of a row, in the words of the `filter` parameter
# (`field:op` or `field:op:value`): any of its facts at all, none, one whose written value holds
# the words, or one whose number or date is at least or at most the value.
OPERATORS = ("has", "lacks", "contains", "min", "max")
COMPARED = ("number", "money", "date")

BAD_CURSOR = "The page asked for does not follow from this table's order. Load the table again."


class TableRefusal(ValueError):
    """A sort, condition or cursor the table cannot take, in the sentence the member is shown."""


@dataclass(frozen=True)
class Condition:
    """A condition on one field of the template: `op` is one of OPERATORS, `value` its words, a
    number or a date as the field's type takes it."""

    field: TemplateField
    op: str
    value: str | float | dt.date | None = None


@dataclass(frozen=True)
class Row:
    """A row of the table: the document, and the facts of each field it has, in its order."""

    document: Document
    cells: dict[str, list[Fact]]


@dataclass(frozen=True)
class Table:
    rows: list[Row]
    # How many rows the conditions keep, over every page.
    total: int
    next_after: str | None


def condition_of(template: Template, written: str) -> Condition:
    """The condition `field:op[:value]` names, checked against the template's fields."""
    name, _, rest = written.partition(":")
    op, _, value = rest.partition(":")
    field = template.field(name)
    if field is None:
        raise TableRefusal(f"The {template.title} template has no field named {name}.")
    if op not in OPERATORS:
        raise TableRefusal(f"A condition on {name} is one of {', '.join(OPERATORS)}, not “{op}”.")
    if op in ("has", "lacks"):
        return Condition(field, op)
    if op == "contains":
        if not value.strip():
            raise TableRefusal(f"Give the words that {name} should contain.")
        return Condition(field, op, value.strip())
    if field.type not in COMPARED:
        raise TableRefusal(
            f"{name} is a text: only a number, an amount or a date is compared with a value."
        )
    return Condition(field, op, _typed(field, value))


def _typed(field: TemplateField, value: str) -> float | dt.date:
    try:
        if field.type == "date":
            return dt.date.fromisoformat(value.strip())
        number = float(value.strip())
    except ValueError:
        kind = "a date written as 2026-03-01" if field.type == "date" else "a number"
        raise TableRefusal(f"Compare {field.name} with {kind}.") from None
    if number != number or number in (float("inf"), float("-inf")):
        raise TableRefusal(f"Compare {field.name} with a number.")
    return number


def read_table(
    session: Session,
    project_id: int,
    template: Template,
    *,
    sort: TemplateField | None = None,
    order: Order = "asc",
    conditions: Sequence[Condition] = (),
    words: str = "",
    after: str | None = None,
    limit: int = 100,
) -> Table:
    """A page of the table of the project's documents read for `template`, sorted by `sort`
    (None for the document's name), with the conditions and the words held, past `after`."""
    # Each statement is planned for this project, as a search is (app/search.py): whether the
    # facts are better read document by document or the project's at once depends on how many
    # it has. And none is compiled: a sort's value looked up for each of thousands of documents
    # costs the planner enough to start the JIT, which took 330 of the 385 ms a page of 5,000
    # invoices took, whose rows came in 55.
    session.execute(text("SET LOCAL plan_cache_mode = force_custom_plan"))
    session.execute(text("SET LOCAL jit = off"))
    name = func.coalesce(Document.title, Document.filename).collate(NATURAL_ORDER)
    held = [
        Document.project_id == project_id,
        Document.template == template.doc_type,
        Document.deleted_at.is_(None),
        *(_holds(project_id, condition) for condition in conditions),
    ]
    if words.strip():
        held.append(_mentions(project_id, template, words.strip()))
    if sort is None:
        ranked = select(Document.id, name.label("key"), name.label("name")).where(*held)
    else:
        # The sort's value once a document, beside it: as a column of the row rather than an
        # expression, which the cursor's comparisons and the order would look up thrice more.
        first = _first_value(project_id, sort)
        ranked = (
            select(Document.id, first.c.key, name.label("name"))
            .outerjoin(first, true())
            .where(*held)
        )
    ranked = ranked.subquery()

    total = session.scalar(select(func.count()).select_from(ranked)) or 0
    page = select(ranked.c.id, ranked.c.key, ranked.c.name)
    if after is not None:
        page = page.where(_past(ranked, _cursor(after, sort), order))
    descending = order == "desc"
    page = page.order_by(
        (ranked.c.key.desc() if descending else ranked.c.key.asc()).nulls_last(),
        ranked.c.name.desc() if descending else ranked.c.name.asc(),
        ranked.c.id.desc() if descending else ranked.c.id.asc(),
    ).limit(limit + 1)
    found = session.execute(page).all()
    shown = found[:limit]

    ids = [row.id for row in shown]
    documents = {
        document.id: document
        for document in session.scalars(select(Document).where(Document.id.in_(ids)))
    }
    cells: dict[int, dict[str, list[Fact]]] = {document_id: {} for document_id in ids}
    for fact in session.scalars(
        select(Fact)
        .where(
            Fact.document_id.in_(ids),
            Fact.name.in_([field.name for field in template.fields]),
        )
        .order_by(Fact.document_id, Fact.block_ordinal.asc().nulls_last(), Fact.id)
    ):
        cells[fact.document_id].setdefault(fact.name, []).append(fact)

    last = shown[-1] if shown else None
    return Table(
        rows=[Row(documents[document_id], cells[document_id]) for document_id in ids],
        total=total,
        next_after=_cursor_of(last.key, last.name, last.id)
        if last is not None and len(found) > limit
        else None,
    )


def _first_value(project_id: int, field: TemplateField) -> Lateral:
    """The typed value of the document's first fact of the field, as the cell shows it first."""
    column = (
        Fact.date
        if field.type == "date"
        else Fact.number
        if field.type in ("number", "money")
        else Fact.value.collate(NATURAL_ORDER)
    )
    return (
        select(column.label("key"))
        .where(*_of(project_id, field.name))
        .order_by(Fact.block_ordinal.asc().nulls_last(), Fact.id)
        .limit(1)
        .lateral("first")
    )


def _of(project_id: int, *names: str) -> list[ColumnElement[bool]]:
    """The document's facts of the names. The project's too, which says nothing more of a
    document's facts but lets the planner read the project's at once, by their index, rather
    than every project's."""
    return [
        Fact.document_id == Document.id,
        Fact.project_id == project_id,
        Fact.name == names[0] if len(names) == 1 else Fact.name.in_(names),
    ]


def _holds(project_id: int, condition: Condition) -> ColumnElement[bool]:
    of_field = _of(project_id, condition.field.name)
    if condition.op == "lacks":
        return ~exists().where(*of_field)
    if condition.op == "contains":
        of_field.append(Fact.value.icontains(str(condition.value), autoescape=True))
    elif condition.op in ("min", "max"):
        column = Fact.date if condition.field.type == "date" else Fact.number
        of_field.append(
            column >= condition.value if condition.op == "min" else column <= condition.value
        )
    return exists().where(*of_field)


def _mentions(project_id: int, template: Template, words: str) -> ColumnElement[bool]:
    """Whether the document's name or any of its facts of the template's fields holds the
    words."""
    return or_(
        Document.filename.icontains(words, autoescape=True),
        Document.title.icontains(words, autoescape=True),
        exists().where(
            *_of(project_id, *(field.name for field in template.fields)),
            Fact.value.icontains(words, autoescape=True),
        ),
    )


def _past(ranked, cursor: tuple[object, str, int], order: Order) -> ColumnElement[bool]:
    """The rows after the cursor's in the table's order, a row without a value going last."""
    value, name, document_id = cursor
    later = operator.lt if order == "desc" else operator.gt
    tied = later(tuple_(ranked.c.name, ranked.c.id), tuple_(literal(name), literal(document_id)))
    if value is None:
        return and_(ranked.c.key.is_(None), tied)
    return or_(
        later(ranked.c.key, value),
        and_(ranked.c.key == value, tied),
        ranked.c.key.is_(None),
    )


def _cursor_of(value: object, name: str, document_id: int) -> str:
    if isinstance(value, dt.date):
        value = value.isoformat()
    encoded = json.dumps([value, name, document_id]).encode()
    return base64.urlsafe_b64encode(encoded).decode().rstrip("=")


def _cursor(after: str, sort: TemplateField | None) -> tuple[object, str, int]:
    """The value, name and id an `after` carries, the value of the sort's type; anything else
    is refused rather than guessed at."""
    try:
        value, name, document_id = json.loads(
            base64.urlsafe_b64decode(after + "=" * (-len(after) % 4))
        )
    except (ValueError, TypeError):
        raise TableRefusal(BAD_CURSOR) from None
    if not isinstance(name, str) or type(document_id) is not int or not 0 < document_id < 2**31:
        raise TableRefusal(BAD_CURSOR)
    if value is None and sort is not None:
        return None, name, document_id
    kind = "text" if sort is None else sort.type
    try:
        if kind == "date":
            return dt.date.fromisoformat(value), name, document_id
        if kind in ("number", "money"):
            if type(value) not in (int, float):
                raise TypeError
            return float(value), name, document_id
        if not isinstance(value, str):
            raise TypeError
        return value, name, document_id
    except (ValueError, TypeError):
        raise TableRefusal(BAD_CURSOR) from None
