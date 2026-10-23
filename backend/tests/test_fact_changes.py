"""What a document's last reading changed (app/fact_changes.py): a template's documents read
again (`POST /api/projects/{id}/templates/read-again`), and what each reading changed of the
facts of the template's fields (`GET /api/projects/{id}/fact-changes`), a member's check the new
reading disagrees with first."""

import datetime as dt

import pytest
from sqlalchemy import select, update

from app.fact_changes import MAX_VALUES, Read, changes_of
from app.fact_checks import Reading
from app.indexing.pipeline import process_document
from app.models import Document, FactChange
from tests.test_api import create_project
from tests.test_fact_checks import check, priced, read_again, read_contract
from tests.test_fact_table import invoice
from tests.test_templates import fact, listed, turn_on

# A project's own contract template: the built-in one's party, price and governing law, and the
# liability cap its members added.
CONTRACT = {
    "doc_type": "contract",
    "title": "Supply contract",
    "fields": [
        {"name": "party", "type": "text", "required": True},
        {"name": "price", "type": "money"},
        {"name": "governing_law", "type": "text"},
        {"name": "liability_cap", "type": "text"},
    ],
}


@pytest.fixture
def templates_on(monkeypatch):
    turn_on(monkeypatch)


def queue(client, project_id: int, **body) -> int:
    response = client.post(f"/api/projects/{project_id}/templates/read-again", json=body)
    assert response.status_code == 202, response.text
    return response.json()["queued"]


def changes(client, project_id: int, kind: str, **params) -> dict:
    response = client.get(
        f"/api/projects/{project_id}/fact-changes", params={"kind": kind, **params}
    )
    assert response.status_code == 200, response.text
    return response.json()


def counts(client, project_id: int, kind: str) -> tuple[int, int, int, int]:
    """A listed template's documents, those of its kind read for none, those being read and
    those changed at their last reading."""
    [template] = [t for t in listed(client, project_id) if t["doc_type"] == kind]
    return template["documents"], template["matching"], template["reading"], template["changed"]


def fields_of(shown: dict) -> list[tuple]:
    [document] = shown["documents"]
    return [
        (field["name"], field["before"], field["after"], field["checked"])
        for field in document["fields"]
    ]


def test_the_routes_are_behind_the_flag(client):
    project = create_project(client)["id"]
    assert (
        client.get(f"/api/projects/{project}/fact-changes", params={"kind": "invoice"}).status_code
        == 404
    )
    response = client.post(
        f"/api/projects/{project}/templates/read-again", json={"kind": "invoice"}
    )
    assert response.status_code == 404


def test_a_template_changed_and_its_documents_read_again_show_what_changed(
    client, session, services, sample_pdf, templates_on
):
    project, document = read_contract(client, services, sample_pdf)
    # The first reading had none before it to change.
    assert changes(client, project, "contract")["read"] == 0
    assert counts(client, project, "contract") == (1, 0, 0, 0)
    table = client.get(f"/api/projects/{project}/fact-table", params={"kind": "contract"})
    [row] = table.json()["rows"]
    check(client, row["cells"]["party"][0]["id"])
    check(client, row["cells"]["price"][0]["id"], "1250")

    # The members make the template their own, a field added, and have it read again: the model
    # reads another price and the cap.
    made = client.post(f"/api/projects/{project}/templates", json=CONTRACT)
    assert made.status_code == 201, made.text
    services.llm.more_facts = [
        priced("EUR 1,300.00", 1300),
        fact("party", "Acme Corp (Supplier)"),
        fact("liability_cap", "EUR 250,000"),
    ]
    assert queue(client, project, kind="contract") == 1
    waiting = changes(client, project, "contract")
    assert (waiting["reading"], waiting["read"]) == (1, 0)
    assert waiting["template"]["title"] == "Supply contract"
    assert counts(client, project, "contract") == (1, 0, 1, 0)
    process_document(document, services)

    shown = changes(client, project, "contract")
    assert (shown["reading"], shown["read"], shown["changed"], shown["unheld"]) == (0, 1, 1, 1)
    [read] = shown["documents"]
    assert (read["document_id"], read["filename"]) == (document, "msa.pdf")
    assert (read["template_before"], read["template_after"]) == ("contract", "contract")
    # The member's corrected price, which the new reading disagrees with, first; then the cap
    # found. The party read as checked and the law read alike are not named.
    assert fields_of(shown) == [
        ("price", ["1,250.00 EUR"], ["EUR 1,300.00"], True),
        ("liability_cap", [], ["EUR 250,000"], False),
    ]
    assert counts(client, project, "contract") == (1, 0, 0, 1)

    # Read again alike, it changed nothing: still read, no longer listed.
    read_again(client, services, document)
    shown = changes(client, project, "contract")
    assert (shown["read"], shown["changed"], shown["unheld"], shown["documents"]) == (1, 0, 0, [])
    assert counts(client, project, "contract") == (1, 0, 0, 0)


def test_a_document_read_for_another_kind_is_listed_under_both(
    client, session, services, sample_pdf, templates_on
):
    project, document = read_contract(client, services, sample_pdf)
    made = client.post(f"/api/projects/{project}/templates", json=CONTRACT).json()
    # Its kind changed, the documents read for the kind before are read again for it.
    changed = client.put(f"/api/templates/{made['id']}", json={**CONTRACT, "doc_type": "agreement"})
    assert changed.status_code == 200, changed.text
    assert queue(client, project, kind="agreement") == 0
    assert queue(client, project, kind="agreement", was="contract") == 1
    services.llm.template = "agreement"
    services.llm.more_facts.append(fact("signature_date", "2 March 2024", "date"))
    process_document(document, services)

    for kind in ("agreement", "contract"):
        shown = changes(client, project, kind)
        [read] = shown["documents"]
        assert (read["template_before"], read["template_after"]) == ("contract", "agreement")
        assert shown["changed"] == 1
    # The fields of both, as they stand: a date the built-in contract asks for found, which the
    # agreement does not; the rest read alike.
    assert [(field["name"], field["after"]) for field in read["fields"]] == [
        ("signature_date", ["2 March 2024"])
    ]
    assert counts(client, project, "agreement")[3] == counts(client, project, "contract")[3] == 1
    assert changes(client, project, "agreement")["template"]["doc_type"] == "agreement"
    assert changes(client, project, "receipt")["documents"] == []


def test_nothing_is_kept_for_a_first_reading_or_one_after_the_outline(
    client, session, services, sample_pdf, templates_on
):
    project, document = read_contract(client, services, sample_pdf)
    rows = select(FactChange.document_id)
    assert session.scalars(rows).all() == []
    read_again(client, services, document)
    assert session.scalars(rows).all() == [document]
    # The outline read again deletes the facts and what their reading changed: the reading
    # after it has nothing to compare with.
    read_again(client, services, document, "structure")
    session.expire_all()
    assert session.scalars(rows).all() == []
    assert changes(client, project, "contract")["read"] == 0


def test_reading_again_takes_the_kind_s_documents_alone(client, session, templates_on):
    project = create_project(client)["id"]
    read_for = invoice(session, project, "read-for.pdf", invoice_number="INV-1")
    before = invoice(session, project, "before.pdf", invoice_number="INV-2")
    invoice(session, project, "contract.pdf", kind="contract", party="Acme")
    trashed = invoice(session, project, "trashed.pdf", invoice_number="INV-3")
    busy = invoice(session, project, "busy.pdf", invoice_number="INV-4")
    flagged = invoice(session, project, "flagged.pdf", invoice_number="INV-5")
    # One read before the template was made, of its kind; one in the trash, one being indexed
    # and one quarantined.
    session.execute(update(Document).where(Document.id == before).values(template=None))
    session.execute(
        update(Document).where(Document.id == trashed).values(deleted_at=dt.datetime.now(dt.UTC))
    )
    session.execute(update(Document).where(Document.id == busy).values(status="processing"))
    session.execute(update(Document).where(Document.id == flagged).values(status="quarantined"))
    session.commit()
    assert counts(client, project, "invoice")[:2] == (3, 1)

    assert queue(client, project, kind="Invoice") == 2
    queued = session.scalars(
        select(Document.filename).where(Document.status == "queued").order_by(Document.filename)
    ).all()
    assert queued == ["before.pdf", "read-for.pdf"]
    [document] = session.scalars(select(Document).where(Document.id == read_for))
    session.refresh(document)
    assert document.stages["enrich"]["status"] == "pending"
    assert counts(client, project, "invoice")[2] == 2
    # A kind the template was for before takes those read for it alone.
    assert queue(client, project, was="invoice") == 1
    response = client.post(f"/api/projects/{project}/templates/read-again", json={})
    assert response.status_code == 400


def test_a_field_of_many_values_names_a_few_and_counts_the_rest():
    def read(name: str, value: str, number: float | None = None, checked=False) -> Read:
        return Read(name, Reading(value, number, None, "EUR" if number else None), checked)

    lines = [read("line_item", f"Line {n}", float(n)) for n in range(20)]
    # The same amount written another way is no change; another amount is.
    before = [read("total_amount", "EUR 1,250.00", 1250.0), *lines[:8]]
    after = [read("total_amount", "1,250.00 EUR", 1250.0), *lines[1:20]]
    [change] = changes_of(before, after, ["total_amount", "line_item"])
    assert change["name"] == "line_item"
    assert (change["before"], change["before_count"]) == (["Line 0"], 1)
    assert change["after"] == [f"Line {n}" for n in range(8, 8 + MAX_VALUES)]
    assert change["after_count"] == 12 and not change["checked"]

    # A member's value gone comes first, whatever the order of the fields.
    before = [read("invoice_number", "INV-1"), read("total_amount", "90", 90.0, checked=True)]
    after = [read("invoice_number", "INV-01"), read("total_amount", "95", 95.0)]
    assert [
        (c["name"], c["checked"])
        for c in changes_of(before, after, ["invoice_number", "total_amount"])
    ] == [
        ("total_amount", True),
        ("invoice_number", False),
    ]


def test_the_pages_of_changes_go_on_whatever_comes_between(client, session, templates_on):
    project = create_project(client)["id"]
    other = create_project(client)["id"]
    now = dt.datetime.now(dt.UTC)
    made = []
    for n in range(6):
        document = invoice(session, project, f"inv-{n}.pdf", invoice_number=f"INV-{n}")
        session.add(
            FactChange(
                document_id=document,
                project_id=project,
                read_at=now - dt.timedelta(minutes=n),
                template_before="invoice",
                template_after="invoice",
                changes=[],
                # One read alike, one whose member's check the reading disagrees with.
                changed=n != 3,
                checks_unheld=1 if n == 4 else 0,
            )
        )
        made.append(document)
    elsewhere = invoice(session, other, "elsewhere.pdf", invoice_number="INV-9")
    session.add(
        FactChange(document_id=elsewhere, project_id=other, template_after="invoice", changed=True)
    )
    session.execute(update(Document).where(Document.id == made[5]).values(deleted_at=now))
    session.commit()

    first = changes(client, project, "invoice", limit=2)
    assert (first["read"], first["changed"], first["unheld"]) == (5, 4, 1)
    shown = [d["filename"] for d in first["documents"]]
    after = first["next_after"]
    # A reading made between the pages, the newest, comes before where the next one goes on.
    session.execute(
        update(FactChange)
        .where(FactChange.document_id == made[3])
        .values(changed=True, read_at=now + dt.timedelta(minutes=1))
    )
    session.commit()
    while after:
        page = changes(client, project, "invoice", limit=2, after=after)
        shown += [d["filename"] for d in page["documents"]]
        after = page["next_after"]
    assert shown == ["inv-4.pdf", "inv-0.pdf", "inv-1.pdf", "inv-2.pdf"]

    response = client.get(
        f"/api/projects/{project}/fact-changes", params={"kind": "invoice", "after": "nonsense"}
    )
    assert response.status_code == 422
    assert response.json()["detail"].startswith("This list's page is not one it gave.")
