"""Fact-extraction accuracy: how well the indexing model reads the facts the sample's documents
state, against values labelled by hand (`python -m app.maintenance fact-accuracy`).

The sample's contract, three invoices and price list are labelled a field of their kind's
built-in template at a time (app/extraction.py): the values a right reading finds, and none for
a field the document does not state, so that any fact of it is wrong. A field left out of a
document's labels is not scored, where what is right is a matter of reading: the contract's
`price`, whose prices are in a schedule the sample leaves out, or the price list's
`vendor_name`, which only its file's name gives. The delivery log, the email and the meeting's
notes are of no built-in kind, so their facts' names move from run to run, and are not scored
either.

A run indexes the sample's files as an upload is, by the models INDEX_MODEL names with extraction
templates on, in an organisation made for it and deleted after with what it recorded
(`indexed_sample` in app/sample), and scores each field over the documents of its kind: its
recall, the labelled values some fact of the field holds, and its precision, the field's facts
that hold a labelled value. A fact holds a value by what it is rather than how it is put: a
number or an amount by its figure, a date by its day, and words by one of the ways they may be
written, read as the golden questions read an answer (`plain` and `holds` in app/golden.py). A
right reading written in a way the labels do not list fails, and the way joins its value's list;
the value is never dropped for it. Each value is what the document says, which the suite holds to
the text of `index.json` (tests/test_fact_accuracy.py).
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, listing
from app.db import SessionLocal
from app.flags import EXTRACTION_TEMPLATES
from app.golden import CONTRACT, INVOICES, PRICE_LIST, holds, plain
from app.llm import LLM
from app.models import Document, Fact, UsageEvent
from app.sample import indexed_sample
from app.usage import money

# How far a fact's figure may be from the value's and still be it: a rounding of the last cent.
HALF_A_CENT = Decimal("0.005")


@dataclass(frozen=True)
class Read:
    """A fact as the run read it."""

    name: str
    value: str
    number: float | None
    day: date | None


@dataclass(frozen=True)
class Value:
    """A labelled value: the ways its words may be written, of which a right fact's value holds
    one, and for a field of numbers, money or dates its figure or day, which the fact's own
    equals."""

    ways: tuple[str, ...] = ()
    number: Decimal | None = None
    day: date | None = None

    def held_by(self, fact: Read) -> bool:
        if self.number is not None and (
            fact.number is None or abs(Decimal(str(fact.number)) - self.number) >= HALF_A_CENT
        ):
            return False
        if self.day is not None and fact.day != self.day:
            return False
        said = plain(fact.value)
        return not self.ways or any(holds(said, way) for way in self.ways)

    def __str__(self) -> str:
        """The value as the report names it: its figure or day with what it is for, or its
        words in their first way."""
        figure = self.number if self.number is not None else self.day
        if figure is None:
            return f'"{self.ways[0]}"'
        return f"{figure} ({self.ways[0]})" if self.ways else str(figure)


def said(*ways: str) -> Value:
    """Words, in each way they may be written."""
    return Value(ways)


def figure(number: str, *ways: str) -> Value:
    """A number or an amount, with the words that tell it from the field's others."""
    return Value(ways, number=Decimal(number))


def dated(day: str) -> Value:
    """A date, by its ISO day."""
    return Value(day=date.fromisoformat(day))


@dataclass(frozen=True)
class Labelled:
    """A document's facts as labelled: the kind of the template it is read for, and each field
    scored with its values, none for a field the document does not state."""

    kind: str
    fields: dict[str, tuple[Value, ...]]


ACME = said("Acme Industrial Supplies")
NORTHWIND = said("Northwind Logistics")
IBAN = said("GB29 NWBK 6016 1331 9268 19", "GB29NWBK60161331926819")
THIRTY_DAYS = said("30 days", "thirty (30) days", "thirty days")


def _invoice(
    number: str,
    issued: str,
    due: str,
    lines: tuple[tuple[str, str, str, str], ...],
    net: str,
    tax: str,
    total: str,
) -> Labelled:
    """One of the sample's invoices, Acme's to Northwind under the contract, naming no purchase
    order: each of its `lines` what was supplied, the quantity, the unit price and the amount."""
    return Labelled(
        "invoice",
        {
            "invoice_number": (said(number),),
            "issue_date": (dated(issued),),
            "due_date": (dated(due),),
            "vendor_name": (ACME,),
            "customer_name": (NORTHWIND,),
            "po_number": (),
            "contract_reference": (said("Master Services Agreement"),),
            "line_item": tuple(figure(amount, item) for item, _, _, amount in lines),
            "unit_price": tuple(figure(price, item) for item, _, price, _ in lines),
            "quantity": tuple(figure(quantity, item) for item, quantity, _, _ in lines),
            "subtotal_amount": (figure(net),),
            "tax_amount": (figure(tax),),
            "total_amount": (figure(total),),
            "payment_terms": (THIRTY_DAYS,),
            "bank_account": (IBAN,),
            "vendor_tax_id": (said("GB 123 4567 89", "GB123456789"),),
        },
    )


# The sample's documents of a built-in kind, by file name, in the order the report reads them.
LABELS: dict[str, Labelled] = {
    CONTRACT: Labelled(
        "contract",
        {
            "party": (ACME, NORTHWIND),
            "effective_date": (dated("2026-03-01"),),
            # It runs 24 months from its start and names no end.
            "expiry_date": (),
            "term_length": (said("24 months", "twenty-four months"),),
            "renewal_terms": (),
            "notice_period": (said("90 days", "ninety (90) days", "ninety days"),),
            "payment_terms": (THIRTY_DAYS,),
            # The interest on a late payment, and the two rates of an early termination.
            "penalty": (said("1.5%"), said("15%"), said("8%")),
            "liability_cap": (figure("250000"),),
            "governing_law": (said("England"),),
            "signature_date": (dated("2026-02-20"), dated("2026-02-21")),
            "bank_account": (IBAN,),
        },
    ),
    INVOICES[0]: _invoice(
        "INV-2026-0412",
        "2026-06-30",
        "2026-07-30",
        (("Hex bolts", "40", "185.00", "7400.00"), ("Washers", "40", "62.50", "2500.00")),
        "9900.00",
        "1881.00",
        "11781.00",
    ),
    INVOICES[1]: _invoice(
        "INV-2026-0458",
        "2026-07-31",
        "2026-08-30",
        (
            ("Hex bolts", "95", "185.00", "17575.00"),
            ("Pipe fittings", "60", "240.00", "14400.00"),
            ("Stretch film", "120", "38.00", "4560.00"),
        ),
        "36535.00",
        "6941.65",
        "43476.65",
    ),
    INVOICES[2]: _invoice(
        "INV-2026-0503",
        "2026-09-30",
        "2026-10-30",
        (
            ("Pipe fittings", "210", "240.00", "50400.00"),
            ("Hex bolts", "130", "185.00", "24050.00"),
            ("Corner protectors", "300", "21.50", "6450.00"),
        ),
        "80900.00",
        "15371.00",
        "96271.00",
    ),
    PRICE_LIST: Labelled(
        "price list",
        {
            "issue_date": (),
            "valid_until": (),
            "unit_price": (
                figure("185.00", "Hex bolts"),
                figure("62.50", "Washers"),
                figure("240.00", "Pipe fittings"),
                figure("38.00", "Stretch film"),
                figure("21.50", "Corner protectors"),
            ),
        },
    ),
}


@dataclass
class DocumentRead:
    """A document as the run read it: the kind of the template it was read for (None for none),
    its facts, and why its enrich stage did not finish, None when it did."""

    filename: str
    template: str | None
    facts: list[Read]
    unfinished: str | None = None


@dataclass
class FieldScore:
    """A field of a kind over the run's documents of it: the values labelled and those some fact
    holds, the facts read and those that hold a value, and the values missed and the facts read
    wrong, each with its document."""

    kind: str
    name: str
    labelled: int = 0
    found: int = 0
    read: int = 0
    right: int = 0
    missed: list[str] = field(default_factory=list)
    wrong: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.missed and not self.wrong


@dataclass(frozen=True)
class Spent:
    """What the run's reading used, by the models that answered."""

    models: list[str]
    calls: int
    input_tokens: int
    output_tokens: int
    cost_micros: int | None


@dataclass
class FactRun:
    """Each labelled field's score, what kept a document from being read for its kind, and what
    the reading used."""

    fields: list[FieldScore]
    problems: list[str]
    spent: Spent

    @property
    def failed(self) -> bool:
        return bool(self.problems) or not all(score.passed for score in self.fields)


def run_facts(settings: Settings, llm: LLM | None = None) -> FactRun:
    """Index the sample's files with extraction templates on, by `llm` or the models the settings
    name, and score what was read against the labels. Refused with `RuntimeError` without a
    model."""
    flags = {EXTRACTION_TEMPLATES: True}
    with indexed_sample(settings, llm, "Fact accuracy", flags) as (org_id, ids):
        with SessionLocal() as session:
            documents = [_read(session, document_id) for document_id in ids]
            spent = _spent(session, org_id)
    fields, problems = score(documents)
    return FactRun(fields, problems, spent)


def _read(session: Session, document_id: int) -> DocumentRead:
    document = session.get_one(Document, document_id)
    facts = session.scalars(
        select(Fact).where(Fact.document_id == document_id).order_by(Fact.id)
    ).all()
    stage = document.stages.get("enrich", {})
    status = stage.get("status", "pending")
    return DocumentRead(
        document.filename,
        document.template,
        [Read(fact.name, fact.value, fact.number, fact.date) for fact in facts],
        None if status == "done" else f"{status}: {stage.get('detail') or 'it did not run'}",
    )


def _spent(session: Session, org_id: int) -> Spent:
    events = session.scalars(
        select(UsageEvent).where(UsageEvent.org_id == org_id, UsageEvent.model.is_not(None))
    ).all()
    units: Counter[str] = Counter()
    for event in events:
        units[event.unit] += event.quantity
    costs = [event.cost_micros for event in events if event.cost_micros is not None]
    return Spent(
        sorted({event.model for event in events if event.model}),
        units["calls"],
        units["input_tokens"],
        units["output_tokens"],
        sum(costs) if costs else None,
    )


def score(
    documents: list[DocumentRead], labels: dict[str, Labelled] = LABELS
) -> tuple[list[FieldScore], list[str]]:
    """Each labelled field scored over the documents of its kind, in the labels' order, and what
    kept a labelled document from being read for its kind, in the report's words."""
    by_name = {document.filename: document for document in documents}
    scores: dict[tuple[str, str], FieldScore] = {}
    problems: list[str] = []
    for filename, labelled in labels.items():
        document = by_name[filename]
        if document.unfinished:
            problems.append(f"{filename} was not read: its enrich stage is {document.unfinished}")
        elif document.template != labelled.kind:
            read_for = f"the {document.template}" if document.template else "no"
            problems.append(
                f"{filename} was read for {read_for} template, not the {labelled.kind} one"
            )
        for name, values in labelled.fields.items():
            scored = scores.setdefault((labelled.kind, name), FieldScore(labelled.kind, name))
            facts = [fact for fact in document.facts if fact.name == name]
            scored.labelled += len(values)
            scored.read += len(facts)
            for value in values:
                if any(value.held_by(fact) for fact in facts):
                    scored.found += 1
                else:
                    scored.missed.append(f"{filename}: {value} not read")
            for fact in facts:
                if any(value.held_by(fact) for value in values):
                    scored.right += 1
                else:
                    scored.wrong.append(f'{filename}: "{fact.value}" is no labelled value')
    return list(scores.values()), problems


def describe_facts(run: FactRun) -> list[str]:
    """The lines the `fact-accuracy` command prints: each kind's fields with their verdict, a
    failed field's values missed and facts read wrong, what kept a document from its kind, what
    the reading used, and a last line with the recall and the precision."""
    lines: list[str] = []
    kind = None
    for scored in run.fields:
        if scored.kind != kind:
            kind = scored.kind
            lines.append(f"  {kind}")
        found = f"{scored.found} of {scored.labelled} found" if scored.labelled else "none labelled"
        right = f"{scored.right} of {scored.read} right" if scored.read else "none read"
        verdict = "ok" if scored.passed else "FAILED"
        lines.append(f"    {verdict:6}  {scored.name}: {found}, {right}")
        lines += [f"            {line}" for line in scored.missed + scored.wrong]
    lines += run.problems
    spent = run.spent
    if spent.calls:
        used = (
            f"Read by {listing(spent.models)}: {spent.calls:,} calls, "
            f"{spent.input_tokens:,} tokens in, {spent.output_tokens:,} out"
        )
        cost = spent.cost_micros
        lines.append(f"{used}, {money(cost)}." if cost is not None else f"{used}.")
    labelled = sum(scored.labelled for scored in run.fields)
    found = sum(scored.found for scored in run.fields)
    read = sum(scored.read for scored in run.fields)
    right = sum(scored.right for scored in run.fields)
    if not run.failed:
        lines.append(f"Every labelled field read as labelled: {found} values, {right} facts.")
    else:
        failed = sum(not scored.passed for scored in run.fields)
        lines.append(
            f"Recall {found} of {labelled} labelled values ({_share(found, labelled)}), precision "
            f"{right} of {read} facts ({_share(right, read)}); {failed} of {len(run.fields)} "
            "fields not as labelled."
        )
    return lines


def _share(part: int, whole: int) -> str:
    return f"{part / whole:.0%}" if whole else "none"
