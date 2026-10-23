"""Cross-checks: rules that compare the facts of a project's documents with one another
(indexing-approach.md, "Cross-checks"), such as an invoice's prices with the price list's, its
quantities with what its delivery notes say was delivered, its payment terms with the contract's
and its due date with its own issue date.

A rule names a field of one kind's documents, the left side (the documents checked are those
read for that kind's template, `documents.template`), the field it is compared with, the right
side, and which documents the right side's values come from: the same document, the documents of
the right kind that state the same key (an invoice's `po_number` and a delivery note's), or every
document of the right kind (the project's price list). It compares the first value of each side,
or, line by line (`by_item`), each of the left field's values with the right value of each paired
document that names the same item, lines being paired by the words they share, figures aside
(`item_words`, `paired`), since a line's item is written differently on an invoice and a price
list and nothing else says which lines go together.

What a rule finds is worked out from the facts whenever it is asked for, deterministically and
never by the model: the facts are written anew whenever a document is read again, and a finding
kept would go stale with them. A finding names the facts on both sides, so the page cites each,
and a member's correction is in the fact's own columns (app/fact_checks.py), so the comparison
reads it. Its values are what documents say: the page holds them, never the audit log or a log
line.
"""

import base64
import json
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.extraction import Template, TemplateField
from app.models import NATURAL_ORDER, CrossCheck, Document, Fact

# How many rules a project may have; each is worked out whenever its list is shown.
MAX_CROSS_CHECKS = 20
# The widest margin between two dates, in days.
MAX_MARGIN_DAYS = 3660

# The comparisons of numbers, amounts and dates, which a margin widens, and those of texts.
ORDERED = ("equal", "at_most", "at_least")
WORDED = ("same_words", "same_numbers")
NUMERIC = ("number", "money")

# Why a finding is what it is: the comparison met or not, amounts in two currencies, or what is
# missing for it to be made: the checked document's value, its key, a document to pair it with,
# that document's value, a value naming the same item, or a value's number or date.
HOLDS = "holds"
DIFFERS = "differs"
CURRENCY = "currency"
NO_VALUE = "no_value"
NO_KEY = "no_key"
NO_PARTNER = "no_partner"
NO_PARTNER_VALUE = "no_partner_value"
NO_ITEM = "no_item"
UNTYPED = "untyped"
OUTCOME_OF = {
    HOLDS: "holds",
    DIFFERS: "differs",
    CURRENCY: "differs",
    NO_VALUE: "missing",
    NO_KEY: "missing",
    NO_PARTNER: "missing",
    NO_PARTNER_VALUE: "missing",
    NO_ITEM: "missing",
    UNTYPED: "missing",
}
# The outcomes in the order findings are listed: what needs looking at first.
OUTCOMES = ("differs", "missing", "holds")

# How much of their words two lines must share, of all the words either has, to name one item.
MATCH_SHARE = 0.5

BAD_CURSOR = "This list's page is not one it gave. Load it again from the start."

_WORDS = re.compile(r"[^\W_]+")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_PARTS = re.compile(r"(\d+)")
# Words that say how a line is written rather than what it is for, and currencies, which say
# what its figures are in.
_HOW = frozenset({"x", "per", "of", "the", "a", "an", "and", "at", "for", "each", "to", "in", "on"})
_CURRENCIES = frozenset(
    {"eur", "usd", "gbp", "chf", "jpy", "cny", "cad", "aud", "nzd", "sek", "nok", "dkk", "pln"}
    | {"czk", "huf", "inr"}
)


class FindingsRefusal(ValueError):
    """A cursor the findings cannot take, in the sentence the member is shown."""


@dataclass(frozen=True)
class Rule:
    """What a cross-check compares, as its row and the form hold it."""

    left_kind: str
    left_field: str
    right_kind: str
    right_field: str
    pairing: str
    left_key: str | None = None
    right_key: str | None = None
    by_item: bool = False
    comparison: str = "equal"
    margin: float = 0
    summed: bool = False


def rule_of(row: CrossCheck) -> Rule:
    return Rule(
        row.left_kind,
        row.left_field,
        row.right_kind,
        row.right_field,
        row.pairing,
        row.left_key,
        row.right_key,
        row.by_item,
        row.comparison,
        row.margin,
        row.summed,
    )


@dataclass(frozen=True)
class Suggestion:
    """A rule offered to a project whose templates have its fields, which a member adds as is."""

    title: str
    rule: Rule


# The rules offered, the checks of an invoice that accounts payable makes before paying it. Each
# is offered while the project's templates have its fields and it has no rule comparing the same.
SUGGESTED = (
    Suggestion(
        "Prices as on the price list",
        Rule("invoice", "unit_price", "price list", "unit_price", "every", by_item=True),
    ),
    Suggestion(
        "Quantities no more than delivered",
        Rule(
            "invoice",
            "quantity",
            "delivery note",
            "delivered_item",
            "key",
            "po_number",
            "po_number",
            by_item=True,
            comparison="at_most",
            summed=True,
        ),
    ),
    Suggestion(
        "Total within its purchase order",
        Rule(
            "invoice",
            "total_amount",
            "purchase order",
            "total_amount",
            "key",
            "po_number",
            "po_number",
            comparison="at_most",
        ),
    ),
    Suggestion(
        "Payment terms as in the contract",
        Rule(
            "invoice",
            "payment_terms",
            "contract",
            "payment_terms",
            "every",
            comparison="same_numbers",
        ),
    ),
    Suggestion(
        "Due within 30 days of issue",
        Rule(
            "invoice", "due_date", "invoice", "issue_date", "same", comparison="at_most", margin=30
        ),
    ),
)


@dataclass(frozen=True)
class Sides:
    """The templates and fields a rule compares, as the project's templates hold them now."""

    left: Template
    right: Template
    left_field: TemplateField
    right_field: TemplateField

    @property
    def dates(self) -> bool:
        return self.left_field.type == "date"


def sides_of(rule: Rule, templates: Sequence[Template]) -> Sides | str:
    """The templates and fields the rule compares, or, when it cannot be worked out with the
    project's templates as they are, why not, in the sentence the member is shown: a template or
    field gone since the rule was made, or a rule that compares what cannot be compared."""
    by_kind = {template.doc_type: template for template in templates}
    left, right = by_kind.get(rule.left_kind), by_kind.get(rule.right_kind)
    for kind, template in ((rule.left_kind, left), (rule.right_kind, right)):
        if template is None:
            return f"This project has no template for {kind} documents."
    assert left is not None and right is not None
    left_field, right_field = left.field(rule.left_field), right.field(rule.right_field)
    for name, template, found in (
        (rule.left_field, left, left_field),
        (rule.right_field, right, right_field),
    ):
        if found is None:
            return f"The {template.title} template has no field named {name}."
    assert left_field is not None and right_field is not None
    if rule.pairing == "same":
        if rule.right_kind != rule.left_kind:
            return "Within the same document, a field is compared with one of the same template."
        if rule.right_field == rule.left_field:
            return "Within the same document, compare the field with another one."
    if rule.pairing == "key":
        for name, template in ((rule.left_key, left), (rule.right_key, right)):
            if not name:
                return "Name the field of each side whose values pair the documents."
            if template.field(name) is None:
                return f"The {template.title} template has no field named {name}."
    types = (left_field.type, right_field.type)
    named = f"{left_field.name} and {right_field.name}"
    if rule.comparison in ORDERED:
        if not (types == ("date", "date") or all(kind in NUMERIC for kind in types)):
            return f"{named} are not both numbers, amounts or dates, which this compares."
    elif types != ("text", "text"):
        return f"{named} are not both texts, which this compares."
    if rule.summed and not all(kind in NUMERIC for kind in types):
        return "Only numbers and amounts are added up."
    if rule.margin:
        if rule.comparison not in ORDERED:
            return "A margin is for numbers, amounts and dates."
        if types[0] == "date" and (
            rule.margin != int(rule.margin) or rule.margin > MAX_MARGIN_DAYS
        ):
            return f"A margin between dates is a whole number of days, at most {MAX_MARGIN_DAYS:,}."
    return Sides(left, right, left_field, right_field)


def suggestions_for(templates: Sequence[Template], held: Sequence[Rule]) -> list[Suggestion]:
    """The suggested rules the project's templates allow and it has none of."""
    return [
        suggestion
        for suggestion in SUGGESTED
        if suggestion.rule not in held and isinstance(sides_of(suggestion.rule, templates), Sides)
    ]


@dataclass(frozen=True)
class Finding:
    """What a rule found for one of the checked documents' values: the document, why it is what
    it is (`reason`, one of OUTCOME_OF's), the value checked, the values it was compared with,
    the documents paired with it, and how far apart the two were (the checked value less the
    other, days for dates)."""

    document: Document
    reason: str
    left: Fact | None = None
    right: tuple[Fact, ...] = ()
    partners: tuple[Document, ...] = ()
    difference: float | None = None
    # The checked value's place among the document's, which keeps a document's lines in order.
    index: int = 0

    @property
    def outcome(self) -> str:
        return OUTCOME_OF[self.reason]


@dataclass(frozen=True)
class Page:
    findings: list[Finding]
    # How many findings there are of each outcome, and how many the page's outcome holds.
    counts: dict[str, int]
    total: int
    next_after: str | None


def counts_of(session: Session, project_id: int, rule: Rule, sides: Sides) -> dict[str, int]:
    found = Counter(finding.outcome for finding in findings_of(session, project_id, rule, sides))
    return {outcome: found[outcome] for outcome in OUTCOMES}


def read_findings(
    session: Session,
    project_id: int,
    rule: Rule,
    sides: Sides,
    *,
    outcome: str | None = None,
    after: str | None = None,
    limit: int = 50,
) -> Page:
    """A page of what the rule finds in the project, those of one outcome or all, differing
    first, past `after`."""
    every = findings_of(session, project_id, rule, sides)
    counts = Counter(finding.outcome for finding in every)
    matching = [finding for finding in every if outcome is None or finding.outcome == outcome]
    kept = matching
    if after is not None:
        past = _cursor(after)
        kept = [finding for finding in matching if _order(finding) > past]
    shown = kept[:limit]
    return Page(
        findings=shown,
        counts={name: counts[name] for name in OUTCOMES},
        total=len(matching),
        next_after=_cursor_of(shown[-1]) if len(kept) > limit else None,
    )


def findings_of(session: Session, project_id: int, rule: Rule, sides: Sides) -> list[Finding]:
    """Every finding of the rule in the project, in the order they are listed."""
    checked = _documents(session, project_id, rule.left_kind)
    left_names = {rule.left_field, *([rule.left_key] if rule.pairing == "key" else [])}
    right_names = {rule.right_field, *([rule.right_key] if rule.pairing == "key" else [])}
    if rule.right_kind == rule.left_kind:
        facts = _facts(session, project_id, rule.left_kind, left_names | right_names)
        others, their_facts = checked, facts
    else:
        facts = _facts(session, project_id, rule.left_kind, left_names)
        others = _documents(session, project_id, rule.right_kind)
        their_facts = _facts(session, project_id, rule.right_kind, right_names)
    keyed: dict[str, list[Document]] = {}
    if rule.pairing == "key":
        for other in others:
            for fact in their_facts.get(other.id, {}).get(rule.right_key or "", []):
                if key := key_of(fact.value):
                    holders = keyed.setdefault(key, [])
                    if other not in holders:
                        holders.append(other)

    findings: list[Finding] = []
    items = Items()
    for document in checked:
        own = facts.get(document.id, {})
        values = own.get(rule.left_field, [])
        if rule.pairing == "same":
            partners = [document]
        elif rule.pairing == "key":
            keys = [key for fact in own.get(rule.left_key or "", []) if (key := key_of(fact.value))]
            if not keys:
                findings.append(Finding(document, NO_KEY, left=values[0] if values else None))
                continue
            partners = []
            for key in keys:
                partners += [o for o in keyed.get(key, []) if o not in partners and o != document]
        else:
            partners = [other for other in others if other != document]
        if not values:
            findings.append(Finding(document, NO_VALUE, partners=tuple(partners)))
            continue
        if not partners:
            findings.append(Finding(document, NO_PARTNER, left=values[0]))
            continue
        theirs = [their_facts.get(partner.id, {}).get(rule.right_field, []) for partner in partners]
        if rule.by_item:
            for index, value in enumerate(values):
                found = [match for facts_of in theirs if (match := paired(value, facts_of, items))]
                findings.append(_compared(document, value, found, partners, rule, sides, index))
        else:
            found = [facts_of[0] for facts_of in theirs if facts_of]
            findings.append(_compared(document, values[0], found, partners, rule, sides, 0))
    return sorted(findings, key=_order)


def _compared(
    document: Document,
    left: Fact,
    right: list[Fact],
    partners: list[Document],
    rule: Rule,
    sides: Sides,
    index: int,
) -> Finding:
    if not right:
        reason = NO_ITEM if rule.by_item else NO_PARTNER_VALUE
        return Finding(document, reason, left, (), tuple(partners), index=index)
    reason, difference = compare(rule, sides, left, right)
    return Finding(document, reason, left, tuple(right), tuple(partners), difference, index)


def compare(
    rule: Rule, sides: Sides, left: Fact, right: Sequence[Fact]
) -> tuple[str, float | None]:
    """Whether the left value meets the rule against each of the right ones, or their sum, and
    how far apart it was from the first it does not meet (or the first): HOLDS, DIFFERS,
    CURRENCY or UNTYPED, and the difference."""
    if rule.comparison == "same_words":
        met = all(_words(left.value) == _words(other.value) for other in right)
        return (HOLDS if met else DIFFERS), None
    if rule.comparison == "same_numbers":
        met = all(same_numbers(left.value, other.value) for other in right)
        return (HOLDS if met else DIFFERS), None
    if sides.dates:
        if left.date is None or any(other.date is None for other in right):
            return UNTYPED, None
        differences = [float((left.date - other.date).days) for other in right]
    else:
        if left.number is None or any(other.number is None for other in right):
            return UNTYPED, None
        currencies = {fact.currency for fact in (left, *right) if fact.currency}
        if len(currencies) > 1:
            return CURRENCY, None
        if rule.summed:
            differences = [left.number - sum(other.number or 0 for other in right)]
        else:
            differences = [left.number - (other.number or 0) for other in right]
    differences = [round(difference, 6) for difference in differences]
    unmet = [d for d in differences if not _meets(rule.comparison, d, rule.margin)]
    return (DIFFERS if unmet else HOLDS), (unmet or differences)[0]


def _meets(comparison: str, difference: float, margin: float) -> bool:
    if comparison == "at_most":
        return difference <= margin
    if comparison == "at_least":
        return difference >= -margin
    return abs(difference) <= margin


def item_words(value: str) -> frozenset[str]:
    """What a line says it is for: its words in lowercase, without its figures, its currency and
    the words that say how it is written ("x", "per")."""
    return frozenset(
        word
        for word in _WORDS.findall(value.lower())
        if not word.isdigit() and word not in _HOW and word not in _CURRENCIES
    )


def paired(value: Fact, candidates: Sequence[Fact], items: "Items | None" = None) -> Fact | None:
    """The candidate naming the same item as the value: the one sharing the largest part of the
    words either has, at least MATCH_SHARE of them, the first of those sharing as much."""
    items = Items() if items is None else items
    words = items[value]
    best, share = None, 0.0
    for candidate in candidates:
        theirs = items[candidate]
        if words.isdisjoint(theirs):
            continue
        if (shared := len(words & theirs) / len(words | theirs)) > share:
            best, share = candidate, shared
    return best if share >= MATCH_SHARE else None


class Items(dict[Fact, frozenset[str]]):
    """The item words of each fact a rule's run reads, worked out once each: a price list's
    lines are compared with every line of every invoice."""

    def __missing__(self, fact: Fact) -> frozenset[str]:
        words = self[fact] = _item(fact)
        return words


def _item(fact: Fact) -> frozenset[str]:
    """What a line is for: its words, or, where a member corrected its figure and wrote nothing
    else, the words the model read of it, which are the document's."""
    words = item_words(fact.value)
    if not words and fact.check is not None:
        return item_words(fact.check.read_value)
    return words


def key_of(value: str) -> str:
    """A key as documents are paired by it: its letters and digits in lowercase, so that
    "PO-7701" and "po 7701" are one."""
    return "".join(_WORDS.findall(value.lower()))


def numbers_in(value: str) -> frozenset[float]:
    """The numbers a text states, each read whatever separates its thousands."""
    return frozenset(_number(written) for written in _NUMBER.findall(value))


def same_numbers(a: str, b: str) -> bool:
    """Whether two texts state the same numbers, so that "30 days" and "within thirty (30) days
    of the invoice date" agree; two that state none say the same words."""
    numbers = numbers_in(a), numbers_in(b)
    if not any(numbers):
        return _words(a) == _words(b)
    return numbers[0] == numbers[1]


def _number(written: str) -> float:
    """A number as written: of its commas and points, the last is the decimal point unless the
    number has only one kind and it is followed by three digits or written more than once, which
    sets thousands apart ("1,250", "1.250.000")."""
    separators = [character for character in written if character in ",."]
    if not separators:
        return float(written)
    last = separators[-1]
    if len(set(separators)) == 1 and (
        len(separators) > 1 or len(written) - written.rindex(last) - 1 == 3
    ):
        return float(written.replace(last, ""))
    whole, _, fraction = written.rpartition(last)
    return float(f"{whole.replace(',', '').replace('.', '')}.{fraction}")


def _words(value: str) -> str:
    return " ".join(value.lower().split())


def _documents(session: Session, project_id: int, kind: str) -> list[Document]:
    """The project's documents read for the kind's template, out of the trash, by name."""
    return list(
        session.scalars(
            select(Document)
            .where(
                Document.project_id == project_id,
                Document.template == kind,
                Document.deleted_at.is_(None),
            )
            .order_by(
                func.coalesce(Document.title, Document.filename).collate(NATURAL_ORDER), Document.id
            )
        )
    )


def _facts(
    session: Session, project_id: int, kind: str, names: set[str]
) -> dict[int, dict[str, list[Fact]]]:
    """The facts of the names of the project's documents read for the kind's template, out of
    the trash, by document and name, in each document's order."""
    held: dict[int, dict[str, list[Fact]]] = {}
    for fact in session.scalars(
        select(Fact)
        .join(Document, Document.id == Fact.document_id)
        .where(
            Fact.project_id == project_id,
            Fact.name.in_(names),
            Document.project_id == project_id,
            Document.template == kind,
            Document.deleted_at.is_(None),
        )
        .order_by(Fact.document_id, Fact.block_ordinal.asc().nulls_last(), Fact.id)
    ):
        held.setdefault(fact.document_id, {}).setdefault(fact.name, []).append(fact)
    return held


def _natural(name: str) -> tuple:
    """A name as the list orders it: its numbers as numbers, the rest without case."""
    return tuple(
        (0, int(part), "") if part.isdigit() else (1, 0, part.casefold())
        for part in _PARTS.split(name)
        if part
    )


def _order(finding: Finding) -> tuple:
    """Where a finding is listed: by its outcome, then its document's name and id, then the
    place of its value in the document."""
    rank = OUTCOMES.index(finding.outcome)
    return (rank, _natural(finding.document.display_title), finding.document.id, finding.index)


def _cursor_of(finding: Finding) -> str:
    rank = OUTCOMES.index(finding.outcome)
    title, document_id = finding.document.display_title, finding.document.id
    written = json.dumps([rank, title, document_id, finding.index])
    return base64.urlsafe_b64encode(written.encode()).decode().rstrip("=")


def _cursor(after: str) -> tuple:
    try:
        rank, name, document_id, index = json.loads(
            base64.urlsafe_b64decode(after + "=" * (-len(after) % 4))
        )
    except (ValueError, TypeError):
        raise FindingsRefusal(BAD_CURSOR) from None
    if not (
        type(rank) is int
        and 0 <= rank < len(OUTCOMES)
        and isinstance(name, str)
        and type(document_id) is int
        and type(index) is int
    ):
        raise FindingsRefusal(BAD_CURSOR)
    return (rank, _natural(name), document_id, index)
