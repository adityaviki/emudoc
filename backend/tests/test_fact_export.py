"""The facts table as a file to save (app/fact_export.py, `GET
/api/projects/{id}/fact-table/export`): every row the table's query keeps, as a CSV file of the
table as the page shows it or an Excel workbook of the same rows typed and a sheet of their facts,
each download a row of the audit log."""

import datetime as dt
import io

import pytest
from openpyxl import load_workbook
from sqlalchemy import select, update

import app.fact_export
from app.config import get_settings
from app.extraction import BUILT_IN
from app.models import Document, Fact
from app.storage import content_disposition
from tests.test_api import create_project
from tests.test_apikeys import make_key, program
from tests.test_audit import actions, read_csv
from tests.test_fact_checks import cell, check
from tests.test_fact_table import invoice, invoices, table

INVOICE_FIELDS = [field.name for field in BUILT_IN[0].fields]
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture
def three(client, session) -> int:
    return invoices(client, session)


def export(client, project_id: int, kind: str = "invoice", **params):
    return client.get(
        f"/api/projects/{project_id}/fact-table/export", params={"kind": kind, **params}
    )


def workbook(response):
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == XLSX
    assert int(response.headers["content-length"]) == len(response.content)
    return load_workbook(io.BytesIO(response.content))


def sheet_rows(sheet) -> list[dict]:
    heading, *rows = sheet.iter_rows(values_only=True)
    return [dict(zip(heading, row, strict=True)) for row in rows]


def exported(session) -> list[dict]:
    return [detail for action, detail in actions(session) if action == "facts_exported"]


def test_the_csv_is_the_table_as_the_page_shows_it(client, session, monkeypatch, three):
    # Read a batch at a time, the batches crossed.
    monkeypatch.setattr(app.fact_export, "BATCH", 2)
    response = export(client, three)
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    day = dt.datetime.now(dt.UTC).date().isoformat()
    assert response.headers["content-disposition"] == content_disposition(
        f"Contracts invoice facts {day}.csv"
    )
    rows = read_csv(response)
    assert list(rows[0]) == [
        "Document",
        *INVOICE_FIELDS,
        "Review",
        "Unchecked",
        "Quote not found",
    ]
    # The documents read for the template alone, by name: not the contract, the one in the trash,
    # the one read for none or another project's.
    assert [row["Document"] for row in rows] == ["acme-2.pdf", "acme-10.pdf", "globex.pdf"]
    acme = rows[1]
    assert acme["invoice_number"] == "INV-10"
    assert acme["issue_date"] == "1 March 2026"
    assert acme["total_amount"] == "EUR 1,200.00"
    # A field's several facts in the document's order; a field it does not state empty.
    assert acme["line_item"] == "Bolts, 40 x 10.00; Nuts, 80 x 10.00"
    assert acme["due_date"] == ""
    assert "notes" not in acme
    assert acme["Unchecked"] == "invoice_number; issue_date; line_item; total_amount"
    assert acme["Review"] == ""


def test_the_csv_holds_the_table_s_sort_conditions_and_words(client, three):
    rows = read_csv(
        export(client, three, sort="total_amount", order="desc", filter=["vendor_name:has"])
    )
    # The highest total first, a row without one last.
    assert [row["Document"] for row in rows] == ["acme-2.pdf", "globex.pdf"]
    rows = read_csv(export(client, three, q="Bolts"))
    assert [row["Document"] for row in rows] == ["acme-10.pdf"]


def test_the_file_says_what_the_members_checked_and_corrected(client, session, three):
    shown = table(client, three)
    for field in ("invoice_number", "vendor_name"):
        check(client, cell(shown, "globex.pdf", field)[0]["id"])
    [total] = cell(shown, "acme-10.pdf", "total_amount")
    check(client, total["id"], "1250")
    # The quotes of acme-2's facts found in the text, but its vendor's.
    acme_2 = session.scalar(select(Document.id).where(Document.filename == "acme-2.pdf"))
    session.execute(
        update(Fact)
        .where(Fact.document_id == acme_2, Fact.name != "vendor_name")
        .values(verified=True)
    )
    session.commit()

    rows = {row["Document"]: row for row in read_csv(export(client, three))}
    assert rows["acme-10.pdf"]["total_amount"] == "1,250.00"
    assert rows["acme-10.pdf"]["Unchecked"] == "invoice_number; issue_date; line_item"
    assert rows["globex.pdf"]["Unchecked"] == ""
    assert rows["acme-2.pdf"]["Quote not found"] == "vendor_name"
    # Unchecked alone: the rows with a fact nobody checked.
    rows = read_csv(export(client, three, unchecked="true"))
    assert [row["Document"] for row in rows] == ["acme-2.pdf", "acme-10.pdf"]


def test_the_workbook_types_each_lone_value_and_lists_every_fact(client, session, account, three):
    session.execute(update(Fact).where(Fact.name == "total_amount").values(currency="EUR"))
    session.commit()
    shown = table(client, three)
    [total] = cell(shown, "acme-10.pdf", "total_amount")
    check(client, total["id"], "1250")
    check(client, cell(shown, "acme-2.pdf", "invoice_number")[0]["id"])

    book = workbook(export(client, three, format="xlsx"))
    assert book.sheetnames == ["Documents", "Facts"]
    documents = book["Documents"]
    assert [row["Document"] for row in sheet_rows(documents)] == [
        "acme-2.pdf",
        "acme-10.pdf",
        "globex.pdf",
    ]
    acme = sheet_rows(documents)[1]
    assert acme["total_amount"] == 1250.0
    assert acme["issue_date"] == dt.datetime(2026, 3, 1)
    assert acme["invoice_number"] == "INV-10"
    # Several values are the cell's text, as the table shows them.
    assert acme["line_item"] == "Bolts, 40 x 10.00; Nuts, 80 x 10.00"
    assert acme["due_date"] is None
    # An amount is a number shown in its currency, a date a date.
    heading = [heading.value for heading in next(documents.iter_rows(max_row=1))]
    amount = documents.cell(row=3, column=heading.index("total_amount") + 1)
    assert amount.number_format == '#,##0.00 "EUR"'
    issued = documents.cell(row=3, column=heading.index("issue_date") + 1)
    assert issued.is_date and issued.number_format == "yyyy-mm-dd"

    facts = sheet_rows(book["Facts"])
    assert [
        (row["Document"], row["Field"]) for row in facts if row["Document"] == "acme-10.pdf"
    ] == [
        ("acme-10.pdf", "invoice_number"),
        ("acme-10.pdf", "issue_date"),
        ("acme-10.pdf", "line_item"),
        ("acme-10.pdf", "line_item"),
        ("acme-10.pdf", "total_amount"),
    ]
    corrected = next(
        row for row in facts if row["Field"] == "total_amount" and row["Value"] == 1250
    )
    assert corrected | {"Checked at (UTC)": None} == {
        "Document": "acme-10.pdf",
        "Field": "total_amount",
        "Value": 1250.0,
        "Currency": "EUR",
        "As written": "1,250.00 EUR",
        "Page": 1,
        "Quote": "“EUR 1,200.00”",
        "Quote found": False,
        "Checked by": account.user.name,
        "Checked at (UTC)": None,
        "Model read": "EUR 1,200.00",
    }
    assert isinstance(corrected["Checked at (UTC)"], dt.datetime)
    checked = next(row for row in facts if row["Value"] == "INV-2")
    assert (checked["Checked by"], checked["Model read"]) == (account.user.name, None)
    unchecked = next(row for row in facts if row["Value"] == "G-7")
    assert (unchecked["Checked by"], unchecked["Checked at (UTC)"]) == (None, None)


def test_nothing_in_either_file_runs_as_a_formula(client, session):
    project = create_project(client)["id"]
    invoice(session, project, "+1.pdf", invoice_number='=HYPERLINK("https://example.com")')
    [row] = read_csv(export(client, project))
    assert row["Document"] == "'+1.pdf"
    assert row["invoice_number"] == '\'=HYPERLINK("https://example.com")'
    documents = workbook(export(client, project, format="xlsx"))["Documents"]
    number = documents.cell(row=2, column=2)
    assert (number.value, number.data_type) == ('=HYPERLINK("https://example.com")', "s")


def test_a_sheet_past_what_excel_holds_says_the_rest_are_left_out(client, monkeypatch, three):
    monkeypatch.setattr(app.fact_export, "SHEET_ROWS", 4)
    book = workbook(export(client, three, format="xlsx"))
    left_out = "Excel holds 4 rows a sheet: the rest are left out. The CSV file holds them."
    rows = list(book["Documents"].iter_rows(values_only=True))
    assert [row[0] for row in rows] == ["Document", "acme-2.pdf", "acme-10.pdf", left_out]
    assert len(list(book["Facts"].iter_rows())) == 4
    # The CSV has no such bound.
    assert len(read_csv(export(client, three))) == 3


def test_each_download_is_a_row_of_the_audit_log_without_a_value(client, session, three):
    read_csv(export(client, three))
    workbook(
        export(
            client,
            three,
            format="xlsx",
            sort="total_amount",
            order="desc",
            filter=["total_amount:min:100", "vendor_name:contains:Acme"],
            q="INV",
            unchecked="true",
        )
    )
    assert exported(session) == [
        {"kind": "invoice", "format": "csv"},
        {
            "kind": "invoice",
            "format": "xlsx",
            "sort": "total_amount",
            "order": "desc",
            "conditions": ["total_amount:min", "vendor_name:contains"],
            "words": True,
            "unchecked": True,
        },
    ]


def test_what_the_table_refuses_the_export_refuses(client, session, three):
    assert export(client, three, kind="receipt", format="pdf").status_code == 422
    assert export(client, three, kind="purchase order and more").status_code == 404
    refused = export(client, three, sort="colour")
    assert (refused.status_code, refused.json()["detail"]) == (
        422,
        "The Invoice template has no field named colour.",
    )
    refused = export(client, three, filter=["total_amount:min:lots"])
    assert (refused.status_code, refused.json()["detail"]) == (
        422,
        "Compare total_amount with a number.",
    )
    assert exported(session) == []


@pytest.mark.usefixtures("one_minute")
def test_downloads_are_held_to_a_rate_of_their_own(client, monkeypatch, three):
    monkeypatch.setattr(get_settings(), "rate_exports", 1)
    assert export(client, three).status_code == 200
    refused = export(client, three, format="xlsx")
    assert refused.status_code == 429
    assert "downloads of a facts table" in refused.json()["detail"]
    # The table itself is a search.
    table(client, three)


def test_a_viewer_and_a_program_download_the_table(client, account, three, make_account, act_as):
    api = program(client, make_key(client)["key"])
    rows = read_csv(api.get(f"/api/projects/{three}/fact-table/export", params={"kind": "invoice"}))
    assert len(rows) == 3
    act_as(make_account("vic@example.com", account.organization, role="viewer"))
    assert len(read_csv(export(client, three))) == 3
