"""Members' checks of the facts the enrich stage read (indexing-approach.md, "Extraction
templates"): a member says a fact was read right, or writes what the document says in its place.

A check is a row of `fact_checks`, never a column of the fact, since the facts are written anew at
every run of the stage: it is kept by the document, the field's name and what the model read, and
`apply_checks` gives it again to the fact of a new run that reads the same, or reads the member's
value. A fact holding a check carries the member's value in its own columns (`value`, `number`,
`date`, `currency`), so that the facts table sorts and filters by it and the agent and the export
read it; the check keeps the model's reading, which taking the check back restores. A check no
fact reads any more stays, for its cell to say so, until a member removes it.
"""

import datetime as dt
import math
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import MIGRATION_LOCK
from app.models import Document, Fact, FactCheck
from app.parsing.types import clean_text
from app.schemas import FactCheckOut, FactOut

# The longest value a member may write, as long as the stage keeps one.
MAX_VALUE_CHARS = 2000
# The advisory lock a document's checks are written under, its id the second key: by a member,
# and by the enrich stage writing the document's facts, so that a check taken back while a run
# writes the facts finds the fact the run gave it, and a run reads every check made before.
CHECKS_LOCK = MIGRATION_LOCK + 5


# A fact or a check asked for by an id that is not there: a run of the enrich stage writes a
# document's facts anew, so an id the page read before it is gone.
FACT_GONE = (
    "This fact is not there any more: its document was read again, or deleted, since it was "
    "shown. Load the table again to check the facts as they read now."
)
CHECK_GONE = "This check is not there any more. Load the table again."


class CheckRefused(ValueError):
    """A value the fact's type cannot take, in the sentence the member is shown."""


@dataclass(frozen=True)
class Reading:
    """A fact's value as its columns hold it: its words, and its number, date and currency as its
    type has them."""

    value: str
    number: float | None = None
    date: dt.date | None = None
    currency: str | None = None


def reading_of(fact: Fact) -> Reading:
    return Reading(fact.value, fact.number, fact.date, fact.currency)


def model_reading(check: FactCheck) -> Reading:
    """What the model read of the fact the check is for."""
    return Reading(check.read_value, check.read_number, check.read_date, check.read_currency)


def member_reading(check: FactCheck) -> Reading | None:
    """The member's value, or None for a check of the model's own."""
    if check.value is None:
        return None
    return Reading(check.value, check.number, check.date, check.currency)


def same(a: Reading, b: Reading) -> bool:
    """Whether two readings say the same: by their number, or their date, where both have one,
    since a model writes an amount or a date in more than one way; else by their words, case and
    spacing aside. An amount in another currency is another amount."""
    if a.number is not None and b.number is not None:
        currencies = {a.currency, b.currency} - {None, ""}
        return a.number == b.number and len(currencies) < 2
    if a.date is not None and b.date is not None:
        return a.date == b.date
    return " ".join(a.value.lower().split()) == " ".join(b.value.lower().split())


def written(fact: Fact, value: str, currency: str | None) -> Reading:
    """The value a member wrote, typed as the fact's type takes it: a number or an amount as a
    number (the page's number field sends it as one, never with separators), an amount in the
    currency given or the fact's own, a date as 2026-03-01."""
    value_type = fact.value_type
    words = clean_text(value)
    if not words:
        raise CheckRefused("Write the value the document states, or check the one read.")
    if len(words) > MAX_VALUE_CHARS:
        raise CheckRefused(f"A value is at most {MAX_VALUE_CHARS:,} characters.")
    if value_type == "date":
        try:
            day = dt.date.fromisoformat(words)
        except ValueError:
            raise CheckRefused("Write the date as 2026-03-01.") from None
        return Reading(day.isoformat(), date=day)
    if value_type in ("number", "money"):
        try:
            number = float(words)
        except ValueError:
            number = math.nan
        if not math.isfinite(number):
            raise CheckRefused("Write the number with digits alone, such as 1250.50.")
        if value_type == "number":
            return Reading(_plain(number), number=number)
        code = clean_text(currency or "").upper()[:10] or fact.currency
        shown = _plain(number, cents=True)
        return Reading(f"{shown} {code}" if code else shown, number=number, currency=code)
    return Reading(words)


def _plain(number: float, cents: bool = False) -> str:
    """A number as a member reads it: grouped by thousands, no trailing zeros, an amount with at
    least its cents."""
    exact = Decimal(repr(number)).normalize()
    if cents and exact.as_tuple().exponent > -2:
        exact = exact.quantize(Decimal("0.01"))
    return format(exact, ",f")


def hold(session: Session, document_id: int) -> None:
    """Hold the document's checks and facts for the rest of the transaction (`CHECKS_LOCK`)."""
    session.execute(
        text("SELECT pg_advisory_xact_lock(:key, :document)"),
        {"key": CHECKS_LOCK, "document": document_id},
    )


def check(session: Session, fact: Fact, user_id: int, value: Reading | None) -> str | None:
    """Check the fact as it reads now (`value` None), or as the member wrote it, and say what was
    done: "fact_checked", "fact_corrected", or None when the fact was checked so already. The
    caller holds the document's checks (`hold`) and the fact's row."""
    held = fact.check
    model = model_reading(held) if held is not None else reading_of(fact)
    if value is None:
        if held is not None:
            return None
        member = None
    else:
        # The model's own value written again is a check of it, not a correction.
        member = None if same(value, model) else value
    if held is not None and member_reading(held) == member:
        return None
    if held is None:
        held = FactCheck(
            document_id=fact.document_id,
            name=fact.name,
            read_value=model.value,
            read_number=model.number,
            read_date=model.date,
            read_currency=model.currency,
        )
        session.add(held)
        fact.check = held
    held.value = member.value if member else None
    held.number = member.number if member else None
    held.date = member.date if member else None
    held.currency = member.currency if member else None
    held.checked_by = user_id
    held.checked_at = dt.datetime.now(dt.UTC)
    _show(fact, member or model)
    return "fact_corrected" if member else "fact_checked"


def remove(session: Session, held: FactCheck) -> None:
    """Take the check back: the fact holding it, if any, reads as the model read it again. The
    caller holds the document's checks (`hold`)."""
    fact = session.scalar(
        select(Fact)
        .where(Fact.check_id == held.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if fact is not None:
        _show(fact, model_reading(held))
        fact.check = None
    session.delete(held)


def apply_checks(session: Session, document_id: int, facts: Sequence[Fact]) -> None:
    """Give the document's checks to the facts a run just read, before they are written: each
    check to the first fact of its field that reads what the model read before, or what the
    member wrote, which then shows the member's value. The caller holds the document's checks
    (`hold`) from before it deleted the facts of the run before."""
    checks = list(
        session.scalars(
            select(FactCheck).where(FactCheck.document_id == document_id).order_by(FactCheck.id)
        )
    )
    for fact in facts:
        if not checks:
            return
        reading = reading_of(fact)
        for held in checks:
            member = member_reading(held)
            if held.name == fact.name and (
                same(reading, model_reading(held)) or (member and same(reading, member))
            ):
                fact.check = held
                if member is not None:
                    _show(fact, member)
                checks.remove(held)
                break


def _show(fact: Fact, reading: Reading) -> None:
    fact.value = reading.value
    fact.number = reading.number
    fact.date = reading.date
    fact.currency = reading.currency


def unheld(session: Session, document_ids: Sequence[int], names: Sequence[str]) -> list[FactCheck]:
    """The checks of the documents' fields that no fact holds: checked before the document was
    read again, and read differently since."""
    if not document_ids:
        return []
    return list(
        session.scalars(
            select(FactCheck)
            .where(
                FactCheck.document_id.in_(document_ids),
                FactCheck.name.in_(names),
                ~select(Fact.id).where(Fact.check_id == FactCheck.id).exists(),
            )
            .order_by(FactCheck.document_id, FactCheck.checked_at, FactCheck.id)
        )
    )


def fact_out(fact: Fact, document: Document) -> FactOut:
    """A fact as the pages show it, with its document's name and a member's check."""
    return FactOut(
        id=fact.id,
        document_id=fact.document_id,
        document_title=document.display_title,
        name=fact.name,
        value_type=fact.value_type,
        value=fact.value,
        number=fact.number,
        date=fact.date,
        currency=fact.currency,
        quote=fact.quote,
        page=fact.page,
        block_ordinal=fact.block_ordinal,
        passage_id=fact.passage_id,
        verified=fact.verified,
        check=check_out(fact.check) if fact.check is not None else None,
    )


def check_out(check: FactCheck) -> FactCheckOut:
    return FactCheckOut(
        id=check.id,
        checked_by=check.checker,
        checked_at=check.checked_at,
        corrected_from=check.read_value if check.corrected else None,
        value=check.value if check.value is not None else check.read_value,
    )
