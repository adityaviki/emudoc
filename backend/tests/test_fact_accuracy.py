"""Fact-extraction accuracy (app/fact_accuracy.py): the labelled values are what the sample's
documents say, a fact holds a value by its figure, day or words, and a run reads the sample with
extraction templates on in an organisation it deletes after, scoring each field."""

from datetime import date

import pytest
from sqlalchemy import func, select

from app import maintenance
from app.config import get_settings
from app.extraction import BUILT_IN, BUILT_IN_TYPES
from app.fact_accuracy import (
    LABELS,
    DocumentRead,
    FactRun,
    Read,
    Spent,
    Value,
    describe_facts,
    figure,
    run_facts,
    said,
    score,
)
from app.golden import CONTRACT, INVOICES, PRICE_LIST, holds, plain
from app.indexing.enrich import ENRICH_PROMPT_VERSION, ExtractedFact
from app.models import Organization, UsageEvent
from app.sample import sample_files
from tests.conftest import FakeLLM

TEMPLATES = {template.doc_type: template for template in BUILT_IN}
# A line each labelled document's request holds and no other's does.
MARKERS = {
    CONTRACT: "Between Acme Industrial Supplies",
    INVOICES[0]: "Invoice INV-2026-0412",
    INVOICES[1]: "Invoice INV-2026-0458",
    INVOICES[2]: "Invoice INV-2026-0503",
    PRICE_LIST: "price_eur",
}


def text_of(name: str) -> str:
    """A sample document's blocks, whose text a fact is read from, as the scoring reads a fact."""
    (file,) = [file for file in sample_files() if file.name == name]
    return plain(" ".join(block["text"] for block in file.index["blocks"]))


def written(value: Value) -> str:
    """A labelled value as a fact of it may write it: what it is for, then its figure or day."""
    figure = value.number if value.number is not None else value.day
    if figure is None:
        return value.ways[0]
    return f"{value.ways[0]}: {figure}" if value.ways else str(figure)


def as_labelled(filename: str) -> list[Read]:
    """The facts of a document read exactly as labelled."""
    return [
        Read(name, written(value), None if value.number is None else float(value.number), value.day)
        for name, values in LABELS[filename].fields.items()
        for value in values
    ]


class Labeller(FakeLLM):
    """A model that reads each labelled document for its kind with its labelled values, but for
    the faults planted: a value it leaves out, a fact it adds, a document it reads for no
    template."""

    def __init__(
        self,
        left_out: tuple[str, str] | None = None,
        added: tuple[str, ExtractedFact] | None = None,
        no_template: str | None = None,
    ):
        super().__init__()
        self.left_out = left_out
        self.added = added
        self.no_template = no_template

    def structured(self, content, schema, usage=None):
        if isinstance(content, str):
            name = next((name for name, marker in MARKERS.items() if marker in content), None)
            self.template, self.more_facts = self._reading(name)
        return super().structured(content, schema, usage)

    def _reading(self, filename: str | None) -> tuple[str | None, list[ExtractedFact]]:
        if filename is None:
            return None, []
        labelled = LABELS[filename]
        template = TEMPLATES[labelled.kind]
        facts = [
            ExtractedFact(
                name=fact.name,
                value=fact.value,
                type=template.field(fact.name).type,
                number=fact.number,
                date=None if fact.day is None else fact.day.isoformat(),
                currency=None,
                block=0,
                quote=fact.value,
            )
            for fact in as_labelled(filename)
            if (filename, fact.name) != self.left_out
        ]
        if self.added and self.added[0] == filename:
            facts.append(self.added[1])
        return (None if filename == self.no_template else labelled.kind), facts


def test_the_labelled_values_are_what_the_documents_say():
    kinds = {file.name: file.index["doc_type"] for file in sample_files()}
    # Every document of the sample of a built-in kind is labelled, as the kind it is.
    assert {name for name, kind in kinds.items() if kind in BUILT_IN_TYPES} == set(LABELS)
    for name, labelled in LABELS.items():
        assert labelled.kind == kinds[name]
        text = text_of(name)
        for field, values in labelled.fields.items():
            kind = TEMPLATES[labelled.kind].field(field)
            assert kind is not None, (name, field)
            for value in values:
                # Typed as the template's field is.
                assert (value.number is not None) == (kind.type in ("number", "money")), field
                assert (value.day is not None) == (kind.type == "date"), field
                assert value.ways or kind.type != "text", field
                # Written in the document, its words in one of their ways at least.
                if value.number is not None:
                    assert holds(text, str(value.number)), (name, field, value)
                if value.day is not None:
                    day = value.day
                    ways = (day.isoformat(), f"{day.day} {day:%B} {day.year}")
                    assert any(holds(text, way) for way in ways), (name, field, value)
                if value.ways:
                    assert any(holds(text, way) for way in value.ways), (name, field, value)


@pytest.mark.parametrize(
    ("value", "fact", "held"),
    [
        (
            said("Acme Industrial Supplies"),
            Read("x", "ACME INDUSTRIAL SUPPLIES LTD.", None, None),
            True,
        ),
        (said("15%"), Read("x", "1.5% per month on late payments", None, None), False),
        (figure("43476.65"), Read("x", "EUR 43 476,65", 43476.65, None), True),
        (figure("43476.65"), Read("x", "EUR 43,476.66", 43476.66, None), False),
        (figure("185.00", "Hex bolts"), Read("x", "Washers M10: 185.00", 185.0, None), False),
        (figure("185.00", "Hex bolts"), Read("x", "Hex bolts M10 (pallet)", None, None), False),
        (Value(day=date(2026, 8, 30)), Read("x", "30 August 2026", None, date(2026, 8, 30)), True),
        (Value(day=date(2026, 8, 30)), Read("x", "2026-08-30", None, None), False),
    ],
)
def test_a_fact_holds_a_value_by_its_figure_day_or_words(value, fact, held):
    assert value.held_by(fact) is held


def test_a_reading_as_labelled_passes_every_field():
    documents = [DocumentRead(name, LABELS[name].kind, as_labelled(name)) for name in LABELS]
    fields, problems = score(documents)
    assert problems == []
    assert all(scored.passed for scored in fields)
    assert [scored.kind for scored in fields][:2] == ["contract", "contract"]
    run = FactRun(fields, problems, Spent([], 0, 0, 0, None))
    values = sum(len(values) for labelled in LABELS.values() for values in labelled.fields.values())
    assert describe_facts(run)[-1] == (
        f"Every labelled field read as labelled: {values} values, {values} facts."
    )


def test_the_command_scores_each_field_and_exits_1_on_one_not_as_labelled(
    session, monkeypatch, capsys
):
    wrong = ExtractedFact(
        name="po_number",
        value="PO-7708",
        type="text",
        number=None,
        date=None,
        currency=None,
        block=0,
        quote="PO-7708",
    )
    labeller = Labeller(
        left_out=(INVOICES[1], "due_date"), added=(INVOICES[0], wrong), no_template=PRICE_LIST
    )
    monkeypatch.setattr(maintenance, "run_facts", lambda settings: run_facts(settings, labeller))
    with pytest.raises(SystemExit) as stop:
        maintenance.main(["fact-accuracy"])

    assert stop.value.code == 1
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "  contract"
    assert out[1] == "    ok      party: 2 of 2 found, 2 of 2 right"
    assert "    ok      expiry_date: none labelled, none read" in out
    assert out[out.index("  invoice") + 3 :][:2] == [
        "    FAILED  due_date: 2 of 3 found, 2 of 2 right",
        "            invoice-INV-2026-0458.pdf: 2026-08-30 not read",
    ]
    assert out[out.index("    FAILED  po_number: none labelled, 0 of 1 right") + 1] == (
        '            invoice-INV-2026-0412.pdf: "PO-7708" is no labelled value'
    )
    # Read for no template, its facts are still scored by their names.
    assert "    ok      unit_price: 5 of 5 found, 5 of 5 right" in out
    assert "price-list.csv was read for no template, not the price list one" in out
    calls = len(labeller.calls)
    # The prompts the facts were read with, by the usage rows of the reading.
    assert out[-3] == f"Prompt versions: enrich {ENRICH_PROMPT_VERSION}."
    assert out[-2] == (
        f"Read by {get_settings().index_model}: {calls} calls, {calls * 10} tokens in, "
        f"{calls * 5} out."
    )
    assert out[-1] == (
        "Recall 78 of 79 labelled values (99%), precision 78 of 79 facts (99%); 2 of 31 fields "
        "not as labelled."
    )
    # The run's organisation, and what its reading recorded, went with it.
    assert session.scalar(select(func.count()).select_from(Organization)) == 0
    assert session.scalar(select(func.count()).select_from(UsageEvent)) == 0


def test_a_run_without_a_model_is_refused(session):
    labeller = Labeller()
    labeller.available = False
    with pytest.raises(RuntimeError, match="indexed by a model, and none answers"):
        run_facts(get_settings(), labeller)
    assert session.scalar(select(func.count()).select_from(Organization)) == 0
