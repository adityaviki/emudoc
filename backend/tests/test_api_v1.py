"""The versioned API (app/api/v1/): a program's way into an organisation's projects with an API key
alone, answered in the API's own models a page at a time, making things once with an
Idempotency-Key (app/idempotency.py), and held to the contract it was published with."""

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import TextPart, ToolCallPart
from sqlalchemy import delete, func, select, update

import app.api.v1.contract
from app.answers import Reply, answer
from app.api.v1.chats import _Sent
from app.api.v1.contract import TAGS, breaking_changes, contract, published, write
from app.api.v1.deps import BAD_CURSOR, NO_KEY
from app.apikeys import READ_ONLY_KEY, issue
from app.check_runs import run_workflow
from app.config import get_settings
from app.db import engine
from app.idempotency import (
    ABANDONED,
    BAD_KEY,
    IN_FLIGHT,
    KEPT,
    KEY_REUSED,
    REPLAYED,
    prune_idempotency_keys,
)
from app.indexing.pipeline import process_document
from app.maintenance import housekeeping
from app.models import ApiKey, ChatEvent, Document, IdempotencyKey, Project
from app.question_reports import write_report
from app.versions import SAME_FILE
from tests.conftest import FakeTurn
from tests.test_authorization import QUESTION_SET
from tests.test_chats import questions_counted
from tests.test_check_runs import TOTAL, invoices, work
from tests.test_cross_checks import CROSS_CHECK, DUE

Route = tuple[str, str, dict]


def key_for(session, account, scope: str = "write") -> str:
    """A key of the account's member, made as the organisation's page makes one."""
    key = ApiKey(
        org_id=account.organization.id, user_id=account.user.id, name="Program", scope=scope
    )
    secret = issue(key)
    session.add(key)
    session.commit()
    return secret


def program(client, secret: str) -> TestClient:
    """A program calling the API with the key: no cookie and none of the app's headers."""
    return TestClient(client.app, headers={"Authorization": f"Bearer {secret}"})


@pytest.fixture
def api(client, session, account) -> TestClient:
    return program(client, key_for(session, account))


def made(api, path: str, body: dict | None = None, **headers) -> dict:
    response = api.post(path, json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def got(api, path: str, **params) -> dict:
    response = api.get(path, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def every(api, path: str, **params) -> list[dict]:
    """Every item of a list, following `next` a page at a time."""
    items, after = [], None
    while True:
        page = got(api, path, **params, **({"after": after} if after else {}))
        items += page["data"]
        if (after := page["next"]) is None:
            return items


def sent(api, project_id: int, *files: tuple[str, bytes], **headers):
    return api.post(
        f"/api/v1/projects/{project_id}/documents",
        files=[("files", file) for file in files],
        headers=headers,
    )


def streamed(response) -> list[tuple[int, str, dict]]:
    """The (id, event, data) of each event of a server-sent event body; comments are skipped."""
    events = []
    for block in response.text.split("\n\n"):
        fields = dict(
            line.split(": ", 1) for line in block.splitlines() if not line.startswith(":")
        )
        if "event" in fields:
            events.append((int(fields["id"]), fields["event"], json.loads(fields["data"])))
    return events


def test_the_api_is_called_with_a_key_alone(client, session, account, make_account):
    # The local account, which the page's routes fall back to, lets nothing in here.
    refused = client.get("/api/v1/projects")
    assert (refused.status_code, refused.json()["detail"]) == (401, NO_KEY)
    assert refused.headers["www-authenticate"] == "Bearer"
    assert program(client, "emk_" + "x" * 43).get("/api/v1/projects").status_code == 401

    api = program(client, key_for(session, account))
    status = got(api, "/api/v1/status")
    assert status["models"] == {"available": True, "error": None}
    assert status["uploads"]["files"] == 100 and ".pdf" in status["uploads"]["extensions"]
    # Any organisation's key is let in: nothing is held to a plan.
    eve = program(client, key_for(session, make_account("eve@globex.com")))
    assert eve.get("/api/v1/projects").status_code == 200


def test_a_program_makes_lists_changes_and_deletes_projects(api, session):
    first = made(api, "/api/v1/projects", {"name": "Contracts", "description": "Supplier files"})
    assert first == {
        "id": first["id"],
        "name": "Contracts",
        "description": "Supplier files",
        "visibility": "organization",
        "sample": False,
        "document_count": 0,
        "created_at": first["created_at"],
    }
    made(api, "/api/v1/projects", {"name": "Invoices"})
    made(api, "/api/v1/projects", {"name": "Archive", "visibility": "private"})

    # The newest first, a page at a time.
    page = got(api, "/api/v1/projects", limit=2)
    assert [p["name"] for p in page["data"]] == ["Archive", "Invoices"]
    rest = got(api, "/api/v1/projects", limit=2, after=page["next"])
    assert [p["name"] for p in rest["data"]] == ["Contracts"] and rest["next"] is None
    bad = api.get("/api/v1/projects", params={"after": "nonsense"})
    assert (bad.status_code, bad.json()["detail"]) == (422, BAD_CURSOR)

    # A change names what it changes; the rest stays.
    path = f"/api/v1/projects/{first['id']}"
    changed = api.patch(path, json={"description": "The signed ones"}).json()
    assert (changed["name"], changed["description"]) == ("Contracts", "The signed ones")
    assert api.patch(path, json={"name": ""}).status_code == 422
    assert got(api, path)["description"] == "The signed ones"

    assert api.delete(path).status_code == 204
    assert api.get(path).status_code == 404
    assert [p["name"] for p in every(api, "/api/v1/projects")] == ["Archive", "Invoices"]


def test_folders_with_their_paths(api):
    project = made(api, "/api/v1/projects", {"name": "Contracts"})
    folders = f"/api/v1/projects/{project['id']}/folders"
    signed = made(api, folders, {"name": "Signed"})
    year = made(api, folders, {"name": "2026", "parent_id": signed["id"]})
    assert (year["path"], year["parent_id"], year["document_count"]) == (
        "Signed/2026",
        signed["id"],
        0,
    )
    clash = api.post(folders, json={"name": "signed"})
    assert clash.status_code == 409

    renamed = api.patch(f"/api/v1/folders/{signed['id']}", json={"name": "Agreed"}).json()
    assert renamed["path"] == "Agreed"
    assert got(api, f"/api/v1/folders/{year['id']}")["path"] == "Agreed/2026"
    # A parent of null moves a folder to the root; a field left out stays as it was.
    moved = api.patch(f"/api/v1/folders/{year['id']}", json={"parent_id": None}).json()
    assert (moved["path"], moved["name"]) == ("2026", "2026")
    assert [f["path"] for f in every(api, folders, limit=1)] == ["Agreed", "2026"]

    assert api.delete(f"/api/v1/folders/{signed['id']}").status_code == 204
    assert [f["name"] for f in every(api, folders)] == ["2026"]
    assert api.get(f"/api/v1/folders/{signed['id']}").status_code == 404


def test_an_upload_is_kept_and_followed_until_it_is_indexed(api, session, services, sample_pdf):
    project = made(api, "/api/v1/projects", {"name": "Contracts"})
    signed = made(api, f"/api/v1/projects/{project['id']}/folders", {"name": "Signed"})
    uploaded = sent(
        api,
        project["id"],
        ("msa.pdf", sample_pdf.read_bytes()),
        ("run.exe", b"MZ"),
        ("empty.txt", b""),
    )
    assert uploaded.status_code == 201, uploaded.text
    body = uploaded.json()
    (document,) = body["documents"]
    assert (document["filename"], document["status"], document["partly_indexed"]) == (
        "msa.pdf",
        "queued",
        False,
    )
    assert len(document["sha256"]) == 64
    assert [(r["index"], r["filename"], r["duplicate_of"]) for r in body["rejected"]] == [
        (1, "run.exe", None),
        (2, "empty.txt", None),
    ]
    assert body["rejected"][1]["reason"] == "Empty file."

    # The program follows the document by its id until it is no longer queued or processing.
    listed = got(api, f"/api/v1/projects/{project['id']}/documents", ids=[document["id"]])
    assert [d["status"] for d in listed["data"]] == ["queued"]
    process_document(document["id"], services)
    ready = got(api, f"/api/v1/documents/{document['id']}")
    assert ready["status"] == "ready" and ready["page_count"] == 2 and ready["title"]
    assert got(api, f"/api/v1/projects/{project['id']}/documents", status="queued")["data"] == []

    # Sent again, the file is the document that holds it already.
    again = sent(api, project["id"], ("copy.pdf", sample_pdf.read_bytes())).json()
    assert again["documents"] == [] and again["rejected"][0]["duplicate_of"] == document["id"]

    # Its file and its facts; moved and renamed; then into the trash.
    file = api.get(f"/api/v1/documents/{document['id']}/file")
    assert file.status_code == 200 and file.content == sample_pdf.read_bytes()
    facts = got(api, f"/api/v1/documents/{document['id']}/facts")
    assert facts["next"] is None and {"name", "value", "check"} <= set(facts["data"][0])
    path = f"/api/v1/documents/{document['id']}"
    moved = api.patch(path, json={"folder_id": signed["id"], "filename": "agreement"}).json()
    assert (moved["folder_id"], moved["filename"]) == (signed["id"], "agreement.pdf")
    inside = got(api, f"/api/v1/projects/{project['id']}/documents", folder_id=signed["id"])
    assert [d["id"] for d in inside["data"]] == [document["id"]]
    assert got(api, f"/api/v1/folders/{signed['id']}")["document_count"] == 1
    assert api.delete(path).status_code == 204
    assert api.get(path).status_code == 404
    assert got(api, f"/api/v1/projects/{project['id']}")["document_count"] == 0


def test_documents_come_newest_first_a_page_at_a_time(api):
    project = made(api, "/api/v1/projects", {"name": "Notes"})
    for number in range(5):
        sent(api, project["id"], (f"note-{number}.txt", f"Note number {number}.".encode()))
    path = f"/api/v1/projects/{project['id']}/documents"
    first = got(api, path, limit=2)
    assert [d["filename"] for d in first["data"]] == ["note-4.txt", "note-3.txt"]
    assert [d["filename"] for d in every(api, path, limit=2)] == [
        f"note-{number}.txt" for number in (4, 3, 2, 1, 0)
    ]


def test_an_upload_sent_again_with_its_key_is_kept_once(api, session):
    project = made(api, "/api/v1/projects", {"name": "Notes"})
    note = ("note.txt", b"A delivery note.")
    first = sent(api, project["id"], note, ("run.exe", b"MZ"), **{"Idempotency-Key": "upload-1"})
    assert first.status_code == 201 and REPLAYED.lower() not in first.headers
    # The answer lost, the program sends it again: the same answer, nothing done twice, and no
    # file taken for a duplicate of itself.
    again = sent(api, project["id"], note, ("run.exe", b"MZ"), **{"Idempotency-Key": "upload-1"})
    assert again.status_code == 201 and again.headers[REPLAYED] == "true"
    assert again.json() == first.json()
    assert session.scalar(select(func.count()).select_from(Document)) == 1

    # The key again with other files is a mistake, which is refused.
    other = sent(
        api, project["id"], ("note.txt", b"Another note."), **{"Idempotency-Key": "upload-1"}
    )
    assert (other.status_code, other.json()["detail"]) == (422, KEY_REUSED)
    # Quoted, as the header's draft writes it, it is the same key.
    quoted = sent(api, project["id"], note, ("run.exe", b"MZ"), **{"Idempotency-Key": '"upload-1"'})
    assert quoted.headers[REPLAYED] == "true"
    for wrong in ("", "x" * 256):
        refused = api.post(
            "/api/v1/projects", json={"name": "Mine"}, headers={"Idempotency-Key": wrong}
        )
        assert (refused.status_code, refused.json()["detail"]) == (400, BAD_KEY), wrong


def test_a_key_is_the_api_keys_own_and_names_one_request(client, api, session, account):
    once = {"Idempotency-Key": "make-invoices"}
    project = made(api, "/api/v1/projects", {"name": "Invoices"}, **once)
    assert made(api, "/api/v1/projects", {"name": "Invoices"}, **once) == project
    # Another key's request with the same key is its own.
    other = program(client, key_for(session, account))
    assert made(other, "/api/v1/projects", {"name": "Invoices"}, **once)["id"] != project["id"]
    assert session.scalar(select(func.count()).select_from(Project)) == 2
    # The key's rows go with it.
    session.execute(delete(ApiKey))
    session.commit()
    assert session.scalar(select(func.count()).select_from(IdempotencyKey)) == 0


def test_a_refused_request_gives_its_key_back(api, session):
    project = made(api, "/api/v1/projects", {"name": "Contracts"})
    folders = f"/api/v1/projects/{project['id']}/folders"
    taken = made(api, folders, {"name": "Signed"})
    once = {"Idempotency-Key": "folder-1"}
    assert api.post(folders, json={"name": "Signed"}, headers=once).status_code == 409
    assert session.scalar(select(func.count()).select_from(IdempotencyKey)) == 0
    # The name freed, the same request is carried out, not given the refusal again.
    api.patch(f"/api/v1/folders/{taken['id']}", json={"name": "Old"})
    second = api.post(folders, json={"name": "Signed"}, headers=once)
    assert second.status_code == 201 and REPLAYED.lower() not in second.headers


def test_a_key_whose_request_is_under_way_waits_for_it(api, session):
    once = {"Idempotency-Key": "slow"}
    made(api, "/api/v1/projects", {"name": "Slow"}, **once)
    # As though the first request were still being carried out.
    session.execute(update(IdempotencyKey).values(status_code=None, body=None))
    session.commit()
    waiting = api.post("/api/v1/projects", json={"name": "Slow"}, headers=once)
    assert (waiting.status_code, waiting.json()["detail"]) == (409, IN_FLIGHT)
    assert waiting.headers["retry-after"] == "5"
    # One that never ended, its process stopped, is taken over once it is abandoned: its
    # request is carried out again.
    session.execute(
        update(IdempotencyKey).values(created_at=datetime.now(UTC) - ABANDONED - timedelta(1))
    )
    session.commit()
    taken = api.post("/api/v1/projects", json={"name": "Slow"}, headers=once)
    assert taken.status_code == 201 and REPLAYED.lower() not in taken.headers
    assert made(api, "/api/v1/projects", {"name": "Slow"}, **once) == taken.json()
    assert session.scalar(select(func.count()).select_from(Project)) == 2


def test_a_key_is_forgotten_a_day_after(api, session, services):
    once = {"Idempotency-Key": "daily"}
    made(api, "/api/v1/projects", {"name": "Daily"}, **once)
    with engine.begin() as connection:
        assert prune_idempotency_keys(connection) == 0
    session.execute(
        update(IdempotencyKey).values(created_at=datetime.now(UTC) - KEPT - timedelta(minutes=1))
    )
    session.commit()
    housekeeping(services.storage)
    assert session.scalar(select(func.count()).select_from(IdempotencyKey)) == 0
    again = api.post("/api/v1/projects", json={"name": "Daily"}, headers=once)
    assert again.status_code == 201 and REPLAYED.lower() not in again.headers


def test_search_facts_and_the_passages_cited(api, services, sample_pdf):
    project = made(api, "/api/v1/projects", {"name": "Contracts"})
    (document,) = sent(api, project["id"], ("msa.pdf", sample_pdf.read_bytes())).json()["documents"]
    process_document(document["id"], services)

    found = got(api, f"/api/v1/projects/{project['id']}/search", q="notice period")
    assert found["semantic"] is True
    hit = found["data"][0]
    assert hit["document_id"] == document["id"] and "notice" in hit["snippet"].lower()
    assert api.get(f"/api/v1/projects/{project['id']}/search", params={"q": ""}).status_code == 422
    # A folder in the trash is not found, where the page's search finds nothing in it.
    folder = made(api, f"/api/v1/projects/{project['id']}/folders", {"name": "Old"})
    assert api.delete(f"/api/v1/folders/{folder['id']}").status_code == 204
    gone = {"q": "notice", "folder_id": folder["id"]}
    assert api.get(f"/api/v1/projects/{project['id']}/search", params=gone).status_code == 404
    passage = got(api, f"/api/v1/passages/{hit['passage_id']}")
    assert passage["document_id"] == document["id"] and passage["text"]

    facts = every(api, f"/api/v1/projects/{project['id']}/facts", limit=1)
    assert len(facts) > 1 and all(fact["document_id"] == document["id"] for fact in facts)
    of_document = got(api, f"/api/v1/documents/{document['id']}/facts")["data"]
    assert sorted(fact["id"] for fact in facts) == sorted(fact["id"] for fact in of_document)
    named = got(api, f"/api/v1/projects/{project['id']}/facts", name=facts[0]["name"])
    assert {fact["name"] for fact in named["data"]} == {facts[0]["name"]}


def test_a_question_asked_and_its_answer_polled_and_followed(api, session, services, sample_pdf):
    project = made(api, "/api/v1/projects", {"name": "Contracts"})
    (document,) = sent(api, project["id"], ("msa.pdf", sample_pdf.read_bytes())).json()["documents"]
    process_document(document["id"], services)
    chat = made(api, f"/api/v1/projects/{project['id']}/chats")
    assert (chat["visibility"], chat["title"]) == ("private", "")

    once = {"Idempotency-Key": "ask-1"}
    asked = made(
        api, f"/api/v1/chats/{chat['id']}/messages", {"content": " Notice period? "}, **once
    )
    question, reply = asked["question"], asked["answer"]
    assert (question["role"], question["content"], question["status"]) == (
        "user",
        "Notice period?",
        "done",
    )
    assert (reply["role"], reply["status"], reply["content"]) == ("assistant", "streaming", "")
    # Sent again, the question is asked and counted once.
    assert (
        made(api, f"/api/v1/chats/{chat['id']}/messages", {"content": " Notice period? "}, **once)
        == asked
    )
    assert questions_counted(session) == 1

    # While it is written, a poll reads what it says so far.
    path = f"/api/v1/chats/{chat['id']}/messages/{reply['id']}"
    session.add(ChatEvent(message_id=reply["id"], seq=0, event={"type": "text", "text": "So far"}))
    session.commit()
    assert got(api, path)["content"] == "So far"
    session.query(ChatEvent).delete()
    session.commit()

    services.llm.agent_turns = [
        FakeTurn(ToolCallPart("search", {"query": "notice"}, "call_1")),
        FakeTurn(TextPart("90 days [P1].")),
    ]
    answer(reply["id"], services)
    done = got(api, path)
    assert (done["status"], done["content"]) == ("done", "90 days [P1].")
    assert done["sources"] and {"passage_id", "excerpt"} <= set(done["sources"][0])
    assert [m["id"] for m in every(api, f"/api/v1/chats/{chat['id']}/messages", limit=1)] == [
        question["id"],
        reply["id"],
    ]
    assert got(api, f"/api/v1/chats/{chat['id']}")["title"] == "Notice period?"

    # The events: what the answer became, then the message as it was settled. The page's own
    # (the tool's call and result, the run's end) are folded in, not sent.
    events = streamed(api.get(f"{path}/events"))
    assert [(name, seq) for seq, name, _ in events] == [("text", 4), ("sources", 4), ("end", 4)]
    assert events[0][2] == {"text": "90 days [P1]."}
    assert events[2][2] == done
    # Followed again after the sources, the stream sends the rest alone.
    later = streamed(api.get(f"{path}/events", headers={"Last-Event-ID": "1"}))
    assert [name for _, name, _ in later] == ["text", "end"]
    assert [
        name for _, name, _ in streamed(api.get(f"{path}/events", headers={"Last-Event-ID": "4"}))
    ] == ["end"]
    # A question has no events.
    assert (
        api.get(f"/api/v1/chats/{chat['id']}/messages/{question['id']}/events").status_code == 404
    )


def test_the_events_send_what_the_answer_became():
    """Text adds to what was sent; a turn tried again, a note beside a step or a run started
    over sends the answer whole in its place, and its sources with it."""
    reply = Reply()
    follower = _Sent(reply)
    source = {
        "id": 7,
        "document_id": 3,
        "document_title": "MSA",
        "context": "Termination",
        "page_start": 2,
        "page_end": 2,
        "excerpt": "ninety (90) days",
    }
    shown = {
        "passage_id": 7,
        "document_id": 3,
        "document_title": "MSA",
        "page_start": 2,
        "page_end": 2,
        "excerpt": "ninety (90) days",
    }
    reply.apply({"type": "sources", "sources": [source]})
    reply.apply({"type": "tool_result", "id": "call_1", "summary": "", "is_error": False})
    reply.apply({"type": "text", "text": "It is "})
    assert list(follower.changes(reply)) == [
        ("text", {"text": "It is "}),
        ("sources", {"sources": [shown]}),
    ]
    assert list(follower.changes(reply)) == []
    reply.apply({"type": "text", "text": "90"})
    reply.apply({"type": "retry"})
    reply.apply({"type": "text", "text": "Ninety"})
    assert list(follower.changes(reply)) == [("reset", {"content": "Ninety", "sources": [shown]})]
    reply.apply({"type": "restart"})
    assert list(follower.changes(reply)) == [("reset", {"content": "", "sources": []})]
    # What the model wrote beside a step was its note to itself, which its call takes back.
    reply.apply({"type": "text", "text": "Let me read the termination clause."})
    assert list(follower.changes(reply)) == [
        ("text", {"text": "Let me read the termination clause."})
    ]
    reply.apply({"type": "tool_call", "id": "call_2", "name": "read", "input": {}})
    assert list(follower.changes(reply)) == [("reset", {"content": "", "sources": []})]


def test_a_run_of_the_cross_checks_followed_to_its_findings(client, api, session, services):
    project = made(api, "/api/v1/projects", {"name": "Invoices"})
    docs = invoices(session, project["id"])
    for rule in (DUE, TOTAL):
        assert (
            client.post(f"/api/projects/{project['id']}/cross-checks", json=rule).status_code == 201
        )
    checks = got(api, f"/api/v1/projects/{project['id']}/cross-checks")
    assert [check["title"] for check in checks["data"]] == [DUE["title"], TOTAL["title"]]
    assert all(check["problem"] is None for check in checks["data"])

    once = {"Idempotency-Key": "run-1"}
    run = made(api, f"/api/v1/projects/{project['id']}/check-runs", {}, **once)
    assert (run["status"], run["report_status"], run["counts"]) == ("queued", None, None)
    assert [rule["title"] for rule in run["rules"]] == [DUE["title"], TOTAL["title"]]
    assert made(api, f"/api/v1/projects/{project['id']}/check-runs", {}, **once) == run
    work(services)
    done = got(api, f"/api/v1/check-runs/{run['id']}")
    assert done["status"] == "done" and done["finished_at"]
    assert done["counts"] == {"differs": 2, "missing": 1, "holds": 1}
    assert [r["rule"] for r in done["rules"]] == [0, 1]
    assert got(api, f"/api/v1/projects/{project['id']}/check-runs")["data"] == [done]

    findings = got(api, f"/api/v1/check-runs/{run['id']}/findings", rule=1, outcome="differs")
    (finding,) = findings["data"]
    assert (finding["document_id"], finding["difference"], findings["total"]) == (
        docs["INV-1"],
        5.0,
        1,
    )
    assert finding["left"]["number"] == 1255.0 and finding["right"][0]["number"] == 1250.0
    every_one = every(api, f"/api/v1/check-runs/{run['id']}/findings", rule=0, limit=1)
    assert len(every_one) == got(api, f"/api/v1/check-runs/{run['id']}/findings", rule=0)["total"]


def test_a_program_reads_the_reports_of_a_projects_question_sets(client, api, services):
    project = made(api, "/api/v1/projects", {"name": "Invoices"})
    question_set = client.post(f"/api/projects/{project['id']}/question-sets", json=QUESTION_SET)
    report = client.post(f"/api/question-sets/{question_set.json()['id']}/run").json()
    services.llm.agent_turns.append(FakeTurn(TextPart("Nothing is due.")))
    write_report(report["id"], services)

    page = got(api, f"/api/v1/projects/{project['id']}/reports")
    (summary,) = page["data"]
    assert (summary["id"], summary["trigger"]) == (report["id"], "member")
    assert (summary["status"], summary["questions"], summary["answered"]) == ("done", 1, 1)
    assert page["next"] is None
    assert "answers" not in summary
    whole = got(api, f"/api/v1/reports/{report['id']}")
    assert whole["answers"] == [
        {
            "question": "What is due?",
            "status": "done",
            "answer": "Nothing is due.",
            "sources": [],
            "error": None,
        }
    ]


def test_a_program_gives_a_document_a_new_version_once_and_downloads_the_earlier_file(
    api, services
):
    old, new = b"The fee is 100 EUR a month.", b"The fee is 250 EUR a month."
    project = made(api, "/api/v1/projects", {"name": "Terms"})
    (document,) = sent(api, project["id"], ("terms.txt", old)).json()["documents"]
    process_document(document["id"], services)
    path = f"/api/v1/documents/{document['id']}/versions"
    key = {"Idempotency-Key": "terms-2"}

    first = api.post(path, files={"file": ("terms.txt", new)}, headers=key)
    assert first.status_code == 201, first.text
    assert (first.json()["id"], first.json()["version"]) == (document["id"], 2)
    # Sent again after a lost answer, it is the first one's answer, not a third version.
    again = api.post(path, files={"file": ("terms.txt", new)}, headers=key)
    assert (again.status_code, again.headers["Idempotent-Replayed"]) == (201, "true")
    assert again.json() == first.json()
    refused = api.post(path, files={"file": ("terms.txt", new)})
    assert (refused.status_code, refused.json()["detail"]) == (409, SAME_FILE)

    listed = got(api, path)
    assert [(v["number"], v["current"], v["sha256"]) for v in listed["data"]] == [
        (2, True, hashlib.sha256(new).hexdigest()),
        (1, False, hashlib.sha256(old).hexdigest()),
    ]
    assert listed["next"] is None
    assert api.get(f"{path}/1/file").content == old
    assert api.get(f"{path}/2/file").status_code == 404
    process_document(document["id"], services)
    assert got(api, f"/api/v1/documents/{document['id']}")["status"] == "ready"


@dataclass
class World:
    """A project with a folder, an indexed document, a chat with one answered question, a run of
    its cross-check, all made by a program with the local owner's write key, and a report of a
    question set the owner made and ran in the app."""

    project_id: int
    folder_id: int
    document_id: int
    passage_id: int
    chat_id: int
    message_id: int
    run_id: int
    report_id: int


@pytest.fixture
def world(client, api, session, services, sample_pdf) -> World:
    project = made(api, "/api/v1/projects", {"name": "Contracts"})
    folder = made(api, f"/api/v1/projects/{project['id']}/folders", {"name": "Signed"})
    # The contract came in as a draft first, the version kept to download.
    (document,) = sent(api, project["id"], ("msa.txt", b"A draft.")).json()["documents"]
    process_document(document["id"], services)
    signed = {"file": ("msa.pdf", sample_pdf.read_bytes())}
    assert api.post(f"/api/v1/documents/{document['id']}/versions", files=signed).status_code == 201
    process_document(document["id"], services)
    hits = got(api, f"/api/v1/projects/{project['id']}/search", q="notice")
    chat = made(api, f"/api/v1/projects/{project['id']}/chats")
    services.llm.agent_turns.append(FakeTurn(TextPart("Nothing is due.")))
    asked = made(api, f"/api/v1/chats/{chat['id']}/messages", {"content": "What is due?"})
    answer(asked["answer"]["id"], services)
    assert (
        client.post(f"/api/projects/{project['id']}/cross-checks", json=CROSS_CHECK).status_code
        == 201
    )
    run = made(api, f"/api/v1/projects/{project['id']}/check-runs", {})
    run_workflow(run["id"], services)
    question_set = client.post(f"/api/projects/{project['id']}/question-sets", json=QUESTION_SET)
    report = client.post(f"/api/question-sets/{question_set.json()['id']}/run").json()
    services.llm.agent_turns.append(FakeTurn(TextPart("Nothing is due.")))
    write_report(report["id"], services)
    return World(
        project["id"],
        folder["id"],
        document["id"],
        hits["data"][0]["passage_id"],
        chat["id"],
        asked["answer"]["id"],
        run["id"],
        report["id"],
    )


def reads(w: World) -> list[Route]:
    """Every route that shows a project or what it holds, the chat aside."""
    return [
        ("GET", f"/api/v1/projects/{w.project_id}", {}),
        ("GET", f"/api/v1/projects/{w.project_id}/folders", {}),
        ("GET", f"/api/v1/folders/{w.folder_id}", {}),
        ("GET", f"/api/v1/projects/{w.project_id}/documents", {}),
        ("GET", f"/api/v1/documents/{w.document_id}", {}),
        ("GET", f"/api/v1/documents/{w.document_id}/file", {}),
        ("GET", f"/api/v1/documents/{w.document_id}/facts", {}),
        ("GET", f"/api/v1/documents/{w.document_id}/versions", {}),
        ("GET", f"/api/v1/documents/{w.document_id}/versions/1/file", {}),
        ("GET", f"/api/v1/projects/{w.project_id}/search", {"params": {"q": "notice"}}),
        ("GET", f"/api/v1/projects/{w.project_id}/facts", {}),
        ("GET", f"/api/v1/passages/{w.passage_id}", {}),
        ("GET", f"/api/v1/projects/{w.project_id}/chats", {}),
        ("GET", f"/api/v1/projects/{w.project_id}/cross-checks", {}),
        ("GET", f"/api/v1/projects/{w.project_id}/check-runs", {}),
        ("GET", f"/api/v1/check-runs/{w.run_id}", {}),
        ("GET", f"/api/v1/check-runs/{w.run_id}/findings", {"params": {"rule": 0}}),
        ("GET", f"/api/v1/projects/{w.project_id}/reports", {}),
        ("GET", f"/api/v1/reports/{w.report_id}", {}),
    ]


def writes(w: World) -> list[Route]:
    """Every route that changes a project or what it holds, the destructive ones last."""
    return [
        ("PATCH", f"/api/v1/projects/{w.project_id}", {"json": {"name": "Renamed"}}),
        ("POST", f"/api/v1/projects/{w.project_id}/folders", {"json": {"name": "Unsigned"}}),
        ("PATCH", f"/api/v1/folders/{w.folder_id}", {"json": {"name": "Renamed"}}),
        (
            "POST",
            f"/api/v1/projects/{w.project_id}/documents",
            {"files": [("files", ("other.txt", b"Some other file."))]},
        ),
        ("PATCH", f"/api/v1/documents/{w.document_id}", {"json": {"filename": "renamed.pdf"}}),
        (
            "POST",
            f"/api/v1/documents/{w.document_id}/versions",
            {"files": {"file": ("msa.pdf", b"%PDF-1.7 another contract")}},
        ),
        ("POST", f"/api/v1/projects/{w.project_id}/check-runs", {"json": {}}),
        ("DELETE", f"/api/v1/folders/{w.folder_id}", {}),
        ("DELETE", f"/api/v1/documents/{w.document_id}", {}),
        ("DELETE", f"/api/v1/projects/{w.project_id}", {}),
    ]


def chat_reads(w: World) -> list[Route]:
    return [
        ("GET", f"/api/v1/chats/{w.chat_id}", {}),
        ("GET", f"/api/v1/chats/{w.chat_id}/messages", {}),
        ("GET", f"/api/v1/chats/{w.chat_id}/messages/{w.message_id}", {}),
        ("GET", f"/api/v1/chats/{w.chat_id}/messages/{w.message_id}/events", {}),
    ]


def chat_writes(w: World) -> list[Route]:
    return [
        ("PATCH", f"/api/v1/chats/{w.chat_id}", {"json": {"title": "Renamed"}}),
        ("POST", f"/api/v1/chats/{w.chat_id}/messages", {"json": {"content": "Again?"}}),
        ("POST", f"/api/v1/chats/{w.chat_id}/messages/{w.message_id}/stop", {}),
        ("DELETE", f"/api/v1/chats/{w.chat_id}", {}),
    ]


def starts(w: World) -> list[Route]:
    """Starting a chat, which is reading: a viewer may."""
    return [("POST", f"/api/v1/projects/{w.project_id}/chats", {})]


def scoped(w: World) -> list[Route]:
    return reads(w) + writes(w) + chat_reads(w) + chat_writes(w) + starts(w)


def statuses(api, routes: list[Route]) -> dict[str, int]:
    return {
        f"{method} {path}": api.request(method, path, **options).status_code
        for method, path, options in routes
    }


def test_every_route_that_names_what_it_acts_on_is_tried(client, world):
    """A route added with an id in its path joins the lists above, or this fails."""
    tried = [(method, path) for method, path, _ in scoped(world)]
    untried = [
        f"{method.upper()} {template}"
        for template, operations in contract()["paths"].items()
        if "{" in template
        for method in operations
        if not any(
            method.upper() == used and re.fullmatch(re.sub(r"\{\w+\}", r"[^/]+", template), path)
            for used, path in tried
        )
    ]
    assert untried == []


def test_another_organisations_key_finds_nothing(client, session, world, make_account):
    globex = make_account("eve@globex.com")
    eve = program(client, key_for(session, globex))
    assert got(eve, "/api/v1/projects")["data"] == []
    found = statuses(eve, scoped(world))
    assert set(found.values()) == {404}, found


def test_a_viewers_key_reads_and_starts_chats_but_changes_nothing(
    client, session, world, account, make_account
):
    vic = make_account("vic@example.com", account.organization, role="viewer")
    viewer = program(client, key_for(session, vic))
    seen = statuses(viewer, reads(world))
    assert set(seen.values()) == {200}, seen
    refused = statuses(viewer, writes(world))
    assert set(refused.values()) == {403}, refused
    # The owner's chat is the owner's alone, and the viewer's own is theirs.
    hidden = statuses(viewer, chat_reads(world) + chat_writes(world))
    assert set(hidden.values()) == {404}, hidden
    assert set(statuses(viewer, starts(world)).values()) == {201}


def test_a_read_key_reads_everything_and_changes_nothing(client, session, world, account):
    reader = program(client, key_for(session, account, scope="read"))
    seen = statuses(reader, reads(world) + chat_reads(world))
    assert set(seen.values()) == {200}, seen
    refused = statuses(reader, writes(world) + chat_writes(world) + starts(world))
    assert set(refused.values()) == {403}, refused
    assert reader.post("/api/v1/projects", json={"name": "Mine"}).json()["detail"] == READ_ONLY_KEY


def test_the_published_contract_is_the_routes():
    """The routes make the contract published beside them; a change is written there with
    `python -m app.api.v1.contract`, which refuses one that breaks a program."""
    made_now, kept = contract(), published()
    assert breaking_changes(kept, made_now) == []
    assert made_now == kept, (
        "The routes changed: write the contract with python -m app.api.v1.contract"
    )


def test_the_contract_is_served_to_anyone_naming_this_deployment(
    client, signed_in_only, monkeypatch
):
    # Whoever writes a program reads it before an owner gives them a key, and the clients
    # generated from it and the reference take their address from its server.
    anyone = TestClient(client.app)
    served = anyone.get("/api/v1/openapi.json")
    assert served.status_code == 200
    assert served.headers["cache-control"] == "no-store"
    document = served.json()
    assert document.pop("servers") == [{"url": "http://testserver"}]
    assert document == published()
    monkeypatch.setattr(get_settings(), "app_url", "https://docs.acme.example/")
    servers = anyone.get("/api/v1/openapi.json").json()["servers"]
    assert servers == [{"url": "https://docs.acme.example"}]


def test_the_contract_says_how_every_call_is_made_and_where_it_is_listed():
    # The key every route takes, which the reference and a generated client send, and the
    # group each operation and event is listed in, which the reference heads with its words.
    document = contract()
    scheme = document["components"]["securitySchemes"]["APIKey"]
    assert (scheme["type"], scheme["scheme"]) == ("http", "bearer")
    assert "`Authorization: Bearer emk_…`" in scheme["description"]
    groups = [tag["name"] for tag in TAGS]
    assert [tag["name"] for tag in document["tags"]] == groups
    for template, operations in document["paths"].items():
        for method, operation in operations.items():
            assert operation["security"] == [{"APIKey": []}], (method, template)
            assert operation["tags"] in ([group] for group in groups), (method, template)
    for event, operations in document["webhooks"].items():
        assert all(operation["tags"] == ["webhooks"] for operation in operations.values()), event
    used = {
        operation["tags"][0]
        for part in ("paths", "webhooks")
        for operations in document[part].values()
        for operation in operations.values()
    }
    assert used == set(groups), "A group lists nothing: take it out of TAGS."


def test_what_only_adds_breaks_no_program():
    before = published()
    after = copy.deepcopy(before)
    after["paths"]["/api/v1/tags"] = {"get": {"responses": {"200": {"description": "Tags."}}}}
    project = after["components"]["schemas"]["Project"]
    project["properties"]["colour"] = {"type": "string"}
    project["required"].append("colour")
    project["description"] = "Another description."
    after["components"]["schemas"]["NewProject"]["properties"]["colour"] = {"type": "string"}
    listing = after["paths"]["/api/v1/projects"]["get"]
    listing["parameters"].append(
        {"name": "q", "in": "query", "required": False, "schema": {"type": "string"}}
    )
    listing["responses"]["418"] = {"description": "A teapot."}
    after["webhooks"]["document.deleted"] = after["webhooks"]["document.failed"]
    assert breaking_changes(before, after) == []


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (
            lambda d: d["paths"]["/api/v1/projects"].pop("post"),
            "POST /api/v1/projects is gone.",
        ),
        (
            lambda d: d["components"]["schemas"]["Project"]["properties"].pop("sample"),
            "Project.sample is gone.",
        ),
        (
            lambda d: d["components"]["schemas"]["Project"]["properties"].update(
                id={"type": "string"}
            ),
            "Project.id takes other values.",
        ),
        (
            lambda d: d["components"]["schemas"]["Document"]["required"].remove("summary"),
            "Document no longer always holds summary.",
        ),
        (
            lambda d: d["components"]["schemas"]["NewProject"]["required"].append("description"),
            "NewProject now requires description.",
        ),
        (
            lambda d: d["paths"]["/api/v1/projects"]["get"]["parameters"].append(
                {"name": "q", "in": "query", "required": True, "schema": {"type": "string"}}
            ),
            "GET /api/v1/projects now requires the query parameter q.",
        ),
        (
            lambda d: d["paths"]["/api/v1/projects"]["get"]["parameters"].pop(0),
            "GET /api/v1/projects no longer takes the query parameter limit.",
        ),
        (
            lambda d: d["paths"]["/api/v1/projects"]["post"]["responses"].pop("201"),
            "POST /api/v1/projects no longer answers 201.",
        ),
        (
            lambda d: d["components"]["schemas"].pop("Passage"),
            "The schema Passage is gone.",
        ),
        (
            lambda d: d["webhooks"].pop("document.failed"),
            "The event document.failed is no longer sent.",
        ),
        (
            lambda d: d["webhooks"]["document.ready"]["post"]["parameters"].pop(0),
            "The event document.ready no longer carries the header Emudoc-Signature.",
        ),
        (
            lambda d: d["webhooks"]["check_run.completed"]["post"]["requestBody"]["content"][
                "application/json"
            ].update(schema={"$ref": "#/components/schemas/Document"}),
            "The event check_run.completed is sent with something else as application/json.",
        ),
        # A body a program receives is read as an answer is: a field it always held stays.
        (
            lambda d: d["components"]["schemas"]["DocumentEvent"]["required"].remove("data"),
            "DocumentEvent no longer always holds data.",
        ),
    ],
)
def test_what_takes_away_or_changes_breaks_programs(change, problem):
    before = published()
    after = copy.deepcopy(before)
    change(after)
    assert problem in breaking_changes(before, after)


def test_a_break_is_written_only_when_the_command_is_told_it_may(tmp_path, monkeypatch, capsys):
    """Refused, the published contract kept, unless `--breaking` says the change may break: a
    feature taken out of the app before 1.0 takes its fields out of /api/v1 too (S.6)."""
    kept = copy.deepcopy(published())
    kept["components"]["schemas"]["Project"]["properties"]["colour"] = {"type": "string"}
    kept["components"]["schemas"]["Project"]["required"].append("colour")
    file = tmp_path / "openapi.json"
    file.write_text(json.dumps(kept))
    monkeypatch.setattr(app.api.v1.contract, "PUBLISHED", file)

    assert write() == 1
    assert json.loads(file.read_text()) == kept
    assert "- Project.colour is gone." in capsys.readouterr().err
    assert write(breaking=True) == 0
    assert json.loads(file.read_text()) == contract()
    assert "- Project.colour is gone." in capsys.readouterr().err
    # Nothing left to break, it writes as it always did.
    assert write() == 0
