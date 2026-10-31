"""Who may see and change what: every route is scoped to the caller's organisation and role."""

import re
from dataclasses import dataclass

import pytest
from pydantic_ai.messages import TextPart

from app.answers import answer
from app.check_runs import run_workflow
from app.config import get_settings
from app.drafts import write_draft
from app.encrypted import CONNECTOR_TOKENS, encrypt
from app.flags import CONNECTORS, DRAFTS, EMAIL_IN, QUESTION_SETS, SHARE_LINKS
from app.indexing.pipeline import process_document
from app.models import Connector
from app.question_reports import write_report
from tests.conftest import FakeTurn
from tests.test_api import create_project
from tests.test_chats import answered, ask, new_chat
from tests.test_cross_checks import CROSS_CHECK
from tests.test_folders import make_folder
from tests.test_templates import INVOICE, turn_on

Route = tuple[str, str, dict]
# An automation of the world's project (tests/test_automations.py imports this module).
AUTOMATION = {"title": "New invoices", "kind": "invoice"}
# A question set of the world's project (tests/test_question_sets.py imports this module).
QUESTION_SET = {"title": "Every morning", "questions": ["What is due?"], "schedule": "daily"}


@dataclass
class World:
    """A project with a folder, an indexed document with a fact a member checked, the upload it came
    in with, an extraction template of its own, a cross-check with a run of it and an automation, a
    draft the model wrote, a question set with a report of its answers, and a chat holding one
    answered
    question, and in its trash a folder, a document and a chat; another project in the trash; an
    invitation to the organisation and an API key of it, all made by the local owner in the local
    organisation, and another member of it; the project has an address, a folder of the
    owner's Google Drive connected, and its document a share link; the project has a tag its
    document carries, a smart folder of it and a filing rule. The extraction templates are
    on for every organisation, and so are the drafts, email in, connected folders, share links
    and question sets, so that what refuses another organisation is the project's scope, not the
    flag."""

    org_id: int
    project_id: int
    folder_id: int
    document_id: int
    chat_id: int
    message_id: int
    passage_id: int
    invitation_id: int
    key_id: int
    member_id: int
    trashed_folder_id: int
    trashed_document_id: int
    trashed_chat_id: int
    trashed_project_id: int
    export_id: int
    upload_id: int
    template_id: int
    fact_id: int
    check_id: int
    cross_check_id: int
    run_id: int
    automation_id: int
    draft_id: int
    connector_id: int
    webhook_id: int
    link_id: int
    tag_id: int
    smart_folder_id: int
    filing_rule_id: int
    question_set_id: int
    report_id: int


@pytest.fixture
def world(client, session, services, sample_pdf, account, make_account, monkeypatch) -> World:
    turn_on(monkeypatch)
    turn_on(monkeypatch, DRAFTS)
    turn_on(monkeypatch, EMAIL_IN)
    turn_on(monkeypatch, CONNECTORS)
    turn_on(monkeypatch, SHARE_LINKS)
    turn_on(monkeypatch, QUESTION_SETS)
    monkeypatch.setattr(get_settings(), "inbound_domain", "in.emudoc.test")
    monkeypatch.setattr(get_settings(), "google_drive_client_id", "drive-client")
    monkeypatch.setattr(get_settings(), "google_drive_client_secret", "drive-secret")
    project = create_project(client)
    folder = make_folder(client, project["id"], "Contracts")
    # The contract came in as a draft first, the version kept to open.
    draft_file = [("files", ("msa.txt", b"A draft of the services agreement."))]
    uploaded = client.post(f"/api/projects/{project['id']}/documents", files=draft_file).json()
    document = uploaded["documents"][0]
    process_document(document["id"], services)
    signed = {"file": ("msa.pdf", sample_pdf.read_bytes())}
    assert client.post(f"/api/documents/{document['id']}/versions", files=signed).status_code == 201
    process_document(document["id"], services)
    link = client.post(f"/api/documents/{document['id']}/share-links", json={})
    chat = new_chat(client, project["id"])
    services.llm.agent_turns.append(FakeTurn(TextPart("Nothing is due.")))
    asked = ask(client, chat["id"], "What is due?")
    answer(asked["answer"]["id"], services)
    hits = client.get(f"/api/projects/{project['id']}/search", params={"q": "notice"}).json()
    invited = client.post("/api/organization/invitations", json={"email": "ivy@example.com"})
    key = client.post("/api/organization/keys", json={"name": "CI"})
    hook = client.post(
        "/api/organization/webhooks",
        json={"url": "https://hooks.example.com/in", "events": ["document.ready"]},
    )
    member = make_account("mo@example.com", account.organization, role="member")
    trashed_folder = make_folder(client, project["id"], "Old")
    note = client.post(
        f"/api/projects/{project['id']}/documents",
        files=[("files", ("note.txt", b"A note for the trash."))],
    ).json()["documents"][0]
    trashed_chat = new_chat(client, project["id"])
    trashed_project = create_project(client, "Archive")
    exported = client.post(f"/api/projects/{project['id']}/exports")
    template = client.post(f"/api/projects/{project['id']}/templates", json=INVOICE)
    fact = client.get(f"/api/documents/{document['id']}/facts").json()[0]
    checked = client.put(f"/api/facts/{fact['id']}/check", json={}).json()
    cross_check = client.post(f"/api/projects/{project['id']}/cross-checks", json=CROSS_CHECK)
    checked_run = client.post(f"/api/projects/{project['id']}/check-runs", json={}).json()
    run_workflow(checked_run["id"], services)
    automation = client.post(f"/api/projects/{project['id']}/automations", json=AUTOMATION)
    draft = client.post(f"/api/projects/{project['id']}/drafts", json={"kind": "report"}).json()
    services.llm.agent_turns.append(FakeTurn(TextPart("A report.")))
    write_draft(draft["id"], account.user.id, services)
    given = client.put(f"/api/projects/{project['id']}/mailbox", json={"senders": ["acme.example"]})
    assert given.status_code == 200, given.text
    connector = Connector(
        org_id=account.organization.id,
        project_id=project["id"],
        provider="google",
        user_id=account.user.id,
        account="owner@example.com",
        refresh_token="",
        folder_ref="folder-1",
        folder_name="Invoices",
        status="active",
    )
    session.add(connector)
    session.flush()
    connector.refresh_token = encrypt(CONNECTOR_TOKENS, connector.id, "refresh-1")
    session.commit()
    tag = client.post(f"/api/projects/{project['id']}/tags", json={"name": "Signed"}).json()
    tagged = client.put(f"/api/documents/{document['id']}/tags", json={"tag_ids": [tag["id"]]})
    assert tagged.status_code == 200, tagged.text
    smart = client.post(
        f"/api/projects/{project['id']}/smart-folders",
        json={"name": "Signed ones", "filter": {"tags": [tag["id"]]}},
    )
    rule = client.post(
        f"/api/projects/{project['id']}/filing-rules", json=filing_rule(folder["id"])
    )
    assert rule.status_code == 201, rule.text
    question_set = client.post(f"/api/projects/{project['id']}/question-sets", json=QUESTION_SET)
    assert question_set.status_code == 201, question_set.text
    report = client.post(f"/api/question-sets/{question_set.json()['id']}/run").json()
    services.llm.agent_turns.append(FakeTurn(TextPart("Nothing is due.")))
    write_report(report["id"], services)
    for path in (
        f"/api/folders/{trashed_folder['id']}",
        f"/api/documents/{note['id']}",
        f"/api/chats/{trashed_chat['id']}",
        f"/api/projects/{trashed_project['id']}",
    ):
        assert client.delete(path).status_code == 204, path
    return World(
        account.organization.id,
        project["id"],
        folder["id"],
        document["id"],
        chat["id"],
        asked["answer"]["id"],
        hits["hits"][0]["passage_id"],
        invited.json()["id"],
        key.json()["id"],
        member.user.id,
        trashed_folder["id"],
        note["id"],
        trashed_chat["id"],
        trashed_project["id"],
        exported.json()["id"],
        uploaded["upload_id"],
        template.json()["id"],
        fact["id"],
        checked["check"]["id"],
        cross_check.json()["id"],
        checked_run["id"],
        automation.json()["id"],
        draft["id"],
        connector.id,
        hook.json()["id"],
        link.json()["id"],
        tag["id"],
        smart.json()["id"],
        rule.json()["id"],
        question_set.json()["id"],
        report["id"],
    )


def filing_rule(folder_id: int) -> dict:
    """A filing rule of the world's project: its contracts into its folder."""
    return {"condition": "type", "value": "contract", "action": "folder", "folder_id": folder_id}


def reads(w: World) -> list[Route]:
    """Every route that shows a project or what is in it, the chat aside."""
    return [
        ("GET", f"/api/projects/{w.project_id}", {}),
        ("GET", f"/api/projects/{w.project_id}/access", {}),
        ("GET", f"/api/projects/{w.project_id}/folders", {}),
        ("GET", f"/api/projects/{w.project_id}/documents", {}),
        ("GET", f"/api/projects/{w.project_id}/documents", {"params": {"tag": w.tag_id}}),
        (
            "GET",
            f"/api/projects/{w.project_id}/documents",
            {"params": {"smart": w.smart_folder_id}},
        ),
        ("GET", f"/api/projects/{w.project_id}/documents", {"params": {"recent": True}}),
        ("GET", f"/api/projects/{w.project_id}/tags", {}),
        ("GET", f"/api/projects/{w.project_id}/smart-folders", {}),
        ("GET", f"/api/projects/{w.project_id}/filing-rules", {}),
        ("GET", f"/api/projects/{w.project_id}/favorites", {}),
        ("GET", f"/api/projects/{w.project_id}/document-types", {}),
        ("GET", f"/api/projects/{w.project_id}/uploads", {"params": {"ids": w.upload_id}}),
        ("GET", f"/api/projects/{w.project_id}/chats", {}),
        ("GET", f"/api/projects/{w.project_id}/search", {"params": {"q": "notice"}}),
        ("GET", f"/api/projects/{w.project_id}/facts", {}),
        ("GET", f"/api/projects/{w.project_id}/facts/names", {}),
        ("GET", f"/api/projects/{w.project_id}/templates", {}),
        ("GET", f"/api/projects/{w.project_id}/fact-table", {"params": {"kind": "invoice"}}),
        (
            "GET",
            f"/api/projects/{w.project_id}/fact-table/export",
            {"params": {"kind": "invoice"}},
        ),
        ("GET", f"/api/projects/{w.project_id}/fact-changes", {"params": {"kind": "invoice"}}),
        ("GET", f"/api/projects/{w.project_id}/cross-checks", {}),
        ("GET", f"/api/cross-checks/{w.cross_check_id}/findings", {}),
        ("GET", f"/api/projects/{w.project_id}/check-runs", {}),
        ("GET", f"/api/check-runs/{w.run_id}", {}),
        ("GET", f"/api/check-runs/{w.run_id}/findings", {"params": {"rule": 0}}),
        ("GET", f"/api/projects/{w.project_id}/automations", {}),
        ("GET", f"/api/projects/{w.project_id}/question-sets", {}),
        ("GET", f"/api/projects/{w.project_id}/reports", {}),
        ("GET", f"/api/reports/{w.report_id}", {}),
        ("GET", f"/api/projects/{w.project_id}/reviews", {}),
        ("GET", f"/api/projects/{w.project_id}/reviewers", {}),
        ("GET", f"/api/projects/{w.project_id}/drafts", {}),
        ("GET", f"/api/projects/{w.project_id}/mailbox", {}),
        ("GET", f"/api/projects/{w.project_id}/connectors", {}),
        ("GET", f"/api/drafts/{w.draft_id}", {}),
        ("GET", f"/api/drafts/{w.draft_id}/versions/1", {}),
        ("GET", f"/api/drafts/{w.draft_id}/versions/1/export", {}),
        ("GET", f"/api/projects/{w.project_id}/draft-templates", {}),
        ("GET", f"/api/documents/{w.document_id}", {}),
        ("GET", f"/api/documents/{w.document_id}/file", {}),
        ("GET", f"/api/documents/{w.document_id}/pages/1.png", {}),
        ("GET", f"/api/documents/{w.document_id}/thumbnail.png", {}),
        ("GET", f"/api/documents/{w.document_id}/outline", {}),
        ("GET", f"/api/documents/{w.document_id}/content", {}),
        ("GET", f"/api/documents/{w.document_id}/facts", {}),
        ("GET", f"/api/documents/{w.document_id}/note", {}),
        ("GET", f"/api/documents/{w.document_id}/copies", {}),
        ("GET", f"/api/documents/{w.document_id}/share-links", {}),
        ("GET", f"/api/documents/{w.document_id}/versions", {}),
        ("GET", f"/api/documents/{w.document_id}/versions/1/file", {}),
        ("GET", f"/api/documents/{w.document_id}/versions/1/content", {}),
        ("GET", f"/api/passages/{w.passage_id}", {}),
    ]


def writes(w: World) -> list[Route]:
    """Every route that changes a project or what is in it, the destructive ones last."""
    return [
        ("PATCH", f"/api/projects/{w.project_id}", {"json": {"name": "Renamed"}}),
        (
            "PUT",
            f"/api/projects/{w.project_id}/access",
            {"json": {"visibility": "organization", "user_ids": []}},
        ),
        ("POST", f"/api/projects/{w.project_id}/reprocess", {}),
        (
            "POST",
            f"/api/projects/{w.project_id}/templates/read-again",
            {"json": {"kind": "invoice"}},
        ),
        ("POST", f"/api/projects/{w.project_id}/folders", {"json": {"name": "Invoices"}}),
        ("POST", f"/api/projects/{w.project_id}/tags", {"json": {"name": "Paid"}}),
        ("PATCH", f"/api/tags/{w.tag_id}", {"json": {"color": "green"}}),
        ("PUT", f"/api/documents/{w.document_id}/tags", {"json": {"tag_ids": []}}),
        (
            "POST",
            f"/api/projects/{w.project_id}/documents/tags",
            {"json": {"document_ids": [w.document_id], "add": [w.tag_id]}},
        ),
        (
            "POST",
            f"/api/projects/{w.project_id}/documents/reprocess",
            {"json": {"document_ids": [w.document_id], "from_stage": "parse"}},
        ),
        (
            "POST",
            f"/api/projects/{w.project_id}/smart-folders",
            {"json": {"name": "Contracts", "filter": {"types": ["contract"]}}},
        ),
        (
            "PUT",
            f"/api/smart-folders/{w.smart_folder_id}",
            {"json": {"name": "Signed", "filter": {"tags": [w.tag_id]}}},
        ),
        (
            "POST",
            f"/api/projects/{w.project_id}/filing-rules",
            {"json": filing_rule(w.folder_id)},
        ),
        ("PUT", f"/api/filing-rules/{w.filing_rule_id}", {"json": filing_rule(w.folder_id)}),
        (
            "POST",
            f"/api/projects/{w.project_id}/templates",
            {"json": {**INVOICE, "doc_type": "credit note"}},
        ),
        ("PUT", f"/api/templates/{w.template_id}", {"json": INVOICE}),
        ("POST", f"/api/projects/{w.project_id}/cross-checks", {"json": CROSS_CHECK}),
        ("PUT", f"/api/cross-checks/{w.cross_check_id}", {"json": CROSS_CHECK}),
        ("POST", f"/api/projects/{w.project_id}/check-runs", {"json": {}}),
        ("POST", f"/api/check-runs/{w.run_id}/report", {}),
        ("POST", f"/api/projects/{w.project_id}/automations", {"json": AUTOMATION}),
        ("PUT", f"/api/automations/{w.automation_id}", {"json": AUTOMATION}),
        ("POST", f"/api/projects/{w.project_id}/question-sets", {"json": QUESTION_SET}),
        ("PUT", f"/api/question-sets/{w.question_set_id}", {"json": QUESTION_SET}),
        ("POST", f"/api/question-sets/{w.question_set_id}/run", {}),
        ("POST", f"/api/projects/{w.project_id}/drafts", {"json": {"kind": "report"}}),
        ("POST", f"/api/drafts/{w.draft_id}/versions", {"json": {"text": "Mine.", "base": 1}}),
        ("POST", f"/api/drafts/{w.draft_id}/rewrite", {"json": {}}),
        (
            "PUT",
            f"/api/projects/{w.project_id}/draft-templates/report",
            {"json": {"tone": "Brief."}},
        ),
        ("PUT", f"/api/facts/{w.fact_id}/check", {"json": {"value": "120 days"}}),
        ("PUT", f"/api/projects/{w.project_id}/mailbox", {"json": {"senders": ["acme.example"]}}),
        ("POST", f"/api/projects/{w.project_id}/mailbox/address", {}),
        ("POST", f"/api/projects/{w.project_id}/connectors", {"json": {"provider": "google"}}),
        # Browsing the member's folders at the service, which is for choosing one: a change.
        ("GET", f"/api/projects/{w.project_id}/connectors/{w.connector_id}/folders", {}),
        (
            "PUT",
            f"/api/projects/{w.project_id}/connectors/{w.connector_id}",
            {"json": {"target_folder_id": None}},
        ),
        ("POST", f"/api/projects/{w.project_id}/connectors/{w.connector_id}/sync", {}),
        (
            "POST",
            f"/api/projects/{w.project_id}/documents",
            {"files": [("files", ("other.txt", b"Some other file."))]},
        ),
        ("PATCH", f"/api/folders/{w.folder_id}", {"json": {"name": "Renamed"}}),
        ("PATCH", f"/api/documents/{w.document_id}", {"json": {"filename": "renamed.pdf"}}),
        ("PATCH", f"/api/documents/{w.document_id}/details", {"json": {"title": "Mine"}}),
        ("PUT", f"/api/documents/{w.document_id}/note", {"json": {"text": "Mine."}}),
        ("POST", f"/api/documents/{w.document_id}/reprocess", {}),
        (
            "POST",
            f"/api/documents/{w.document_id}/versions",
            {"files": {"file": ("msa.pdf", b"%PDF-1.7 another contract")}},
        ),
        ("PUT", f"/api/documents/{w.document_id}/review", {"json": {"state": "approved"}}),
        ("POST", f"/api/documents/{w.document_id}/share-links", {"json": {}}),
        ("DELETE", f"/api/fact-checks/{w.check_id}", {}),
        ("DELETE", f"/api/check-runs/{w.run_id}", {}),
        ("DELETE", f"/api/drafts/{w.draft_id}", {}),
        ("DELETE", f"/api/projects/{w.project_id}/draft-templates/report", {}),
        ("DELETE", f"/api/automations/{w.automation_id}", {}),
        ("DELETE", f"/api/reports/{w.report_id}", {}),
        ("DELETE", f"/api/question-sets/{w.question_set_id}", {}),
        ("DELETE", f"/api/filing-rules/{w.filing_rule_id}", {}),
        ("DELETE", f"/api/smart-folders/{w.smart_folder_id}", {}),
        ("DELETE", f"/api/tags/{w.tag_id}", {}),
        ("DELETE", f"/api/projects/{w.project_id}/mailbox", {}),
        ("DELETE", f"/api/projects/{w.project_id}/connectors/{w.connector_id}", {}),
        ("DELETE", f"/api/cross-checks/{w.cross_check_id}", {}),
        ("DELETE", f"/api/templates/{w.template_id}", {}),
        ("DELETE", f"/api/folders/{w.folder_id}", {}),
        ("DELETE", f"/api/documents/{w.document_id}", {}),
        ("DELETE", f"/api/projects/{w.project_id}", {}),
    ]


def trash_reads(w: World) -> list[Route]:
    """The project's trash, which whoever sees the project reads."""
    return [("GET", f"/api/projects/{w.project_id}/trash", {})]


def trash_writes(w: World) -> list[Route]:
    """Restoring from the project's trash and deleting for good, a member's or better."""
    return [
        ("POST", f"/api/trash/folders/{w.trashed_folder_id}/restore", {}),
        ("POST", f"/api/trash/documents/{w.trashed_document_id}/restore", {}),
        ("DELETE", f"/api/trash/folders/{w.trashed_folder_id}", {}),
        ("DELETE", f"/api/trash/documents/{w.trashed_document_id}", {}),
        ("DELETE", f"/api/projects/{w.project_id}/trash", {}),
    ]


def trashed_project(w: World) -> list[Route]:
    """Restoring a project from the organisation's trash and deleting it for good."""
    return [
        ("POST", f"/api/trash/projects/{w.trashed_project_id}/restore", {}),
        ("DELETE", f"/api/trash/projects/{w.trashed_project_id}", {}),
    ]


def chat_trash(w: World) -> list[Route]:
    """A chat in the trash, its starter's alone whatever it was."""
    return [
        ("POST", f"/api/trash/chats/{w.trashed_chat_id}/restore", {}),
        ("DELETE", f"/api/trash/chats/{w.trashed_chat_id}", {}),
    ]


def chat_reads(w: World) -> list[Route]:
    """The routes that show a chat, which a shared chat shows the project's members, and its pin,
    which each of its readers puts on their own list (test_pins.py)."""
    return [
        ("GET", f"/api/chats/{w.chat_id}", {}),
        ("PUT", f"/api/chats/{w.chat_id}/pin", {}),
        ("DELETE", f"/api/chats/{w.chat_id}/pin", {}),
        ("GET", f"/api/chats/{w.chat_id}/messages/{w.message_id}/events", {}),
        ("GET", f"/api/chats/{w.chat_id}/messages/{w.message_id}/share-links", {}),
        ("GET", f"/api/chats/{w.chat_id}/messages/{w.message_id}/export", {}),
        ("GET", f"/api/chats/{w.chat_id}/export", {}),
    ]


def marks(w: World) -> list[Route]:
    """A reader's own marks on a document, which a viewer makes too and nobody else sees: their
    star and their visit (test_favorites.py)."""
    return [
        ("PUT", f"/api/documents/{w.document_id}/favorite", {}),
        ("DELETE", f"/api/documents/{w.document_id}/favorite", {}),
        ("PUT", f"/api/documents/{w.document_id}/visit", {}),
    ]


def chat_writes(w: World) -> list[Route]:
    """The routes that change a chat or ask in it, which stay its starter's when it is shared."""
    return [
        ("PATCH", f"/api/chats/{w.chat_id}", {"json": {"title": "Renamed"}}),
        ("POST", f"/api/chats/{w.chat_id}/messages", {"json": {"content": "What is due?"}}),
        ("POST", f"/api/chats/{w.chat_id}/messages/{w.message_id}/stop", {}),
        ("POST", f"/api/chats/{w.chat_id}/messages/{w.message_id}/retry", {}),
        ("POST", f"/api/chats/{w.chat_id}/messages/{w.message_id}/regenerate", {"json": {}}),
        ("POST", f"/api/chats/{w.chat_id}/messages/{w.message_id}/draft", {}),
        ("DELETE", f"/api/chats/{w.chat_id}", {}),
    ]


def chat_routes(w: World) -> list[Route]:
    return chat_reads(w) + chat_writes(w)


def starts(w: World) -> list[Route]:
    """Starting a chat, which is reading: a viewer may (test_a_viewer_can_read_but_not_change)."""
    return [("POST", f"/api/projects/{w.project_id}/chats", {})]


def exports(w: World) -> list[Route]:
    """A project's exports, each its member's alone (test_exports.py): asking for one, which a
    viewer may not, the member's list, and the file."""
    return [
        ("GET", f"/api/projects/{w.project_id}/exports", {}),
        ("POST", f"/api/projects/{w.project_id}/exports", {}),
        ("GET", f"/api/projects/{w.project_id}/exports/{w.export_id}/file", {}),
    ]


def switches(w: World) -> list[Route]:
    """Switching to the organisation, which only its members may, and joining it, which only
    someone whose confirmed address is at one of its domains may; the local account, which
    has no session, may do neither (test_organizations.py, test_invitations.py)."""
    return [
        ("POST", f"/api/organizations/{w.org_id}/switch", {}),
        ("POST", f"/api/organizations/{w.org_id}/join", {}),
    ]


def invitations(w: World) -> list[Route]:
    """The organisation's invitations, which only its owners and admins manage."""
    return [
        ("POST", f"/api/organization/invitations/{w.invitation_id}/resend", {}),
        ("DELETE", f"/api/organization/invitations/{w.invitation_id}", {}),
    ]


def keys(w: World) -> list[Route]:
    """The organisation's API keys, which only its owners and admins manage (test_apikeys.py)."""
    return [
        ("POST", f"/api/organization/keys/{w.key_id}/roll", {}),
        ("DELETE", f"/api/organization/keys/{w.key_id}", {}),
    ]


def webhooks(w: World) -> list[Route]:
    """The organisation's webhooks, which only its owners and admins manage (test_webhooks.py)."""
    return [
        ("PATCH", f"/api/organization/webhooks/{w.webhook_id}", {"json": {"enabled": False}}),
        ("POST", f"/api/organization/webhooks/{w.webhook_id}/roll", {}),
        ("GET", f"/api/organization/webhooks/{w.webhook_id}/deliveries", {}),
        ("DELETE", f"/api/organization/webhooks/{w.webhook_id}", {}),
    ]


def links(w: World) -> list[Route]:
    """Revoking a document's share link, which its maker or an owner or admin may
    (test_share_links.py)."""
    return [("DELETE", f"/api/share-links/{w.link_id}", {})]


def answer_links(w: World) -> list[Route]:
    """Sharing an answer by link, which whoever sees its chat and may change the project may, a
    shared chat's starter or not (test_share_links.py)."""
    return [("POST", f"/api/chats/{w.chat_id}/messages/{w.message_id}/share-links", {"json": {}})]


def members(w: World) -> list[Route]:
    """Changing a member's role and removing them, which only the owners and admins may."""
    return [
        ("PATCH", f"/api/organization/members/{w.member_id}", {"json": {"role": "viewer"}}),
        ("DELETE", f"/api/organization/members/{w.member_id}", {}),
    ]


def scoped(w: World) -> list[Route]:
    return (
        reads(w)
        + writes(w)
        + chat_routes(w)
        + trash_reads(w)
        + trash_writes(w)
        + trashed_project(w)
        + chat_trash(w)
        + starts(w)
        + exports(w)
        + switches(w)
        + invitations(w)
        + keys(w)
        + webhooks(w)
        + links(w)
        + answer_links(w)
        + members(w)
        + marks(w)
    )


def statuses(client, routes: list[Route]) -> dict[str, int]:
    """Status per route, keyed by "METHOD /path", so a failure names the route at fault."""
    return {
        f"{method} {path}": client.request(method, path, **kwargs).status_code
        for method, path, kwargs in routes
    }


def test_every_route_that_names_what_it_acts_on_is_tried(client, world):
    """A route added with an id in its path joins the lists above, or this fails: the lists
    are what the tests below hold to the caller's organisation and role. The account's own
    routes (/api/auth) are held to the user in test_auth.py, the operator's area to the operator
    in test_operator.py, the versioned API's (/api/v1), which takes keys alone, to the key's
    organisation, member and scope in test_api_v1.py, and a share link's visitor's
    (/api/shared), which take no account, to the link's view in test_share_links.py."""
    tried = [(method, path) for method, path, _ in scoped(world)]
    untried = [
        f"{method.upper()} {template}"
        for template, operations in client.app.openapi()["paths"].items()
        if "{" in template
        and not template.startswith(("/api/auth/", "/api/operator/", "/api/v1/", "/api/shared/"))
        for method in operations
        if not any(
            method.upper() == used and re.fullmatch(re.sub(r"\{\w+\}", r"[^/]+", template), path)
            for used, path in tried
        )
    ]
    assert untried == []


def test_another_organisation_sees_nothing(client, world, make_account, act_as):
    act_as(make_account("eve@globex.com"))
    assert client.get("/api/projects").json() == []
    found = statuses(client, scoped(world))
    assert set(found.values()) == {404}, found

    # Eve's own project is hers, and hers alone.
    own = create_project(client, "Globex files")
    assert [p["id"] for p in client.get("/api/projects").json()] == [own["id"]]
    act_as(make_account("ada@initech.com"))
    assert client.get("/api/projects").json() == []

    # The owner's project is untouched by what Eve tried.
    client.app.dependency_overrides.clear()
    assert client.get(f"/api/projects/{world.project_id}").json()["document_count"] == 1
    assert client.get(f"/api/documents/{world.document_id}").json()["filename"] == "msa.pdf"
    folders = client.get(f"/api/projects/{world.project_id}/folders").json()
    assert [f["name"] for f in folders] == ["Contracts"]
    messages = client.get(f"/api/chats/{world.chat_id}").json()["messages"]
    assert [(m["content"], m["status"]) for m in messages] == [
        ("What is due?", "done"),
        ("Nothing is due.", "done"),
    ]


def test_a_private_project_is_found_by_nobody_else(client, world, account, make_account, act_as):
    """A member not chosen for a private project finds nothing of it, as another organisation
    does not; one chosen, and an admin, find everything (test_access.py has the rest)."""
    chosen = make_account("cy@example.com", account.organization, role="member")
    admin = make_account("al@example.com", account.organization, role="admin")
    client.patch(f"/api/chats/{world.chat_id}", json={"visibility": "project"})
    made = client.put(
        f"/api/projects/{world.project_id}/access",
        json={"visibility": "private", "user_ids": [chosen.user.id]},
    )
    assert made.status_code == 200, made.text
    project_routes = (
        reads(world)
        + writes(world)
        + chat_routes(world)
        + trash_reads(world)
        + trash_writes(world)
        + starts(world)
        + links(world)
        + answer_links(world)
        + marks(world)
    )

    act_as(make_account("mo.too@example.com", account.organization, role="member"))
    assert client.get("/api/projects").json() == []
    found = statuses(client, project_routes)
    assert set(found.values()) == {404}, found

    for member in (chosen, admin):
        act_as(member)
        assert [p["id"] for p in client.get("/api/projects").json()] == [world.project_id]
        shown = statuses(client, reads(world) + chat_reads(world) + trash_reads(world))
        assert set(shown.values()) == {200}, shown
        marked = statuses(client, marks(world))
        assert set(marked.values()) == {204}, marked


def test_a_viewer_can_read_but_not_change(client, world, account, make_account, act_as):
    act_as(make_account("vic@example.com", account.organization, role="viewer"))
    assert [p["id"] for p in client.get("/api/projects").json()] == [world.project_id]
    seen = statuses(client, reads(world) + trash_reads(world))
    assert set(seen.values()) == {200}, seen
    marked = statuses(client, marks(world))
    assert set(marked.values()) == {204}, marked
    refused = statuses(client, writes(world) + trash_writes(world) + trashed_project(world))
    assert set(refused.values()) == {403}, refused
    # The owner's chat in the trash is the owner's alone, and so is the private chat's answer.
    hidden = statuses(client, chat_trash(world) + answer_links(world))
    assert set(hidden.values()) == {404}, hidden
    assert client.post("/api/projects", json={"name": "Mine"}).status_code == 403
    detail = client.delete(f"/api/documents/{world.document_id}").json()["detail"]
    assert detail.startswith("Your role in this organisation is viewer")
    managed = statuses(
        client, invitations(world) + keys(world) + webhooks(world) + links(world) + members(world)
    )
    assert set(managed.values()) == {403}, managed

    # Asking questions is reading: a viewer may start a chat of their own.
    chat = new_chat(client, world.project_id)
    assert chat["visibility"] == "private"


def test_a_member_can_change_things(client, world, account, make_account, act_as):
    act_as(make_account("max@example.com", account.organization, role="member"))
    assert client.post("/api/projects", json={"name": "Mine"}).status_code == 201
    renamed = client.patch(f"/api/projects/{world.project_id}", json={"name": "Ours"})
    assert renamed.status_code == 200
    assert client.delete(f"/api/folders/{world.folder_id}").status_code == 204
    # The invitations, the API keys, the webhooks, another's share links and the members' roles
    # are the owners' and admins' to manage.
    managed = statuses(
        client, invitations(world) + keys(world) + webhooks(world) + links(world) + members(world)
    )
    assert set(managed.values()) == {403}, managed


def test_chats_are_private_until_shared(client, services, world, account, make_account, act_as):
    owner_chat = world.chat_id
    max_ = make_account("max@example.com", account.organization, role="member")
    act_as(max_)
    assert client.get(f"/api/projects/{world.project_id}/chats").json() == []
    hidden = statuses(client, chat_routes(world) + chat_trash(world) + answer_links(world))
    assert set(hidden.values()) == {404}, hidden
    own = new_chat(client, world.project_id)

    # The owner shares the chat. Max can read it, but it stays the owner's to run.
    client.app.dependency_overrides.clear()
    assert [c["id"] for c in client.get(f"/api/projects/{world.project_id}/chats").json()] == [
        owner_chat
    ]
    assert client.get(f"/api/chats/{own['id']}").status_code == 404
    client.patch(f"/api/chats/{owner_chat}", json={"visibility": "project"})

    act_as(max_)
    listed = client.get(f"/api/projects/{world.project_id}/chats").json()
    assert {c["id"] for c in listed} == {owner_chat, own["id"]}
    # Each chat names its starter, so the page offers the composer to them alone.
    starters = {c["id"]: (c["user_id"], c["started_by"]) for c in listed}
    assert starters == {
        owner_chat: (account.user.id, account.user.name),
        own["id"]: (max_.user.id, "Max"),
    }
    assert client.get(f"/api/chats/{owner_chat}").json()["started_by"] == account.user.name
    shown = statuses(client, chat_reads(world))
    assert set(shown.values()) == {200}, shown
    refused = statuses(client, chat_writes(world))
    assert set(refused.values()) == {403}, refused
    detail = client.delete(f"/api/chats/{owner_chat}").json()["detail"]
    assert detail == "Only the member who started this chat can change it or ask in it."

    # Max's own chat is fully his, questions included.
    services.llm.agent_turns.append(FakeTurn(TextPart("Nothing is due.")))
    assert answered(client, services, own["id"], "What is due?")[-1]["status"] == "done"
    assert client.delete(f"/api/chats/{own['id']}").status_code == 204
