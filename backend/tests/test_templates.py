"""Extraction templates (app/extraction.py, app/api/templates.py): the fields a project's documents
of each kind are read for, the built-in ones and the project's own, and the enrich stage that
reads a document for the one it is."""

import json
from dataclasses import replace

import pytest
from sqlalchemy import select

from app.api import templates as routes
from app.extraction import BUILT_IN, kind_of, templates_for
from app.flags import EXTRACTION_TEMPLATES, FLAGS
from app.indexing import enrich
from app.indexing.enrich import ExtractedFact
from app.indexing.pipeline import process_document
from app.models import Document, Fact
from app.schemas import TemplateIn
from tests.test_api import create_project, upload
from tests.test_audit import actions
from tests.test_exports import archive_of, ask, run_export

BUILT_IN_KINDS = [
    "invoice",
    "contract",
    "purchase order",
    "receipt",
    "bank statement",
    "delivery note",
    "price list",
]

INVOICE = {
    "doc_type": "Invoice",
    "title": "Supplier invoice",
    "description": "An invoice from one of our suppliers.",
    "fields": [
        {
            "name": "Invoice Number",
            "type": "text",
            "required": True,
            "examples": ["INV-1", "INV-1"],
        },
        {"name": "IBAN", "type": "text", "description": "The account to pay into"},
        {"name": "total_amount", "type": "money", "required": True},
    ],
}


def turn_on(monkeypatch) -> None:
    """The feature on for every organisation, as once it is rolled out to all."""
    monkeypatch.setitem(
        FLAGS, EXTRACTION_TEMPLATES, replace(FLAGS[EXTRACTION_TEMPLATES], share=100)
    )


@pytest.fixture
def templates_on(monkeypatch):
    turn_on(monkeypatch)


def listed(client, project_id) -> list[dict]:
    response = client.get(f"/api/projects/{project_id}/templates")
    assert response.status_code == 200, response.text
    return response.json()["templates"]


def make(client, project_id, body=INVOICE) -> dict:
    response = client.post(f"/api/projects/{project_id}/templates", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def fact(name: str, value: str, type: str = "text", number: float | None = None) -> ExtractedFact:
    return ExtractedFact(
        name=name,
        value=value,
        type=type,
        number=number,
        date=None,
        currency=None,
        block=0,
        quote=value,
    )


def facts_of(session, document_id: int) -> dict[str, Fact]:
    found = session.scalars(select(Fact).where(Fact.document_id == document_id))
    return {row.name: row for row in found}


# The routes


def test_the_templates_are_hidden_while_the_flag_is_off(client):
    project = create_project(client)
    for method, path in (
        ("GET", f"/api/projects/{project['id']}/templates"),
        ("POST", f"/api/projects/{project['id']}/templates"),
        ("PUT", "/api/templates/1"),
        ("DELETE", "/api/templates/1"),
    ):
        assert client.request(method, path, json=INVOICE).status_code == 404, (method, path)


def test_a_project_starts_with_the_built_in_templates(client, templates_on):
    project = create_project(client)
    shown = listed(client, project["id"])
    assert [t["doc_type"] for t in shown] == BUILT_IN_KINDS
    assert all(t["built_in"] and t["id"] is None and t["documents"] == 0 for t in shown)
    invoice = shown[0]
    assert invoice["title"] == "Invoice"
    assert {"invoice_number", "total_amount", "line_item"} <= {f["name"] for f in invoice["fields"]}


def test_the_built_in_templates_keep_to_what_a_member_s_template_may_be():
    for template in BUILT_IN:
        written = TemplateIn(
            doc_type=template.doc_type,
            title=template.title,
            description=template.description,
            fields=[
                {
                    "name": field.name,
                    "type": field.type,
                    "description": field.description,
                    "required": field.required,
                    "examples": list(field.examples),
                }
                for field in template.fields
            ],
        )
        assert kind_of(written.doc_type) == template.doc_type
        assert [enrich.normalize_fact_name(f.name) for f in written.fields] == [
            f.name for f in template.fields
        ]
        assert any(field.required for field in template.fields), template.doc_type


def test_a_project_s_own_template_replaces_the_built_in_one_of_its_kind(
    client, session, account, templates_on
):
    project = create_project(client)
    made = make(client, project["id"])
    # The kind in lowercase words, the fields named as their facts are, each example once.
    assert made["doc_type"] == "invoice"
    assert [f["name"] for f in made["fields"]] == ["invoice_number", "iban", "total_amount"]
    assert made["fields"][0]["examples"] == ["INV-1"]
    assert made["replaces_built_in"] and not made["built_in"]

    shown = listed(client, project["id"])
    assert [(t["doc_type"], t["id"]) for t in shown][:2] == [
        ("invoice", made["id"]),
        ("contract", None),
    ]
    assert [t["doc_type"] for t in shown].count("invoice") == 1

    # Deleted, the built-in one applies again.
    assert client.delete(f"/api/templates/{made['id']}").status_code == 204
    assert [t["doc_type"] for t in listed(client, project["id"])] == BUILT_IN_KINDS
    assert actions(session, account.organization.id)[-2:] == [
        (
            "template_created",
            {
                "name": "Supplier invoice",
                "doc_type": "invoice",
                "fields": ["invoice_number", "iban", "total_amount"],
            },
        ),
        ("template_deleted", {"name": "Supplier invoice", "doc_type": "invoice"}),
    ]


def test_a_template_of_a_kind_of_the_members_own_is_listed_first(client, templates_on):
    project = create_project(client)
    made = make(
        client,
        project["id"],
        {
            "doc_type": "timesheet",
            "title": "Timesheet",
            "fields": [{"name": "hours", "type": "number"}],
        },
    )
    assert not made["replaces_built_in"]
    assert [t["doc_type"] for t in listed(client, project["id"])] == ["timesheet", *BUILT_IN_KINDS]


def test_a_change_writes_the_template_anew_and_says_what_changed(
    client, session, account, templates_on
):
    project = create_project(client)
    made = make(client, project["id"])
    changed = {
        **INVOICE,
        "doc_type": "supplier invoice",
        "fields": [
            {"name": "invoice_number", "type": "text", "required": True, "examples": ["INV-1"]},
            {"name": "total_amount", "type": "money"},
            {"name": "vat_number", "type": "text"},
        ],
    }
    response = client.put(f"/api/templates/{made['id']}", json=changed)
    assert response.status_code == 200, response.text
    assert response.json()["doc_type"] == "supplier invoice"
    assert not response.json()["replaces_built_in"]
    # The built-in invoice template applies again beside it.
    assert "invoice" in [t["doc_type"] for t in listed(client, project["id"])]
    assert actions(session, account.organization.id)[-1] == (
        "template_changed",
        {
            "name": "Supplier invoice",
            "doc_type": "supplier invoice",
            "added": ["vat_number"],
            "removed": ["iban"],
            "changed": ["total_amount"],
            "from": "invoice",
            "to": "supplier invoice",
        },
    )

    # Written as it was, nothing is recorded.
    assert client.put(f"/api/templates/{made['id']}", json=changed).status_code == 200
    assert [action for action, _ in actions(session, account.organization.id)].count(
        "template_changed"
    ) == 1


def test_a_template_is_refused_what_it_cannot_be(client, templates_on, monkeypatch):
    project = create_project(client)
    path = f"/api/projects/{project['id']}/templates"

    def refused(body: dict) -> tuple[int, str]:
        response = client.post(path, json=body)
        return response.status_code, str(response.json()["detail"])

    twice = {
        **INVOICE,
        "fields": [{"name": "Total", "type": "money"}, {"name": "total", "type": "text"}],
    }
    assert refused(twice) == (400, "Two fields are named total. Give each field a name of its own.")
    nameless = {**INVOICE, "fields": [{"name": "—", "type": "text"}]}
    assert refused(nameless) == (400, "The field “—” needs a letter or a digit in its name.")
    assert refused({**INVOICE, "doc_type": "  "})[0] == 422
    assert refused({**INVOICE, "fields": []})[0] == 422
    assert refused({**INVOICE, "fields": [{"name": "x", "type": "colour"}]})[0] == 422
    many = {**INVOICE, "fields": [{"name": f"f{n}", "type": "text"} for n in range(31)]}
    assert refused(many)[0] == 422

    made = make(client, project["id"])
    status, detail = refused({**INVOICE, "doc_type": "INVOICE"})
    assert status == 409
    assert detail.startswith("This project's template “Supplier invoice” is for invoice documents")
    other = make(client, project["id"], {**INVOICE, "doc_type": "credit note"})
    taken = client.put(f"/api/templates/{other['id']}", json=INVOICE)
    assert taken.status_code == 409

    monkeypatch.setattr(routes, "MAX_TEMPLATES", 2)
    status, detail = refused({**INVOICE, "doc_type": "receipt"})
    assert status == 409
    assert detail == routes.TOO_MANY
    assert made["id"] != other["id"]


def test_a_viewer_reads_the_templates_and_changes_none(
    client, account, make_account, act_as, templates_on
):
    project = create_project(client)
    made = make(client, project["id"])
    act_as(make_account("vic@example.com", account.organization, role="viewer"))
    assert listed(client, project["id"])[0]["id"] == made["id"]
    assert client.post(f"/api/projects/{project['id']}/templates", json=INVOICE).status_code == 403
    assert client.put(f"/api/templates/{made['id']}", json=INVOICE).status_code == 403
    assert client.delete(f"/api/templates/{made['id']}").status_code == 403


# The enrich stage


def test_without_the_flag_a_document_is_read_as_it_always_was(
    client, session, services, sample_pdf
):
    project = create_project(client)
    document = upload(client, project["id"], sample_pdf)["documents"][0]
    services.llm.template = "contract"
    process_document(document["id"], services)

    [(kind, prompt)] = [call for call in services.llm.calls if "Analysis" in call[0]]
    assert kind == "DocumentAnalysis"
    assert "<templates>" not in prompt and "- template:" not in prompt
    assert enrich.FACT_INSTRUCTIONS in prompt
    assert session.get(Document, document["id"]).template is None


def test_a_document_is_read_for_the_template_it_is(
    client, session, services, sample_pdf, templates_on
):
    project = create_project(client)
    document = upload(client, project["id"], sample_pdf)["documents"][0]
    services.llm.template = "Contract"
    services.llm.more_facts = [fact("liability_cap", "EUR 250,000", "text", 250000)]
    process_document(document["id"], services)

    [(kind, prompt)] = [call for call in services.llm.calls if "Analysis" in call[0]]
    assert kind == "TemplatedDocumentAnalysis"
    listing = prompt[prompt.index("<templates>") :]
    assert "contract: Contract. An agreement between parties" in listing
    assert "  - party (text, required): Each party to the contract" in listing
    assert 'For example "INV-2026-0412".' in listing
    assert "- template: the kind of document this is" in prompt
    assert "first, when you named a template, each of its fields" in prompt

    shown = client.get(f"/api/documents/{document['id']}").json()
    assert (shown["template"], shown["doc_type"]) == ("contract", "contract")
    # A field's facts are of its type, whatever the reply said.
    cap = facts_of(session, document["id"])["liability_cap"]
    assert (cap.value_type, cap.number) == ("money", 250000)
    # The fields every contract should state that this one was not found to.
    assert shown["stages"]["enrich"]["detail"].endswith(
        ", by the contract template (not found: party)"
    )
    assert listed(client, project["id"])[1] == {
        **listed(client, project["id"])[1],
        "doc_type": "contract",
        "documents": 1,
    }


def test_a_document_of_no_listed_kind_keeps_its_own(
    client, session, services, sample_pdf, templates_on
):
    project = create_project(client)
    document = upload(client, project["id"], sample_pdf)["documents"][0]
    services.llm.template = "letter"
    process_document(document["id"], services)
    shown = client.get(f"/api/documents/{document['id']}").json()
    assert (shown["template"], shown["doc_type"]) == (None, "contract")
    assert "template" not in shown["stages"]["enrich"]["detail"]


def test_index_again_reads_the_documents_for_the_project_s_own_template(
    client, session, services, sample_pdf, templates_on
):
    project = create_project(client)
    document = upload(client, project["id"], sample_pdf)["documents"][0]
    services.llm.template = "contract"
    process_document(document["id"], services)
    made = make(
        client,
        project["id"],
        {
            "doc_type": "contract",
            "title": "Services agreement",
            "fields": [{"name": "notice_period", "type": "text", "required": True}],
        },
    )

    services.llm.calls.clear()
    queued = client.post(
        f"/api/projects/{project['id']}/reprocess", params={"from_stage": "enrich"}
    )
    assert queued.json() == {"queued": 1}
    process_document(document["id"], services)
    [(_, prompt)] = [call for call in services.llm.calls if "Analysis" in call[0]]
    # The project's own contract template in the built-in one's place.
    assert "contract: Services agreement." in prompt
    assert "Each party to the contract" not in prompt
    notice = facts_of(session, document["id"])["notice_period"]
    assert notice.value_type == "text"
    detail = client.get(f"/api/documents/{document['id']}").json()["stages"]["enrich"]["detail"]
    assert detail.endswith(", by the contract template")
    assert listed(client, project["id"])[0]["id"] == made["id"]
    assert listed(client, project["id"])[0]["documents"] == 1

    # Indexed again from its structure without the model, its facts are gone, and the template.
    services.llm.available = False
    client.post(f"/api/documents/{document['id']}/reprocess", params={"from_stage": "structure"})
    process_document(document["id"], services)
    assert session.get(Document, document["id"]).template is None
    assert listed(client, project["id"])[0]["documents"] == 0


def test_a_long_document_names_its_template_in_its_first_part(
    session, services, account, make_document, sample_pdf, templates_on, monkeypatch
):
    monkeypatch.setattr(enrich, "WINDOW_CHARS", 150)
    services.llm.template = "contract"
    document = make_document(sample_pdf)
    process_document(document.id, services)
    session.refresh(document)

    calls = [(kind, prompt) for kind, prompt in services.llm.calls if "Analysis" in kind]
    assert calls[0][0] == "TemplatedWindowAnalysis"
    assert {kind for kind, _ in calls[1:]} == {"WindowAnalysis"}
    first, rest = calls[0][1], [prompt for _, prompt in calls[1:]]
    assert first.count("\n  - ") > 40 and "invoice: Invoice." in first
    # The parts after the first are shown the template it named, and no other.
    for prompt in rest:
        assert "which its first part showed to be of the kind in <templates> below" in prompt
        assert "contract: Contract." in prompt and "invoice: Invoice." not in prompt
        assert "- template:" not in prompt
    assert (document.template, document.doc_type) == ("contract", "contract")


def test_a_long_listing_names_each_field_with_its_type_alone(
    session, services, account, make_document, sample_pdf, templates_on, monkeypatch
):
    monkeypatch.setattr(enrich, "LISTING_CHARS", 500)
    document = make_document(sample_pdf)
    process_document(document.id, services)
    [(_, prompt)] = [call for call in services.llm.calls if "Analysis" in call[0]]
    listing = prompt[prompt.index("<templates>") :]
    assert "  - invoice_number (text, required)\n" in listing
    assert "Each party" not in listing and "For example" not in listing
    assert len(templates_for(session, document.project_id)) == len(BUILT_IN)


def test_a_project_s_export_holds_its_own_templates(client, services, sample_pdf, templates_on):
    project = create_project(client)
    document = upload(client, project["id"], sample_pdf)["documents"][0]
    services.llm.template = "contract"
    process_document(document["id"], services)
    make(client, project["id"])
    export = ask(client, project["id"]).json()
    run_export(services)

    written = json.loads(archive_of(client, project["id"], export["id"]).read("project.json"))
    # The built-in ones are Emudoc's, and not the project's to keep.
    assert [(t["doc_type"], t["title"]) for t in written["templates"]] == [
        ("invoice", "Supplier invoice")
    ]
    assert written["templates"][0]["fields"][1] == {
        "name": "iban",
        "type": "text",
        "description": "The account to pay into",
        "required": False,
        "examples": [],
    }
    assert [d["template"] for d in written["documents"]] == ["contract"]
