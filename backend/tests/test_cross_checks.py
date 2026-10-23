"""Cross-checks (app/cross_checks.py, app/api/cross_checks.py): a project's rules that compare the
facts of its documents with one another, an invoice's prices with the price list's, its
quantities with its delivery notes', its due date with its issue date, and what each finds, with
the facts on both sides."""

import datetime as dt
import uuid
from base64 import urlsafe_b64encode
from dataclasses import replace

import pytest

from app.cross_checks import (
    BAD_CURSOR,
    MAX_CROSS_CHECKS,
    SUGGESTED,
    item_words,
    key_of,
    numbers_in,
    same_numbers,
)
from app.flags import EXTRACTION_TEMPLATES, FLAGS
from app.models import Document, Fact
from tests.test_api import create_project
from tests.test_audit import rows as audit_rows
from tests.test_templates import make, turn_on

Value = str | tuple[str, float | dt.date] | tuple[str, float, str]

# A rule of the project's own invoice template (INVOICE in tests/test_templates.py) against the
# built-in purchase order, which the authorization tests make in their world.
CROSS_CHECK = {
    "title": "Total within its order",
    "left_kind": "invoice",
    "left_field": "total_amount",
    "right_kind": "purchase order",
    "right_field": "total_amount",
    "pairing": "key",
    "left_key": "invoice_number",
    "right_key": "po_number",
    "comparison": "at_most",
}

PRICES = {
    "title": "Prices as on the price list",
    "left_kind": "invoice",
    "left_field": "unit_price",
    "right_kind": "price list",
    "right_field": "unit_price",
    "pairing": "every",
    "by_item": True,
    "comparison": "equal",
}

DELIVERED = {
    "title": "Quantities no more than delivered",
    "left_kind": "invoice",
    "left_field": "quantity",
    "right_kind": "delivery note",
    "right_field": "delivered_item",
    "pairing": "key",
    "left_key": "po_number",
    "right_key": "po_number",
    "by_item": True,
    "comparison": "at_most",
    "summed": True,
}

DUE = {
    "title": "Due within 30 days of issue",
    "left_kind": "invoice",
    "left_field": "due_date",
    "right_kind": "invoice",
    "right_field": "issue_date",
    "pairing": "same",
    "comparison": "at_most",
    "margin": 30,
}

TERMS = {
    "title": "Payment terms as in the contract",
    "left_kind": "invoice",
    "left_field": "payment_terms",
    "right_kind": "contract",
    "right_field": "payment_terms",
    "pairing": "every",
    "comparison": "same_numbers",
}


@pytest.fixture(autouse=True)
def templates_on(monkeypatch):
    turn_on(monkeypatch)


def read(session, project_id: int, filename: str, kind: str, **fields) -> int:
    """A document read for the `kind` template, with a fact for each value of each field given: a
    text as it is, a number or a date as its written form and its typed value, an amount with its
    currency third. The facts are in the order given, one block apart."""
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
            written, typed, currency = (
                (*value, None)[:3] if isinstance(value, tuple) else (value, None, None)
            )
            dated = isinstance(typed, dt.date)
            kind_of_value = (
                "date" if dated else "text" if typed is None else "money" if currency else "number"
            )
            session.add(
                Fact(
                    document_id=document.id,
                    project_id=project_id,
                    name=name,
                    value_type=kind_of_value,
                    value=written,
                    number=None if dated else typed,
                    date=typed if dated else None,
                    currency=currency,
                    quote=f"“{written}”",
                    page=1,
                    block_ordinal=block,
                )
            )
            block += 1
    session.commit()
    return document.id


def made(client, project_id: int, body: dict) -> dict:
    response = client.post(f"/api/projects/{project_id}/cross-checks", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def listed(client, project_id: int) -> dict:
    response = client.get(f"/api/projects/{project_id}/cross-checks")
    assert response.status_code == 200, response.text
    return response.json()


def found(client, check_id: int, **params) -> dict:
    response = client.get(f"/api/cross-checks/{check_id}/findings", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def refused(client, project_id: int, body: dict) -> tuple[int, str]:
    response = client.post(f"/api/projects/{project_id}/cross-checks", json=body)
    return response.status_code, response.json()["detail"]


def shown(finding: dict) -> tuple:
    """A finding in a few words: the document, why, the value checked and those compared."""
    return (
        finding["filename"],
        finding["reason"],
        finding["left"]["value"] if finding["left"] else None,
        [fact["value"] for fact in finding["right"]],
    )


def test_an_invoice_s_prices_are_compared_line_by_line_with_the_price_list(client, session):
    project = create_project(client)["id"]
    read(
        session,
        project,
        "inv-1.pdf",
        "invoice",
        unit_price=[
            ("Hex bolts M10 (pallet): 185.00", 185.0, "EUR"),
            ("Pipe fittings 1in (pallet): 245.00", 245.0, "EUR"),
            ("Freight to Rotterdam: 90.00", 90.0, "EUR"),
        ],
    )
    prices = read(
        session,
        project,
        "prices.csv",
        "price list",
        unit_price=[
            ("Hex bolts M10 (FB-M10): 185.00 per pallet", 185.0, "EUR"),
            ("Washers M10 (WS-M10): 62.50 per pallet", 62.5, "EUR"),
            ("Pipe fittings 1in (PF-1IN): 240.00 per pallet", 240.0, "EUR"),
        ],
    )
    check = made(client, project, PRICES)
    assert check["counts"] == {"differs": 1, "missing": 1, "holds": 1}
    assert check["problem"] is None
    assert (check["left_type"], check["right_type"]) == ("money", "money")

    findings = found(client, check["id"])
    assert [shown(finding) for finding in findings["findings"]] == [
        (
            "inv-1.pdf",
            "differs",
            "Pipe fittings 1in (pallet): 245.00",
            ["Pipe fittings 1in (PF-1IN): 240.00 per pallet"],
        ),
        ("inv-1.pdf", "no_item", "Freight to Rotterdam: 90.00", []),
        (
            "inv-1.pdf",
            "holds",
            "Hex bolts M10 (pallet): 185.00",
            ["Hex bolts M10 (FB-M10): 185.00 per pallet"],
        ),
    ]
    differing = findings["findings"][0]
    assert differing["outcome"] == "differs"
    assert differing["difference"] == 5.0
    # Each side cites where it was found.
    assert differing["left"]["quote"] == "“Pipe fittings 1in (pallet): 245.00”"
    other = differing["right"][0]
    assert (other["document_id"], other["document_title"], other["page"]) == (
        prices,
        "prices.csv",
        1,
    )
    assert other["block_ordinal"] == 2
    assert differing["partners"] == [{"document_id": prices, "title": "prices.csv"}]
    assert findings["total"] == 3

    only = found(client, check["id"], outcome="holds")
    assert [shown(finding)[1] for finding in only["findings"]] == ["holds"]
    assert only["total"] == 1
    assert only["check"]["counts"] == {"differs": 1, "missing": 1, "holds": 1}


def test_quantities_are_held_to_what_the_delivery_notes_of_the_order_say_was_delivered(
    client, session
):
    project = create_project(client)["id"]
    read(
        session,
        project,
        "inv-1.pdf",
        "invoice",
        po_number="PO-7701",
        quantity=[("Hex bolts M10 (pallet): 30", 30.0), ("Washers M10 (pallet): 15", 15.0)],
    )
    read(
        session,
        project,
        "inv-2.pdf",
        "invoice",
        po_number="PO-7708",
        quantity=("Pipe fittings 1in (pallet): 25", 25.0),
    )
    read(session, project, "inv-3.pdf", "invoice", quantity=("Stretch film: 40", 40.0))
    # Two deliveries for one order, its number written another way on each, and one of another.
    read(
        session,
        project,
        "dn-1.pdf",
        "delivery note",
        po_number="po 7701",
        delivered_item=[("Hex bolts M10 (pallet): 20", 20.0), ("Washers M10: 10", 10.0)],
    )
    read(
        session,
        project,
        "dn-2.pdf",
        "delivery note",
        po_number="PO7701",
        delivered_item=("Hex bolts M10 (pallet): 10", 10.0),
    )
    read(session, project, "dn-3.pdf", "delivery note", po_number="PO-9999")
    check = made(client, project, DELIVERED)

    findings = found(client, check["id"])["findings"]
    assert [shown(finding) for finding in findings] == [
        ("inv-1.pdf", "differs", "Washers M10 (pallet): 15", ["Washers M10: 10"]),
        ("inv-2.pdf", "no_partner", "Pipe fittings 1in (pallet): 25", []),
        ("inv-3.pdf", "no_key", "Stretch film: 40", []),
        (
            "inv-1.pdf",
            "holds",
            "Hex bolts M10 (pallet): 30",
            ["Hex bolts M10 (pallet): 20", "Hex bolts M10 (pallet): 10"],
        ),
    ]
    # Added up, the deliveries hold the bolts billed; the washers are five more than delivered.
    assert findings[0]["difference"] == 5.0
    assert findings[3]["difference"] == 0.0
    assert [partner["title"] for partner in findings[0]["partners"]] == ["dn-1.pdf", "dn-2.pdf"]


def test_a_date_is_compared_with_another_of_the_same_document(client, session):
    project = create_project(client)["id"]
    read(
        session,
        project,
        "on-time.pdf",
        "invoice",
        issue_date=("2026-06-30", dt.date(2026, 6, 30)),
        due_date=("2026-07-30", dt.date(2026, 7, 30)),
    )
    read(
        session,
        project,
        "late.pdf",
        "invoice",
        issue_date=("2026-06-30", dt.date(2026, 6, 30)),
        due_date=("2026-08-14", dt.date(2026, 8, 14)),
    )
    read(
        session, project, "undated.pdf", "invoice", issue_date=("2026-06-30", dt.date(2026, 6, 30))
    )
    read(
        session,
        project,
        "unread.pdf",
        "invoice",
        issue_date=("2026-06-30", dt.date(2026, 6, 30)),
        due_date="end of next month",
    )
    check = made(client, project, DUE)

    findings = found(client, check["id"])["findings"]
    assert [(finding["filename"], finding["reason"]) for finding in findings] == [
        ("late.pdf", "differs"),
        ("undated.pdf", "no_value"),
        ("unread.pdf", "untyped"),
        ("on-time.pdf", "holds"),
    ]
    assert findings[0]["difference"] == 45
    assert findings[0]["right"][0]["value"] == "2026-06-30"
    # Within the same document, the document is its own partner.
    assert findings[0]["partners"] == [
        {"document_id": findings[0]["document_id"], "title": "late.pdf"}
    ]


def test_texts_are_compared_by_their_words_or_the_numbers_they_state(client, session):
    project = create_project(client)["id"]
    read(session, project, "a.pdf", "invoice", payment_terms="30 days")
    read(session, project, "b.pdf", "invoice", payment_terms="60 days net")
    read(
        session,
        project,
        "msa.pdf",
        "contract",
        payment_terms="payable within thirty (30) days of the invoice date",
    )
    check = made(client, project, TERMS)
    assert [shown(finding)[:2] for finding in found(client, check["id"])["findings"]] == [
        ("b.pdf", "differs"),
        ("a.pdf", "holds"),
    ]

    worded = made(client, project, {**TERMS, "title": "Same words", "comparison": "same_words"})
    assert worded["counts"] == {"differs": 2, "missing": 0, "holds": 0}


def test_amounts_in_two_currencies_differ_and_a_sum_needs_one(client, session):
    project = create_project(client)["id"]
    read(
        session,
        project,
        "inv.pdf",
        "invoice",
        unit_price=("Hex bolts M10: 185.00", 185.0, "USD"),
    )
    read(
        session,
        project,
        "prices.csv",
        "price list",
        unit_price=("Hex bolts M10: 185.00", 185.0, "EUR"),
    )
    check = made(client, project, PRICES)
    [finding] = found(client, check["id"])["findings"]
    assert (finding["outcome"], finding["reason"], finding["difference"]) == (
        "differs",
        "currency",
        None,
    )


def test_every_document_of_the_kind_is_compared_and_none_is_found_without_one(client, session):
    project = create_project(client)["id"]
    read(session, project, "a.pdf", "invoice", payment_terms="30 days")
    check = made(client, project, TERMS)
    [finding] = found(client, check["id"])["findings"]
    assert shown(finding) == ("a.pdf", "no_partner", "30 days", [])

    read(session, project, "msa.pdf", "contract", governing_law="England and Wales")
    [finding] = found(client, check["id"])["findings"]
    assert (finding["reason"], [p["title"] for p in finding["partners"]]) == (
        "no_partner_value",
        ["msa.pdf"],
    )


def test_the_trash_other_projects_and_other_kinds_are_left_out(client, session):
    project = create_project(client)["id"]
    elsewhere = create_project(client, "Elsewhere")["id"]
    read(session, project, "a.pdf", "invoice", unit_price=("Bolts: 10.00", 10.0))
    gone = read(session, project, "gone.pdf", "invoice", unit_price=("Bolts: 12.00", 12.0))
    read(session, project, "kept.csv", "price list", unit_price=("Bolts: 10.00", 10.0))
    trashed = read(session, project, "old.csv", "price list", unit_price=("Bolts: 9.00", 9.0))
    read(session, elsewhere, "theirs.csv", "price list", unit_price=("Bolts: 8.00", 8.0))
    read(session, project, "quote.pdf", "quotation", unit_price=("Bolts: 7.00", 7.0))
    for document_id in (gone, trashed):
        assert client.delete(f"/api/documents/{document_id}").status_code == 204
    check = made(client, project, PRICES)
    [finding] = found(client, check["id"])["findings"]
    assert shown(finding) == ("a.pdf", "holds", "Bolts: 10.00", ["Bolts: 10.00"])


def test_a_member_s_correction_is_what_is_compared(client, session):
    project = create_project(client)["id"]
    document = read(
        session, project, "inv.pdf", "invoice", unit_price=("Bolts: 12.00", 12.0, "EUR")
    )
    read(session, project, "prices.csv", "price list", unit_price=("Bolts: 10.00", 10.0, "EUR"))
    check = made(client, project, PRICES)
    assert check["counts"]["differs"] == 1

    fact = session.query(Fact).filter_by(document_id=document).one()
    corrected = client.put(f"/api/facts/{fact.id}/check", json={"value": "10"})
    assert corrected.status_code == 200, corrected.text
    [finding] = found(client, check["id"])["findings"]
    assert finding["reason"] == "holds"
    assert finding["left"]["check"]["corrected_from"] == "Bolts: 12.00"


def test_the_suggested_rules_are_those_the_templates_allow_and_the_project_lacks(client, session):
    project = create_project(client)["id"]
    suggested = listed(client, project)["suggestions"]
    assert [suggestion["title"] for suggestion in suggested] == [s.title for s in SUGGESTED]
    prices = next(s for s in suggested if s["title"] == "Prices as on the price list")
    assert (prices["left_type"], prices["right_type"]) == ("money", "money")

    # Added as offered, a suggestion is offered no more.
    body = {key: value for key, value in prices.items() if key not in ("left_type", "right_type")}
    made(client, project, body)
    after = listed(client, project)
    assert "Prices as on the price list" not in [s["title"] for s in after["suggestions"]]
    assert [check["title"] for check in after["checks"]] == ["Prices as on the price list"]
    assert after["max_checks"] == MAX_CROSS_CHECKS

    # A template of the project's own without a field a suggestion compares leaves it out.
    make(
        client,
        project,
        {
            "doc_type": "invoice",
            "title": "Invoice",
            "fields": [{"name": "total_amount", "type": "money"}],
        },
    )
    assert listed(client, project)["suggestions"] == []


@pytest.mark.parametrize(
    ("change", "sentence"),
    [
        ({"left_kind": "quotation"}, "This project has no template for quotation documents."),
        ({"right_field": "colour"}, "The Price list template has no field named colour."),
        (
            {"right_kind": "delivery note", "right_field": "delivery_date"},
            "unit_price and delivery_date are not both numbers, amounts or dates, which this "
            "compares.",
        ),
        (
            {"comparison": "same_words"},
            "unit_price and unit_price are not both texts, which this compares.",
        ),
        (
            {"pairing": "same"},
            "Within the same document, a field is compared with one of the same template.",
        ),
        ({"pairing": "key"}, "Name the field of each side whose values pair the documents."),
        (
            {"pairing": "key", "left_key": "po_number", "right_key": "po"},
            "The Price list template has no field named po.",
        ),
    ],
)
def test_a_rule_the_templates_cannot_work_out_is_refused(client, change, sentence):
    project = create_project(client)["id"]
    assert refused(client, project, {**PRICES, **change}) == (400, sentence)


def test_a_margin_and_a_sum_are_for_what_they_can_measure(client):
    project = create_project(client)["id"]
    assert refused(client, project, {**TERMS, "margin": 2}) == (
        400,
        "A margin is for numbers, amounts and dates.",
    )
    assert refused(client, project, {**DUE, "summed": True}) == (
        400,
        "Only numbers and amounts are added up.",
    )
    assert refused(client, project, {**DUE, "margin": 1.5}) == (
        400,
        "A margin between dates is a whole number of days, at most 3,660.",
    )
    assert refused(client, project, {**DUE, "right_field": "due_date"}) == (
        400,
        "Within the same document, compare the field with another one.",
    )


def test_a_project_holds_so_many_rules(client, monkeypatch):
    monkeypatch.setattr("app.api.cross_checks.MAX_CROSS_CHECKS", 2)
    project = create_project(client)["id"]
    made(client, project, PRICES)
    made(client, project, DUE)
    status, sentence = refused(client, project, TERMS)
    assert status == 409
    assert sentence.startswith("A project has at most")


def test_a_rule_whose_field_left_its_template_says_why_it_finds_nothing(client, session):
    project = create_project(client)["id"]
    template = make(
        client,
        project,
        {
            "doc_type": "invoice",
            "title": "Our invoice",
            "fields": [
                {"name": "issue_date", "type": "date"},
                {"name": "due_date", "type": "date"},
            ],
        },
    )
    read(
        session,
        project,
        "a.pdf",
        "invoice",
        issue_date=("2026-06-30", dt.date(2026, 6, 30)),
        due_date=("2026-07-30", dt.date(2026, 7, 30)),
    )
    check = made(client, project, DUE)
    assert check["counts"]["holds"] == 1

    changed = client.put(
        f"/api/templates/{template['id']}",
        json={
            "doc_type": "invoice",
            "title": "Our invoice",
            "fields": [{"name": "issue_date", "type": "date"}],
        },
    )
    assert changed.status_code == 200
    [shown_check] = listed(client, project)["checks"]
    assert shown_check["problem"] == "The Our invoice template has no field named due_date."
    assert (shown_check["counts"], shown_check["left_type"]) == (None, None)
    findings = found(client, check["id"])
    assert (findings["findings"], findings["check"]["problem"]) == ([], shown_check["problem"])


def test_a_rule_is_changed_whole_and_deleted_each_in_the_audit_log_without_values(client, session):
    project = create_project(client)["id"]
    check = made(client, project, {**PRICES, "title": "  Prices  ", "left_field": "Unit Price"})
    assert (check["title"], check["left_field"]) == ("Prices", "unit_price")

    unchanged = client.put(f"/api/cross-checks/{check['id']}", json={**PRICES, "title": "Prices"})
    assert unchanged.status_code == 200
    changed = client.put(
        f"/api/cross-checks/{check['id']}",
        json={**PRICES, "title": "Prices, within a cent", "margin": 0.01},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["margin"] == 0.01
    assert client.delete(f"/api/cross-checks/{check['id']}").status_code == 204
    assert client.get(f"/api/cross-checks/{check['id']}/findings").status_code == 404

    rows = [row for row in audit_rows(session) if row.action.startswith("cross_check_")]
    assert [row.action for row in rows] == [
        "cross_check_created",
        "cross_check_changed",
        "cross_check_deleted",
    ]
    assert rows[0].detail == {
        "name": "Prices",
        "left": "invoice.unit_price",
        "right": "price list.unit_price",
        "comparison": "equal",
    }
    assert rows[1].detail["changed"] == ["title", "comparison"]
    assert all(row.target_type == "cross_check" and row.project_id == project for row in rows)


def test_findings_are_paged_differing_first_and_a_page_not_given_is_refused(client, session):
    project = create_project(client)["id"]
    for number in (10, 2, 1):
        read(
            session,
            project,
            f"inv-{number}.pdf",
            "invoice",
            issue_date=("2026-06-30", dt.date(2026, 6, 30)),
            due_date=("2026-09-30", dt.date(2026, 9, 30))
            if number == 2
            else ("2026-07-01", dt.date(2026, 7, 1)),
        )
    check = made(client, project, DUE)
    first = found(client, check["id"], limit=2)
    assert [finding["filename"] for finding in first["findings"]] == ["inv-2.pdf", "inv-1.pdf"]
    assert first["total"] == 3
    second = found(client, check["id"], limit=2, after=first["next_after"])
    assert [finding["filename"] for finding in second["findings"]] == ["inv-10.pdf"]
    assert second["next_after"] is None

    bad = urlsafe_b64encode(b'["x", 1]').decode()
    response = client.get(f"/api/cross-checks/{check['id']}/findings", params={"after": bad})
    assert (response.status_code, response.json()["detail"]) == (422, BAD_CURSOR)


def test_the_rules_are_behind_the_flag(client, monkeypatch):
    project = create_project(client)["id"]
    monkeypatch.setitem(FLAGS, EXTRACTION_TEMPLATES, replace(FLAGS[EXTRACTION_TEMPLATES], share=0))
    assert client.get(f"/api/projects/{project}/cross-checks").status_code == 404


def test_a_line_s_item_is_its_words_without_its_figures():
    assert item_words("Hex bolts M10 (pallet): 40 x 185.00 = 7,400.00") == {
        "hex",
        "bolts",
        "m10",
        "pallet",
    }
    assert item_words("Stretch film (SF-01): EUR 38.00 per roll box") == {
        "stretch",
        "film",
        "sf",
        "roll",
        "box",
    }
    assert item_words("1,250.00") == frozenset()


def test_a_key_is_its_letters_and_digits():
    assert key_of("PO-7701") == key_of("po 7701") == key_of("PO7701") == "po7701"
    assert key_of("—") == ""


def test_numbers_are_read_whatever_sets_their_thousands_apart():
    assert numbers_in("payable within thirty (30) days") == {30}
    assert numbers_in("EUR 1,250.00 and 1.250,00 and 1,250 and 1.5% and 2.000.000") == {
        1250.0,
        1.5,
        2_000_000.0,
    }
    assert same_numbers("30 days", "Net 30")
    assert not same_numbers("30 days", "2/10 net 30")
    assert same_numbers("End of month", "end of  month")
