"""Members' checks of facts (app/fact_checks.py, `PUT /api/facts/{id}/check`, `DELETE
/api/fact-checks/{id}`): a fact checked as read or corrected, what the table, the agent and the
export read of it, and the check kept through the document's next reading."""

import datetime as dt
import json

import pytest
from sqlalchemy import select

from app.agent.tools import QueryFactsInput, ToolContext, query_facts
from app.fact_checks import CHECK_GONE, FACT_GONE
from app.indexing.enrich import ExtractedFact
from app.indexing.pipeline import process_document
from app.models import Document, Fact, FactCheck
from app.trash import DOCUMENT_IN_TRASH
from tests.test_api import create_project, upload
from tests.test_audit import actions
from tests.test_exports import archive_of, ask, run_export
from tests.test_fact_table import invoice, invoices, names, table
from tests.test_templates import fact, turn_on


@pytest.fixture
def templates_on(monkeypatch):
    turn_on(monkeypatch)


@pytest.fixture
def three(client, session) -> int:
    return invoices(client, session)


def check(client, fact_id: int, value: str | None = None, currency: str | None = None) -> dict:
    body = {} if value is None else {"value": value, "currency": currency}
    response = client.put(f"/api/facts/{fact_id}/check", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def refused(client, fact_id: int, value: str) -> tuple[int, str]:
    response = client.put(f"/api/facts/{fact_id}/check", json={"value": value})
    return response.status_code, response.json()["detail"]


def remove(client, check_id: int) -> None:
    assert client.delete(f"/api/fact-checks/{check_id}").status_code == 204


def cell(shown: dict, filename: str, field: str) -> list[dict]:
    [row] = [row for row in shown["rows"] if row["filename"] == filename]
    return row["cells"].get(field, [])


def row_of(shown: dict, filename: str) -> dict:
    [row] = [row for row in shown["rows"] if row["filename"] == filename]
    return row


def checks_of(session, org_id: int) -> list[tuple[str, dict]]:
    return [(action, detail) for action, detail in actions(session, org_id) if "fact" in action]


def test_checks_are_refused_while_the_flag_is_off(client, session):
    project = create_project(client)["id"]
    document = invoice(session, project, "a.pdf", invoice_number="INV-1")
    fact_id = session.scalar(select(Fact.id).where(Fact.document_id == document))
    response = client.put(f"/api/facts/{fact_id}/check", json={})
    assert response.status_code == 404


def test_a_member_checks_a_fact_as_read_and_takes_the_check_back(
    client, session, account, templates_on, three
):
    [number] = cell(table(client, three), "acme-2.pdf", "invoice_number")
    assert number["check"] is None

    checked = check(client, number["id"])
    assert checked["value"] == "INV-2" and checked["check"]["corrected_from"] is None
    assert checked["check"]["checked_by"] == account.user.name
    assert checked["check"]["value"] == "INV-2"
    shown = cell(table(client, three), "acme-2.pdf", "invoice_number")[0]
    assert shown["check"] == checked["check"]
    # Checking it again changes nothing, and writes no second row.
    assert check(client, number["id"])["check"] == checked["check"]

    remove(client, checked["check"]["id"])
    assert cell(table(client, three), "acme-2.pdf", "invoice_number")[0]["check"] is None
    assert session.scalar(select(FactCheck)) is None
    # The log names the field and the document, never what the document says.
    assert checks_of(session, account.organization.id) == [
        ("fact_checked", {"filename": "acme-2.pdf", "field": "invoice_number"}),
        ("fact_check_removed", {"filename": "acme-2.pdf", "field": "invoice_number"}),
    ]


def test_a_correction_is_the_value_the_table_sorts_and_keeps_rows_by(
    client, session, account, templates_on, three
):
    [total] = cell(table(client, three), "acme-2.pdf", "total_amount")
    corrected = check(client, total["id"], "2000")
    assert (corrected["value"], corrected["number"]) == ("2,000.00", 2000.0)
    assert corrected["check"]["corrected_from"] == "EUR 90.00"
    assert corrected["check"]["value"] == "2,000.00"
    # Sorted and kept by the member's value at once.
    assert names(table(client, three, sort="total_amount")) == [
        "acme-10.pdf",
        "acme-2.pdf",
        "globex.pdf",
    ]
    assert names(table(client, three, filter="total_amount:min:1500")) == ["acme-2.pdf"]

    # Another value in another currency, then the model's own again: a check of it.
    assert check(client, total["id"], "1999.5", "usd")["value"] == "1,999.50 USD"
    back = check(client, total["id"], "90")
    assert (back["value"], back["number"], back["check"]["corrected_from"]) == (
        "EUR 90.00",
        90.0,
        None,
    )
    corrected = check(client, total["id"], "2000")
    remove(client, corrected["check"]["id"])
    restored = cell(table(client, three), "acme-2.pdf", "total_amount")[0]
    assert (restored["value"], restored["number"], restored["check"]) == ("EUR 90.00", 90.0, None)
    assert [action for action, _ in checks_of(session, account.organization.id)] == [
        "fact_corrected",
        "fact_corrected",
        "fact_checked",
        "fact_corrected",
        "fact_check_removed",
    ]


def test_a_value_is_written_as_its_field_s_type_takes_it(client, session, templates_on, three):
    shown = table(client, three)
    [day] = cell(shown, "acme-2.pdf", "issue_date")
    [number] = cell(shown, "acme-2.pdf", "invoice_number")
    [total] = cell(shown, "acme-2.pdf", "total_amount")

    assert refused(client, day["id"], "12 Feb 2026") == (422, "Write the date as 2026-03-01.")
    assert refused(client, total["id"], "1,200") == (
        422,
        "Write the number with digits alone, such as 1250.50.",
    )
    assert refused(client, total["id"], "inf")[0] == 422
    assert refused(client, number["id"], "   ")[1].startswith("Write the value the document")
    dated = check(client, day["id"], "2026-02-13")
    assert (dated["value"], dated["date"]) == ("2026-02-13", "2026-02-13")
    assert check(client, number["id"], "  INV-0002 ")["value"] == "INV-0002"
    assert check(client, total["id"], "1234567.891")["value"] == "1,234,567.891"


def test_the_rows_to_review_are_those_with_a_fact_nobody_checked(client, templates_on, three):
    shown = table(client, three)
    assert (shown["total"], shown["to_review"]) == (3, 3)
    for facts in row_of(shown, "globex.pdf")["cells"].values():
        for each in facts:
            check(client, each["id"])
    shown = table(client, three, review="true")
    assert names(shown) == ["acme-2.pdf", "acme-10.pdf"]
    assert (shown["total"], shown["to_review"]) == (2, 2)
    assert table(client, three)["to_review"] == 2

    # A field's own condition: one of its facts nobody checked.
    [first, second] = cell(table(client, three), "acme-10.pdf", "line_item")
    check(client, first["id"])
    assert names(table(client, three, filter="line_item:unchecked")) == [
        "acme-2.pdf",
        "acme-10.pdf",
    ]
    check(client, second["id"])
    assert names(table(client, three, filter="line_item:unchecked")) == ["acme-2.pdf"]


def test_a_fact_of_a_document_in_the_trash_or_read_again_is_gone(
    client, session, templates_on, three
):
    [number] = cell(table(client, three), "acme-2.pdf", "invoice_number")
    held = check(client, number["id"])["check"]
    document = session.get(Document, number["document_id"])
    document.deleted_at = dt.datetime.now(dt.UTC)
    session.commit()
    for response in (
        client.put(f"/api/facts/{number['id']}/check", json={}),
        client.delete(f"/api/fact-checks/{held['id']}"),
    ):
        assert (response.status_code, response.json()["detail"]) == (404, DOCUMENT_IN_TRASH)

    document.deleted_at = None
    session.delete(session.get(Fact, number["id"]))
    session.commit()
    response = client.put(f"/api/facts/{number['id']}/check", json={})
    assert (response.status_code, response.json()["detail"]) == (404, FACT_GONE)
    remove(client, held["id"])
    response = client.delete(f"/api/fact-checks/{held['id']}")
    assert (response.status_code, response.json()["detail"]) == (404, CHECK_GONE)


def priced(value: str, number: float) -> ExtractedFact:
    return fact("price", value, "money", number).model_copy(update={"currency": "EUR"})


def read_contract(client, services, sample_pdf) -> tuple[int, int]:
    """A project and the contract in it, read for the built-in contract template with a price
    and a party besides the facts the fake model finds."""
    project = create_project(client)["id"]
    document = upload(client, project, sample_pdf)["documents"][0]["id"]
    services.llm.template = "contract"
    services.llm.more_facts = [
        priced("EUR 1,200.00", 1200),
        fact("party", "Acme Corp (Supplier)"),
    ]
    process_document(document, services)
    return project, document


def read_again(client, services, document: int, stage: str = "enrich") -> None:
    queued = client.post(f"/api/documents/{document}/reprocess", params={"from_stage": stage})
    assert queued.status_code == 202, queued.text
    process_document(document, services)


def test_a_check_outlives_the_document_s_next_reading(
    client, session, services, sample_pdf, templates_on
):
    project, document = read_contract(client, services, sample_pdf)
    shown = table(client, project, "contract")
    [party] = cell(shown, "msa.pdf", "party")
    [price] = cell(shown, "msa.pdf", "price")
    check(client, party["id"])
    check(client, price["id"], "1250")

    # Read again the same, from its summaries and facts and from its outline: new facts, the
    # checks given to them again, the correction in the price's place.
    for stage in ("enrich", "structure"):
        read_again(client, services, document, stage)
        shown = table(client, project, "contract")
        [party] = cell(shown, "msa.pdf", "party")
        [price] = cell(shown, "msa.pdf", "price")
        assert party["check"] is not None and party["check"]["corrected_from"] is None
        assert (price["value"], price["number"]) == ("1,250.00 EUR", 1250.0)
        assert price["check"]["corrected_from"] == "EUR 1,200.00"
        assert row_of(shown, "msa.pdf")["earlier"] == {}

    # A model that writes the same amount another way keeps the check.
    services.llm.more_facts[0] = priced("1200.00 EUR", 1200)
    read_again(client, services, document)
    [price] = cell(table(client, project, "contract"), "msa.pdf", "price")
    assert (price["value"], price["check"]["corrected_from"]) == ("1,250.00 EUR", "EUR 1,200.00")

    # One that reads another amount leaves the check to the cell as made before.
    services.llm.more_facts[0] = priced("EUR 1,300.00", 1300)
    read_again(client, services, document)
    shown = table(client, project, "contract")
    [price] = cell(shown, "msa.pdf", "price")
    assert (price["value"], price["check"]) == ("EUR 1,300.00", None)
    [earlier] = row_of(shown, "msa.pdf")["earlier"]["price"]
    assert (earlier["value"], earlier["corrected_from"]) == ("1,250.00 EUR", "EUR 1,200.00")
    assert shown["to_review"] == 1

    # And one that reads the member's own amount takes it again.
    services.llm.more_facts[0] = priced("EUR 1,250", 1250)
    read_again(client, services, document)
    shown = table(client, project, "contract")
    [price] = cell(shown, "msa.pdf", "price")
    assert price["check"]["id"] == earlier["id"]
    assert row_of(shown, "msa.pdf")["earlier"] == {}

    # A check no fact holds is forgotten by its removal.
    services.llm.more_facts[0] = priced("EUR 1,300.00", 1300)
    read_again(client, services, document)
    remove(client, earlier["id"])
    assert row_of(table(client, project, "contract"), "msa.pdf")["earlier"] == {}
    assert session.scalar(select(FactCheck.name)) == "party"


def test_the_agent_and_the_export_read_what_the_members_said(
    client, session, services, sample_pdf, templates_on
):
    project, document = read_contract(client, services, sample_pdf)
    shown = table(client, project, "contract")
    [party] = cell(shown, "msa.pdf", "party")
    [price] = cell(shown, "msa.pdf", "price")
    check(client, party["id"])
    check(client, price["id"], "1250")

    context = ToolContext(session, services.embedder, project)
    rows = query_facts(context, QueryFactsInput(names=["party", "price"]))
    assert "party | Acme Corp (Supplier) (checked by a member) |" in rows
    assert (
        "price | 1,250.00 EUR (EUR) (corrected by a member; the indexing model read "
        "EUR 1,200.00) |" in rows
    )
    assert "price EUR: count 1, sum 1,250.00" in rows

    export = ask(client, project).json()
    run_export(services)
    data = json.loads(archive_of(client, project, export["id"]).read(f"documents/{document}.json"))
    kept = {item["name"]: item for item in data["facts"]}
    assert kept["price"]["value"] == "1,250.00 EUR"
    assert kept["price"]["check"]["corrected_from"] == "EUR 1,200.00"
    assert kept["party"]["check"]["value"] == "Acme Corp (Supplier)"
    assert kept["notice_period"]["check"] is None


def test_a_viewer_checks_nothing(client, account, templates_on, three, make_account, act_as):
    [number] = cell(table(client, three), "acme-2.pdf", "invoice_number")
    held = check(client, number["id"])["check"]
    act_as(make_account("vic@example.com", account.organization, role="viewer"))
    assert client.put(f"/api/facts/{number['id']}/check", json={}).status_code == 403
    assert client.delete(f"/api/fact-checks/{held['id']}").status_code == 403
    assert cell(table(client, three), "acme-2.pdf", "invoice_number")[0]["check"] == held
