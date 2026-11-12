"""The facts table (app/fact_table.py, `GET /api/projects/{id}/fact-table`): a project's documents
of one kind side by side, a row for each document read for the kind's template and a column for
each of its fields, sorted, kept by conditions and paged."""

import datetime as dt
import uuid
from base64 import urlsafe_b64encode

import pytest

from app.fact_table import BAD_CURSOR
from app.indexing.pipeline import process_document
from app.models import Document, Fact
from tests.test_api import create_project, upload
from tests.test_templates import make

Value = str | tuple[str, float | dt.date]


def invoice(session, project_id: int, filename: str, kind: str = "invoice", **fields) -> int:
    """A document read for the `kind` template, with a fact for each value of each field given:
    a text as it is, a number, an amount or a date as its written form and its typed value. The
    facts are in the order given, one block apart."""
    document = Document(
        project_id=project_id,
        filename=filename,
        mime_type="text/plain",
        size_bytes=1,
        sha256=uuid.uuid4().hex,
        storage_key="",
        status="ready",
        stages={},
        doc_type=kind,
        template=kind,
    )
    session.add(document)
    session.flush()
    block = 0
    for name, values in fields.items():
        for value in values if isinstance(values, list) else [values]:
            written, typed = value if isinstance(value, tuple) else (value, None)
            dated = isinstance(typed, dt.date)
            session.add(
                Fact(
                    document_id=document.id,
                    project_id=project_id,
                    name=name,
                    value_type="date" if dated else "money" if typed is not None else "text",
                    value=written,
                    number=None if dated else typed,
                    date=typed if dated else None,
                    quote=f"“{written}”",
                    page=1,
                    block_ordinal=block,
                )
            )
            block += 1
    session.commit()
    return document.id


def table(client, project_id: int, kind: str = "invoice", **params) -> dict:
    response = client.get(f"/api/projects/{project_id}/fact-table", params={"kind": kind, **params})
    assert response.status_code == 200, response.text
    return response.json()


def names(shown: dict) -> list[str]:
    return [row["filename"] for row in shown["rows"]]


def refusal(client, project_id: int, **params) -> tuple[int, str]:
    response = client.get(
        f"/api/projects/{project_id}/fact-table", params={"kind": "invoice", **params}
    )
    return response.status_code, response.json()["detail"]


@pytest.fixture
def three(client, session) -> int:
    return invoices(client, session)


def invoices(client, session) -> int:
    """A project of three invoices, a contract, an invoice in the trash and one read for no
    template, and an invoice of another project."""
    project = create_project(client)["id"]
    invoice(
        session,
        project,
        "acme-10.pdf",
        invoice_number="INV-10",
        issue_date=("1 March 2026", dt.date(2026, 3, 1)),
        total_amount=("EUR 1,200.00", 1200.0),
        line_item=[("Bolts, 40 x 10.00", 400.0), ("Nuts, 80 x 10.00", 800.0)],
        notes="Not a field of the template",
    )
    invoice(
        session,
        project,
        "acme-2.pdf",
        invoice_number="INV-2",
        issue_date=("12 February 2026", dt.date(2026, 2, 12)),
        total_amount=("EUR 90.00", 90.0),
        line_item=("Washers, 60 x 10.00", 600.0),
        vendor_name="Acme Industrial Supplies Ltd.",
    )
    invoice(
        session,
        project,
        "globex.pdf",
        invoice_number="G-7",
        vendor_name="Globex Ltd.",
    )
    invoice(session, project, "msa.pdf", kind="contract", party="Acme (Supplier)")
    gone = invoice(session, project, "binned.pdf", invoice_number="INV-9")
    session.get(Document, gone).deleted_at = dt.datetime.now(dt.UTC)
    unread = invoice(session, project, "unread.pdf", invoice_number="INV-1")
    session.get(Document, unread).template = None
    session.commit()
    invoice(session, create_project(client, "Other")["id"], "other.pdf", invoice_number="INV-1")
    return project


def test_a_row_for_each_document_read_for_the_template_and_a_column_for_each_field(client, three):
    shown = table(client, three)
    assert shown["total"] == 3 and shown["next_after"] is None
    # The live documents of the kind read for its template, by name in the natural order.
    assert names(shown) == ["acme-2.pdf", "acme-10.pdf", "globex.pdf"]
    assert shown["template"]["doc_type"] == "invoice" and shown["template"]["built_in"]
    assert shown["template"]["documents"] == 3
    assert [field["name"] for field in shown["template"]["fields"]][:3] == [
        "invoice_number",
        "issue_date",
        "due_date",
    ]
    first = shown["rows"][1]
    # The template's fields alone, each fact with where it was found, a field's in order.
    assert set(first["cells"]) == {"invoice_number", "issue_date", "total_amount", "line_item"}
    assert [fact["value"] for fact in first["cells"]["line_item"]] == [
        "Bolts, 40 x 10.00",
        "Nuts, 80 x 10.00",
    ]
    total = first["cells"]["total_amount"][0]
    assert (total["number"], total["quote"], total["page"], total["block_ordinal"]) == (
        1200.0,
        "“EUR 1,200.00”",
        1,
        2,
    )
    assert names(table(client, three, "contract")) == ["msa.pdf"]


def test_a_document_indexed_for_a_template_is_a_row_whose_facts_open_their_passage(
    client, session, services, sample_pdf
):
    project = create_project(client)
    document = upload(client, project["id"], sample_pdf)["documents"][0]
    services.llm.template = "contract"
    process_document(document["id"], services)

    [row] = table(client, project["id"], "Contract")["rows"]
    assert row["document_id"] == document["id"]
    assert row["title"] == "Master Services Agreement"
    [notice] = row["cells"]["notice_period"]
    assert notice["passage_id"] is not None and notice["block_ordinal"] is not None
    passage = client.get(f"/api/passages/{notice['passage_id']}").json()
    assert passage["block_start"] <= notice["block_ordinal"] < passage["block_end"]
    assert row["cells"]["effective_date"][0]["date"] == "2024-03-01"


def test_rows_are_sorted_by_a_field_s_typed_value_those_without_it_last(client, three):
    def by(field, order="asc"):
        return names(table(client, three, sort=field, order=order))

    # An amount by its number, not by its words; a document without the field goes last.
    assert by("total_amount") == ["acme-2.pdf", "acme-10.pdf", "globex.pdf"]
    assert by("total_amount", "desc") == ["acme-10.pdf", "acme-2.pdf", "globex.pdf"]
    assert by("issue_date") == ["acme-2.pdf", "acme-10.pdf", "globex.pdf"]
    # A text by its words in the natural order: INV-2 before INV-10.
    assert by("invoice_number") == ["globex.pdf", "acme-2.pdf", "acme-10.pdf"]
    assert by("invoice_number", "desc") == ["acme-10.pdf", "acme-2.pdf", "globex.pdf"]
    # A field of several facts by its first, as the cell shows it first: 400 before 600,
    # though the same invoice's second line is 800.
    assert by("line_item") == ["acme-10.pdf", "acme-2.pdf", "globex.pdf"]
    assert by(None, "desc") == ["globex.pdf", "acme-10.pdf", "acme-2.pdf"]


def test_conditions_keep_the_rows_whose_facts_meet_them(client, three):
    def kept(*conditions, q=""):
        return names(table(client, three, filter=list(conditions), q=q))

    assert kept("vendor_name:has") == ["acme-2.pdf", "globex.pdf"]
    assert kept("vendor_name:lacks") == ["acme-10.pdf"]
    assert kept("vendor_name:contains:ACME") == ["acme-2.pdf"]
    assert kept("total_amount:min:100") == ["acme-10.pdf"]
    assert kept("total_amount:max:100") == ["acme-2.pdf"]
    # Any of a field's facts: the second line is over 700, the first under it.
    assert kept("line_item:min:700") == ["acme-10.pdf"]
    assert kept("issue_date:min:2026-03-01") == ["acme-10.pdf"]
    assert kept("issue_date:max:2026-02-28", "invoice_number:contains:2") == ["acme-2.pdf"]
    # Words in the document's name or in any of its facts of the template's fields.
    assert kept(q="globex") == ["globex.pdf"]
    assert kept(q="nuts") == ["acme-10.pdf"]
    assert kept(q="not a field") == []
    # A pattern's characters are words like any other.
    assert kept(q="%") == []
    shown = table(client, three, filter=["vendor_name:has"])
    assert (shown["total"], shown["template"]["documents"]) == (2, 3)


def test_what_the_table_cannot_take_is_refused_in_words(client, three):
    assert refusal(client, three, filter="iban:has") == (
        422,
        "The Invoice template has no field named iban.",
    )
    assert refusal(client, three, filter="total_amount:above:1")[0] == 422
    assert refusal(client, three, filter="vendor_name:min:1") == (
        422,
        "vendor_name is a text: only a number, an amount or a date is compared with a value.",
    )
    assert refusal(client, three, filter="total_amount:min:lots") == (
        422,
        "Compare total_amount with a number.",
    )
    assert refusal(client, three, filter="total_amount:min:nan")[0] == 422
    assert refusal(client, three, filter="issue_date:max:March") == (
        422,
        "Compare issue_date with a date written as 2026-03-01.",
    )
    assert refusal(client, three, filter="vendor_name:contains: ")[0] == 422
    assert refusal(client, three, filter=["vendor_name:has"] * 11)[0] == 422
    assert refusal(client, three, sort="iban") == (
        422,
        "The Invoice template has no field named iban.",
    )
    assert refusal(client, three, kind="letter") == (
        404,
        "This project has no template for letter documents.",
    )
    assert refusal(client, three, after="nonsense") == (422, BAD_CURSOR)
    # A cursor of another sort's type is refused rather than compared.
    wrong = urlsafe_b64encode(b'["INV-2", "acme-2.pdf", 1]').decode()
    assert refusal(client, three, sort="total_amount", after=wrong)[0] == 422


@pytest.mark.parametrize(
    ("sort", "order"),
    [(None, "asc"), ("total_amount", "asc"), ("total_amount", "desc"), ("vendor_name", "desc")],
)
def test_the_pages_hold_every_row_once_whatever_the_order(client, session, three, sort, order):
    # Ties and missing values among them: two of one amount, two of none.
    invoice(session, three, "b.pdf", total_amount=("EUR 90.00", 90.0), vendor_name="Beta")
    invoice(session, three, "c.pdf", vendor_name="Gamma")
    params = {"order": order, "limit": 2, **({"sort": sort} if sort else {})}
    whole = names(table(client, three, **{**params, "limit": 100}))
    paged, after = [], None
    while True:
        shown = table(client, three, **params, **({"after": after} if after else {}))
        paged += names(shown)
        after = shown["next_after"]
        if after is None:
            break
        if len(paged) == 2:
            # A row that comes before the cursor meanwhile moves nothing after it.
            invoice(session, three, "0-first.pdf", total_amount=("EUR 1.00", 1.0), vendor_name="A")
    assert [name for name in paged if name != "0-first.pdf"] == [
        name for name in whole if name != "0-first.pdf"
    ]
    assert len(paged) == len(set(paged))
    assert shown["total"] in (5, 6)


def test_the_columns_are_the_project_s_own_template_once_it_has_one(client, session, three):
    invoice(session, three, "with-iban.pdf", invoice_number="INV-3", iban="DE89 3704")
    assert "iban" not in {name for row in table(client, three)["rows"] for name in row["cells"]}
    made = make(client, three)
    shown = table(client, three, sort="iban")
    assert shown["template"]["id"] == made["id"]
    assert [field["name"] for field in shown["template"]["fields"]] == [
        "invoice_number",
        "iban",
        "total_amount",
    ]
    assert names(shown)[0] == "with-iban.pdf"
    assert shown["rows"][0]["cells"]["iban"][0]["value"] == "DE89 3704"


def test_a_viewer_reads_the_table(client, account, make_account, act_as, three):
    act_as(make_account("vic@example.com", account.organization, role="viewer"))
    assert table(client, three)["total"] == 3
