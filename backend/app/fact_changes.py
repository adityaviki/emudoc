"""What a document's last reading changed (indexing-approach.md, "Extraction templates"): when the
enrich stage reads a document it has read before, as it does once a template is changed and its
documents are read again, the facts of the template's fields are compared with those the reading
before left, and what changed is kept for the page to show.

A field changed when a value read before is not read now, or a value read now was not before,
the two compared as a member's check compares them (`same` in app/fact_checks.py), so that
"EUR 1,250.00" and "1,250.00 EUR" are one amount. The facts before are those the members saw: a
corrected fact shows the member's value, which the new reading keeps where it reads as the check
was made (`apply_checks`); a member's value gone is a check the new reading disagrees with, which
the facts table shows in its cell to settle, and the changes name first.

One row a document (`FactChange`), written anew at each reading that had one before it, which
the structure stage and a quarantine drop with the facts, since the facts they delete are the
reading the row compared against. Its values are what the document says, as its facts are: the
members' tables and the page hold them, never the audit log or a log line.
"""

import base64
import datetime as dt
import json
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import ColumnElement, and_, delete, func, or_, select, text, tuple_
from sqlalchemy.orm import Session

from app.extraction import Template
from app.fact_checks import Reading, reading_of, same
from app.models import Document, Fact, FactChange

# How many of a field's values gone and new a change keeps of each, and how much of each value:
# a field such as `line_item` holds hundreds, and the page names a few and counts the rest.
MAX_VALUES = 5
VALUE_CHARS = 300

BAD_CURSOR = "This list's page is not one it gave. Load it again from the start."


class ChangesRefusal(ValueError):
    """A cursor the list cannot take, in the sentence the member is shown."""


@dataclass(frozen=True)
class Read:
    """A fact as a reading left it: its field, its value as shown, and whether a member's check
    is on it."""

    name: str
    reading: Reading
    checked: bool = False


def facts_read(session: Session, document_id: int) -> list[Read]:
    """The document's facts as they stand, in the order they were read: what the reading about
    to replace them is compared with. The caller holds the document's checks (`hold` in
    app/fact_checks.py), so that no check is made or taken back meanwhile."""
    rows = session.execute(
        select(Fact.name, Fact.value, Fact.number, Fact.date, Fact.currency, Fact.check_id)
        .where(Fact.document_id == document_id)
        .order_by(Fact.id)
    ).all()
    return [
        Read(
            row.name,
            Reading(row.value, row.number, row.date, row.currency),
            row.check_id is not None,
        )
        for row in rows
    ]


def read_now(facts: Sequence[Fact]) -> list[Read]:
    """The facts a reading just made, once the checks went to those reading the same."""
    return [Read(fact.name, reading_of(fact), fact.check is not None) for fact in facts]


def changes_of(before: Sequence[Read], after: Sequence[Read], names: Sequence[str]) -> list[dict]:
    """For each field named, in their order, the values read before and no longer, and those
    read now and not before; a field read alike is left out, and those whose values gone held a
    member's check come first."""
    changes: list[dict] = []
    for name in names:
        gone = [read for read in before if read.name == name]
        new: list[Read] = []
        for read in after:
            if read.name != name:
                continue
            match = next((old for old in gone if same(old.reading, read.reading)), None)
            if match is None:
                new.append(read)
            else:
                gone.remove(match)
        if gone or new:
            changes.append(
                {
                    "name": name,
                    "before": [_shown(read) for read in gone[:MAX_VALUES]],
                    "after": [_shown(read) for read in new[:MAX_VALUES]],
                    "before_count": len(gone),
                    "after_count": len(new),
                    "checked": any(read.checked for read in gone),
                }
            )
    return sorted(changes, key=lambda change: not change["checked"])


def _shown(read: Read) -> str:
    value = read.reading.value
    return value if len(value) <= VALUE_CHARS else f"{value[: VALUE_CHARS - 1]}…"


def record(
    session: Session,
    document: Document,
    before: Sequence[Read],
    was: str | None,
    after: Sequence[Read],
    template: Template | None,
    templates: Sequence[Template],
) -> None:
    """Keep what the document's reading changed, in place of what its last one did: the fields
    of the template it is read for now, and of the one it was read for before if another (as
    that kind's template stands now), compared with the facts before (`facts_read`). Nothing
    is kept for a first reading, for one after the structure stage deleted the facts, or for a
    document read for no template either time, which have no fields to compare."""
    session.execute(delete(FactChange).where(FactChange.document_id == document.id))
    now = template.doc_type if template is not None else None
    if was is None and (now is None or not before):
        return
    names = [field.name for field in template.fields] if template is not None else []
    if was is not None and was != now:
        earlier = next((t for t in templates if t.doc_type == was), None)
        if earlier is not None:
            names += [field.name for field in earlier.fields if field.name not in names]
    changes = changes_of(before, after, names)
    session.add(
        FactChange(
            document_id=document.id,
            project_id=document.project_id,
            template_before=was,
            template_after=now,
            changes=changes,
            changed=bool(changes) or was != now,
            checks_unheld=sum(1 for change in changes if change["checked"]),
        )
    )


def of_kind(kind: str) -> ColumnElement[bool]:
    """A document of the kind: read for its template, or read for none and found to be of it,
    as documents read before the template was made are. Reading a template's documents again
    takes both (app/api/templates.py)."""
    return or_(
        Document.template == kind,
        and_(Document.template.is_(None), Document.doc_type == kind),
    )


def being_read() -> ColumnElement[bool]:
    """A document the enrich stage is to read, or reads now: queued to be read again, or
    waiting for a retry of the stage. One whose indexing failed before it is waits for nothing."""
    enrich = Document.stages["enrich"]
    return and_(
        Document.status.not_in(("failed", "quarantined")),
        or_(
            enrich["status"].astext.in_(("pending", "running")),
            enrich["retry_at"].is_not(None),
        ),
    )


def _concerning(kind: str) -> ColumnElement[bool]:
    return or_(FactChange.template_after == kind, FactChange.template_before == kind)


@dataclass(frozen=True)
class Readings:
    """The last readings of a project's documents of one kind: how many there are and changed
    something, how many changed a value a member checked, and a page of those that changed."""

    read: int
    changed: int
    unheld: int
    rows: list[tuple[FactChange, Document]]
    next_after: str | None


def read_changes(
    session: Session, project_id: int, kind: str, after: str | None, limit: int
) -> Readings:
    """The project's documents out of the trash whose last reading was of the kind, before or
    now: those that changed something, the ones whose new reading disagrees with a member's check
    first, then the newest reading first, a page of `limit` going on from `after`."""
    concerning = (
        select(FactChange, Document)
        .join(Document, Document.id == FactChange.document_id)
        .where(
            FactChange.project_id == project_id,
            Document.deleted_at.is_(None),
            _concerning(kind),
        )
    )
    counted = concerning.with_only_columns(
        func.count(),
        func.count().filter(FactChange.changed),
        func.count().filter(FactChange.checks_unheld > 0),
    )
    read, changed, unheld = session.execute(counted).one()
    unheld_first = FactChange.checks_unheld > 0
    page = concerning.where(FactChange.changed)
    if after:
        place = tuple_(unheld_first, FactChange.read_at, FactChange.document_id)
        page = page.where(place < tuple_(*_cursor(after)))
    found = session.execute(
        page.order_by(
            unheld_first.desc(), FactChange.read_at.desc(), FactChange.document_id.desc()
        ).limit(limit + 1)
    ).all()
    rows = [(change, document) for change, document in found[:limit]]
    last = rows[-1][0] if rows and len(found) > limit else None
    return Readings(
        read=read,
        changed=changed,
        unheld=unheld,
        rows=rows,
        next_after=_cursor_of(last) if last is not None else None,
    )


def changed_by_kind(session: Session, project_id: int) -> dict[str, int]:
    """How many of the project's documents out of the trash had a last reading that changed
    something, by each kind it was of, before or now."""
    return dict(
        session.execute(
            text(
                """
                SELECT kind, count(*)
                FROM fact_changes AS change
                JOIN documents AS document ON document.id = change.document_id
                CROSS JOIN LATERAL (
                    SELECT DISTINCT kind
                    FROM unnest(ARRAY[change.template_before, change.template_after]) AS kind
                ) AS kinds
                WHERE change.project_id = :project
                  AND change.changed
                  AND document.deleted_at IS NULL
                  AND kind IS NOT NULL
                GROUP BY kind
                """
            ),
            {"project": project_id},
        ).all()
    )


def _cursor_of(change: FactChange) -> str:
    encoded = json.dumps(
        [change.checks_unheld > 0, change.read_at.isoformat(), change.document_id]
    ).encode()
    return base64.urlsafe_b64encode(encoded).decode().rstrip("=")


def _cursor(after: str) -> tuple[bool, dt.datetime, int]:
    try:
        unheld, read_at, document_id = json.loads(
            base64.urlsafe_b64decode(after + "=" * (-len(after) % 4))
        )
        moment = dt.datetime.fromisoformat(read_at)
    except (ValueError, TypeError):
        raise ChangesRefusal(BAD_CURSOR) from None
    if (
        type(unheld) is not bool
        or moment.tzinfo is None
        or type(document_id) is not int
        or not 0 < document_id < 2**31
    ):
        raise ChangesRefusal(BAD_CURSOR)
    return unheld, moment, document_id
