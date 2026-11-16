"""How a document's language writes its dates and numbers (app/locales.py): told to the model as
its facts are read, and the figures a value settles, by itself or by the document's own language,
read from it in the model's place."""

import datetime as dt

import pytest
from sqlalchemy import select

from app.indexing import enrich
from app.indexing.enrich import ExtractedFact
from app.locales import WRITINGS, read_date, read_number, reading_line, writing_of
from app.models import Document, Fact
from tests.test_api import create_project
from tests.test_document_language import ENGLISH, GERMAN, NUMBERS, POLISH
from tests.test_organization_settings import change, text_upload
from tests.test_versions import given

GERMAN_INVOICE = (
    GERMAN
    + (
        "\n\nRechnungsdatum: 03.04.2026\n\nGesamtbetrag: 1.250,00 €\n\nMenge: 1.250 Stück\n"
    ).encode()
)
POLISH_INVOICE = POLISH + "\n\nData faktury: 03.04.2026\n\nKwota: 1.250,00 zł\n".encode()
ENGLISH_INVOICE = ENGLISH + b"\n\nInvoice date: 03/04/2026\n\nUnits: 1,250\n\nDue: 04/13/2026\n"
# Dates and figures alone, whose words show no language.
LOG = b"2026-03-01 1.250 EUR 42\n\n03.04.2026 980,00 EUR 17\n"

DE, EN, ES = WRITINGS["de"], WRITINGS["en"], WRITINGS["es"]


def fact(
    name: str, value: str, type: str, number: float | None = None, date: str | None = None
) -> ExtractedFact:
    """A fact as the model would reply with it, quoting its value."""
    return ExtractedFact(
        name=name,
        value=value,
        type=type,
        number=number,
        date=date,
        currency=None,
        block=0,
        quote=value,
    )


def asked(services) -> str:
    """What the last request that read a document's facts asked."""
    return next(
        content
        for schema, content in reversed(services.llm.calls)
        if schema.endswith("DocumentAnalysis")
    )


def facts_of(session, document_id: int) -> dict[str, Fact]:
    found = session.scalars(select(Fact).where(Fact.document_id == document_id))
    return {row.name: row for row in found}


def code_of(session, document_id: int) -> str | None:
    return session.scalar(select(Document.read_language_code).where(Document.id == document_id))


@pytest.mark.parametrize(
    ("written", "writing", "number"),
    [
        # Both marks: the last stands before the decimals, whatever the language.
        ("1.250,00 €", None, 1250.0),
        ("EUR 4,250.00", None, 4250.0),
        ("1 250,00", None, 1250.0),
        ("CHF 1'250.00", None, 1250.0),
        ("1’250.50", None, 1250.5),
        ("1.250.000", None, 1250000.0),
        ("1,25,000.00", None, 125000.0),
        ("12 345 678", None, 12345678.0),
        ("1,25", None, 1.25),
        ("0,250", None, 0.25),
        ("1250.000", DE, 1250.0),
        ("90", None, 90.0),
        ("-€1,250.00", None, -1250.0),
        ("€ -1.250,00", None, -1250.0),
        ("(1,250.00)", None, -1250.0),
        ("50,– €", None, 50.0),
        ("US$ 1,250", EN, 1250.0),
        ("1 250 kr", WRITINGS["sv"], 1250.0),
        # One mark before three digits: thousands or decimals, as the language writes them.
        ("1,250", None, None),
        ("1,250", DE, 1.25),
        ("1,250", EN, 1250.0),
        ("1.250", DE, 1250.0),
        ("1.250", EN, 1.25),
        ("1.250", ES, None),
        # Anything but a figure alone is the model's to read.
        ("19 %", DE, None),
        ("30 days", None, None),
        ("1,5 Mio. EUR", DE, None),
        ("1.25.0,00", None, None),
        ("12 34", None, None),
        ("1.250,00-", DE, None),
        ("03.04.2026", DE, None),
    ],
)
def test_a_figure_is_read_by_its_marks_and_where_they_leave_it_open_by_its_language(
    written, writing, number
):
    assert read_number(written, writing) == number


@pytest.mark.parametrize(
    ("written", "writing", "day"),
    [
        ("2026-04-03", None, "2026-04-03"),
        ("13.04.2026", None, "2026-04-13"),
        ("04/13/2026", EN, "2026-04-13"),
        ("04.04.2026", None, "2026-04-04"),
        ("03/24/2026", DE, "2026-03-24"),
        ("03.04.2026", DE, "2026-04-03"),
        ("2026. 04. 03.", WRITINGS["hu"], "2026-04-03"),
        ("3. 4. 2026", WRITINGS["cs"], "2026-04-03"),
        # English puts the day first in Britain and the month first in the United States.
        ("03/04/2026", EN, None),
        ("03/04/2026", None, None),
        ("03.04.2026", WRITINGS["sv"], None),
        ("03.04.26", DE, None),
        ("31.02.2026", DE, None),
        ("3 April 2026", DE, None),
    ],
)
def test_a_date_is_read_by_its_numbers_and_where_they_leave_it_open_by_its_language(
    written, writing, day
):
    read = read_date(written, writing)
    assert (read.isoformat() if read else None) == day


def test_a_language_is_known_by_its_configuration_or_where_that_names_none_by_its_code():
    assert writing_of("german", None) == DE
    assert writing_of("german", "fr") == DE
    assert writing_of("norwegian", None) == WRITINGS["no"]
    assert writing_of("simple", "pl") == WRITINGS["pl"]
    assert writing_of("simple", None) is None
    # A language the conventions do not know, or one whose years may be of another era.
    assert writing_of("yiddish", None) is None
    assert writing_of("simple", "th") is None
    assert reading_line(None) == ""
    assert reading_line(WRITINGS["sv"]).endswith("An amount in kr is in SEK.")
    assert "leave its date null" in reading_line(EN)


def test_a_documents_facts_are_read_by_the_way_its_language_writes_dates_and_numbers(
    client, session, services
):
    project = create_project(client)
    # The model as a careless one reads them: month first, and the decimals where English has
    # them; "1,250" is how English writes the document's "1.250", so it is the model's alone.
    services.llm.more_facts = [
        fact("invoice_date", "03.04.2026", "date", date="2026-03-04"),
        fact("total_amount", "1.250,00 €", "money", number=1.25),
        fact("quantity", "1.250", "number", number=1.25),
        fact("units", "1,250", "number", number=1250),
        fact("payment_days", "30 Tage", "number", number=30),
    ]
    german = text_upload(client, services, project["id"], "rechnung.txt", GERMAN_INVOICE)

    assert code_of(session, german) == "de"
    prompt = asked(services)
    assert "The document is written in German. A date in figures puts the day first" in prompt
    read = facts_of(session, german)
    assert read["invoice_date"].date == dt.date(2026, 4, 3)
    assert read["total_amount"].number == 1250.0
    assert read["quantity"].number == 1250.0
    assert read["units"].number == 1250
    assert read["payment_days"].number == 30

    # Polish is searched by its words as written, and still read as Polish writes.
    services.llm.more_facts = [
        fact("invoice_date", "03.04.2026", "date", date="2026-03-04"),
        fact("total_amount", "1.250,00 zł", "money", number=1.25),
    ]
    polish = text_upload(client, services, project["id"], "faktura.txt", POLISH_INVOICE)
    assert code_of(session, polish) == "pl"
    assert "The document is written in Polish." in asked(services)
    read = facts_of(session, polish)
    assert read["invoice_date"].date == dt.date(2026, 4, 3)
    assert read["total_amount"].number == 1250.0

    # English leaves a date open whose day and month could each be the other: the model's
    # reading stands, or none, as it was told to leave it.
    services.llm.more_facts = [
        fact("invoice_date", "03/04/2026", "date", date=None),
        fact("due_date", "04/13/2026", "date", date=None),
        fact("units", "1,250", "number", number=1.25),
    ]
    english = text_upload(client, services, project["id"], "invoice.txt", ENGLISH_INVOICE)
    assert "where nothing shows which, leave its date null" in asked(services)
    read = facts_of(session, english)
    assert read["invoice_date"].date is None
    assert read["due_date"].date == dt.date(2026, 4, 13)
    assert read["units"].number == 1250


def test_every_request_of_a_long_document_says_how_it_writes_its_dates_and_numbers(
    client, services, monkeypatch
):
    monkeypatch.setattr(enrich, "WINDOW_CHARS", 150)
    project = create_project(client)
    text_upload(client, services, project["id"], "rechnung.txt", GERMAN_INVOICE)
    requests = [
        (schema, content)
        for schema, content in services.llm.calls
        if schema.endswith("WindowAnalysis") or schema == "ProfileWithSections"
    ]
    assert [schema for schema, _ in requests][-1] == "ProfileWithSections"
    assert len(requests) > 2
    assert all("The document is written in German." in content for _, content in requests)


def test_the_organisations_language_is_a_likelihood_for_a_document_without_one_of_its_own(
    client, session, services
):
    project = create_project(client)
    services.llm.more_facts = [
        fact("quantity", "1.250", "number", number=1.25),
        fact("total_amount", "980,00", "money", number=98000),
        fact("delivered_on", "03.04.2026", "date", date="2026-03-04"),
    ]
    # While the organisation chose no language, the deployment's says nothing of the document.
    log = text_upload(client, services, project["id"], "log.txt", LOG)
    assert code_of(session, log) is None
    assert "A date in figures" not in asked(services)

    change(client, language="german")
    later = text_upload(client, services, project["id"], "more.txt", LOG + b"\n")
    assert "its organisation works in German" in asked(services)
    read = facts_of(session, later)
    # The model was told; only what the value settles by itself is read in its place.
    assert read["quantity"].number == 1.25
    assert read["total_amount"].number == 980.0
    assert read["delivered_on"].date == dt.date(2026, 3, 4)


def test_a_new_version_forgets_the_language_the_earlier_file_was_read_in(client, session, services):
    project = create_project(client)
    first = text_upload(client, services, project["id"], "notice.txt", GERMAN)
    assert code_of(session, first) == "de"
    given(client, services, first, "notice.txt", NUMBERS)
    assert code_of(session, first) is None
