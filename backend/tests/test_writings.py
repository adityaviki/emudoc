"""Writings (app/writings.py, the routes in app/api/my_files.py): a document of a kind the agent
writes into a member's My files outside a chat, a disputed document's dispute letter, from the
facts read from its documents and what the project's newest run of cross-checks found about them,
citing the passages those were read from, to the project's template of the kind. It is listed
while it is written, and once failed until it is tried again or dismissed; written, it is a Word
file of the member's like any other."""

import io
from decimal import Decimal

import pytest
from docx import Document as WordDocument
from pydantic_ai.messages import TextPart
from sqlalchemy import func, select

from app import writings
from app.api.chats import TOO_MANY_ANSWERS
from app.config import get_settings
from app.db import engine
from app.draft_templates import BUILT_IN, kind_task
from app.jobs import WRITE_DRAFT, claim, enqueue
from app.models import MyFile, MyFileWriting, UsageEvent
from app.my_files import TOO_MANY, drop_files
from app.worker import HANDLERS
from app.writings import (
    DRAFT_PROMPT_VERSION,
    DRAFT_TASK,
    NOTHING_WRITTEN,
    PROJECT_GONE,
    WRITE_FAILED,
    abandon_writing,
)
from tests.conftest import FakeTurn
from tests.test_api import create_project
from tests.test_audit import actions
from tests.test_chats import ask as ask_question
from tests.test_chats import new_chat, questions_counted
from tests.test_check_runs import TOTAL, cite, invoices, made, run, work
from tests.test_spending import refusal, spend


def asking(client, project_id: int, document_ids: list[int], kind: str = "dispute_letter"):
    return client.post(
        f"/api/projects/{project_id}/my-files/writings",
        json={"kind": kind, "document_ids": document_ids},
    )


def ask(client, project_id: int, document_ids: list[int], kind: str = "dispute_letter") -> dict:
    response = asking(client, project_id, document_ids, kind)
    assert response.status_code == 201, response.text
    return response.json()


def write(services) -> None:
    """Claim the next writing's job and run it as the worker's answerer would."""
    job = claim("w:1", [WRITE_DRAFT])
    assert job is not None
    HANDLERS[WRITE_DRAFT].run(job, services)


def listed(client, project_id: int) -> dict:
    response = client.get(f"/api/projects/{project_id}/my-files")
    assert response.status_code == 200, response.text
    return response.json()


def text_of(client, file_id: int) -> str:
    response = client.get(f"/api/my-files/{file_id}/versions/1/file")
    assert response.status_code == 200, response.text
    return "\n".join(each.text for each in WordDocument(io.BytesIO(response.content)).paragraphs)


def disputed(client, session, services, project_id: int) -> dict[str, int]:
    """The invoices of tests/test_check_runs.py with the totals' passages, a run of the totals'
    check that found INV-1 5 EUR over its order, and INV-1 disputed."""
    docs = invoices(session, project_id)
    docs["invoiced"] = cite(session, docs["INV-1"], "total_amount", "Total due: 1,255.00 EUR.")
    docs["ordered"] = cite(session, docs["PO-7701"], "total_amount", "Order total: 1,250.00 EUR.")
    made(client, project_id, TOTAL)
    docs["run"] = run(client, project_id)["id"]
    work(services)
    review = client.put(f"/api/documents/{docs['INV-1']}/review", json={"state": "disputed"})
    assert review.status_code == 200, review.text
    return docs


def test_a_dispute_letter_is_written_into_my_files_from_its_documents_facts_and_findings(
    client, session, services, account
):
    project = create_project(client)
    docs = disputed(client, session, services, project["id"])
    letter = (
        f"We dispute invoice INV-1: its total of 1,255.00 EUR [P{docs['invoiced']}] is 5 EUR "
        f"over the order's 1,250.00 EUR [P{docs['ordered']}].\n\n[Your name]"
    )
    services.llm.agent_turns.append(FakeTurn(TextPart(letter)))

    queued = ask(client, project["id"], [docs["INV-1"]])
    assert (queued["status"], queued["kind"], queued["title"], queued["error"]) == (
        "queued",
        "dispute_letter",
        "Dispute letter about INV-1.md",
        None,
    )
    # Asking counts as asking a question, and the member's My files list it while it is written.
    assert questions_counted(session) == 1
    assert [each["id"] for each in listed(client, project["id"])["writings"]] == [queued["id"]]

    write(services)
    mine = listed(client, project["id"])
    assert mine["writings"] == []
    (file,) = mine["files"]
    assert (file["name"], file["kind"], file["versions"]) == (
        "Dispute letter about INV-1.md.docx",
        "docx",
        1,
    )
    assert (file["latest"]["by_model"], file["latest"]["note"], file["latest"]["scan"]) == (
        True,
        "Dispute letter",
        "skipped",
    )
    # The letter as the agent wrote it, its citations numbered notes naming the passages.
    text = text_of(client, file["id"])
    assert "We dispute invoice INV-1: its total of 1,255.00 EUR" in text
    assert "[P" not in text and "[Your name]" in text
    assert "Sources" in text and "INV-1.md" in text and "PO-7701.md" in text

    # The agent was given the kind's task and template, the documents' facts with their
    # passages, and what the newest run found about them, in the server's words.
    assert services.llm.agent_prompts[-1].endswith(
        DRAFT_TASK + kind_task(BUILT_IN["dispute_letter"])
    )
    given = services.llm.agent_requests[-1][0].parts[0].content
    assert given.startswith("The document to write: dispute letter.")
    assert f'Document {docs["INV-1"]}: "INV-1.md" (INV-1.md), an invoice, disputed' in given
    assert "- total_amount: 1,255.00 EUR (EUR)" in given and f"[P{docs['invoiced']}]" in given
    assert "Rule: Total within its purchase order" in given
    assert f'"PO-7701.md" total_amount "1,250.00 EUR" [P{docs["ordered"]}]' in given
    # What the documents say and what the checks found are fenced as data, the server's words
    # left outside as the message's one instruction.
    told, documents, found = given.split("<project_data>\n")
    assert "<project_data>" not in told
    assert documents.startswith(f'Document {docs["INV-1"]}: "INV-1.md"')
    assert documents.endswith("</project_data>\n\n") and found.endswith("</project_data>")
    # Its calls are the member's usage, asked with the writings' prompts.
    charged = session.scalars(
        select(UsageEvent).where(UsageEvent.kind == "draft", UsageEvent.unit == "calls")
    ).all()
    assert [(row.user_id, row.ref_type, row.prompt_version) for row in charged] == [
        (account.user.id, None, DRAFT_PROMPT_VERSION)
    ]
    # The member's row of the audit log names the file by its kind, and what was written.
    added = [detail for action, detail in actions(session) if action == "my_file_added"]
    assert added == [
        {
            "kind": "docx",
            "by_model": True,
            "writing": "dispute_letter",
            "size_bytes": file["latest"]["size_bytes"],
        }
    ]


def test_a_writing_is_never_settled_before_what_it_used(client, session, services, monkeypatch):
    # The page stops asking once the file is there, so the usage is there by then.
    project = create_project(client)
    docs = invoices(session, project["id"])
    services.llm.agent_turns.append(FakeTurn(TextPart("Written.")))
    ask(client, project["id"], [docs["INV-1"]], "report")
    at_settle = []

    def settle(*args, **kwargs):
        with engine.connect() as connection:
            at_settle.append(
                connection.scalar(
                    select(func.count()).select_from(UsageEvent).where(UsageEvent.kind == "draft")
                )
            )
        return settled(*args, **kwargs)

    settled = writings._settle
    monkeypatch.setattr(writings, "_settle", settle)
    write(services)
    assert at_settle[0] > 0
    assert listed(client, project["id"])["files"][0]["name"] == "Report about INV-1.md.docx"


def test_a_writing_is_held_to_what_holds_a_question(
    client, session, services, account, monkeypatch
):
    project = create_project(client)
    docs = invoices(session, project["id"])
    other = create_project(client, "Other")
    stranger = invoices(session, other["id"])["INV-1"]
    assert asking(client, project["id"], [stranger]).status_code == 404
    assert asking(client, project["id"], []).status_code == 422
    assert asking(client, project["id"], [docs["INV-1"]], "sonnet").status_code == 422

    monkeypatch.setattr(get_settings(), "max_answers", 0)
    assert asking(client, project["id"], [docs["INV-1"]]).status_code == 429
    monkeypatch.setattr(get_settings(), "max_answers", 20)

    monkeypatch.setattr("app.api.my_files.MAX_FILES", 0)
    full = asking(client, project["id"], [docs["INV-1"]])
    assert (full.status_code, full.json()["detail"]) == (409, TOO_MANY)
    monkeypatch.setattr("app.api.my_files.MAX_FILES", 200)

    monkeypatch.setattr(get_settings(), "daily_spend_cap", Decimal("2"))
    spend(session, account.organization.id, "3")
    over = asking(client, project["id"], [docs["INV-1"]])
    assert (over.status_code, over.json()["detail"]) == (503, refusal("3.00", "2.00"))
    # Nothing was asked for, and no question counted.
    assert session.scalar(select(func.count()).select_from(MyFileWriting)) == 0
    assert questions_counted(session) == 0


def test_a_writing_under_way_is_one_of_the_agents_runs_in_flight(
    client, session, services, monkeypatch
):
    project = create_project(client)
    docs = invoices(session, project["id"])
    ask(client, project["id"], [docs["INV-1"]])
    monkeypatch.setattr(get_settings(), "max_answers", 1)
    chat = new_chat(client, project["id"])
    refused = client.post(f"/api/chats/{chat['id']}/messages", json={"content": "Hello?"})
    assert (refused.status_code, refused.json()["detail"]) == (429, TOO_MANY_ANSWERS)
    services.llm.agent_turns.append(FakeTurn(TextPart("Written.")))
    write(services)
    assert ask_question(client, chat["id"], "Hello?")["answer"]["status"] == "streaming"


def test_a_writing_that_fails_says_why_and_is_tried_again_or_dismissed(
    client, session, services, monkeypatch, account
):
    project = create_project(client)
    docs = invoices(session, project["id"])
    writing = ask(client, project["id"], [docs["INV-1"]])
    path = f"/api/my-file-writings/{writing['id']}"
    # Tried again while it waits, it is refused: it is being written.
    assert client.post(f"{path}/retry").status_code == 409

    # The day's spending caught it as it started.
    monkeypatch.setattr(get_settings(), "daily_spend_cap", Decimal("2"))
    spend(session, account.organization.id, "3")
    write(services)
    (failed,) = listed(client, project["id"])["writings"]
    assert (failed["status"], failed["error"]) == ("failed", refusal("3.00", "2.00"))
    assert listed(client, project["id"])["files"] == []
    monkeypatch.setattr(get_settings(), "daily_spend_cap", Decimal("0"))

    def wordless(*args, **kwargs):
        yield {"type": "notice", "message": "Stopped after 12 rounds of tool calls."}
        yield {"type": "done", "usage": {}}

    # Tried again, it counts a question; a writing that ends with no text fails rather than keep
    # an empty file.
    again = client.post(f"{path}/retry")
    assert (again.status_code, again.json()["status"]) == (200, "queued")
    assert questions_counted(session) == 2
    monkeypatch.setattr("app.writings.run_agent", wordless)
    write(services)
    assert listed(client, project["id"])["writings"][0]["error"] == NOTHING_WRITTEN

    def broken(*args, **kwargs):
        raise RuntimeError("the model loop broke")

    monkeypatch.setattr("app.writings.run_agent", broken)
    client.post(f"{path}/retry")
    with pytest.raises(RuntimeError):
        write(services)
    assert listed(client, project["id"])["writings"][0]["error"] == WRITE_FAILED

    client.post(f"{path}/retry")
    abandon_writing(writing["id"], 3)
    assert listed(client, project["id"])["writings"][0]["error"] == (
        "It stopped unexpectedly 3 times and was not tried again. Try again in a moment."
    )

    # Dismissed while it is written again, the job keeps nothing.
    client.post(f"{path}/retry")
    assert client.delete(path).status_code == 204
    assert listed(client, project["id"])["writings"] == []
    assert client.delete(path).status_code == 404
    monkeypatch.undo()
    services.llm.agent_turns.append(FakeTurn(TextPart("Written.")))
    write(services)
    assert listed(client, project["id"])["files"] == []


def test_a_writing_my_files_cannot_take_fails_with_why(client, session, services, monkeypatch):
    project = create_project(client)
    docs = invoices(session, project["id"])
    ask(client, project["id"], [docs["INV-1"]])
    monkeypatch.setattr("app.my_files.MAX_FILES", 0)
    services.llm.agent_turns.append(FakeTurn(TextPart("Written.")))
    write(services)
    (failed,) = listed(client, project["id"])["writings"]
    assert (failed["status"], failed["error"]) == ("failed", TOO_MANY)
    assert session.scalar(select(func.count()).select_from(MyFile)) == 0


def test_a_project_put_in_the_trash_before_the_writing_fails_it(client, session, services):
    project = create_project(client)
    docs = invoices(session, project["id"])
    writing = ask(client, project["id"], [docs["INV-1"]])
    assert client.delete(f"/api/projects/{project['id']}").status_code == 204
    write(services)
    kept = session.get(MyFileWriting, writing["id"], populate_existing=True)
    assert (kept.status, kept.error) == ("failed", PROJECT_GONE)
    assert client.delete(f"/api/my-file-writings/{writing['id']}").status_code == 404


def test_documents_gone_since_are_left_out_and_said_so(client, session, services):
    project = create_project(client)
    docs = invoices(session, project["id"])
    ask(client, project["id"], [docs["INV-1"], docs["PO-7701"]], "comparison_table")
    assert client.delete(f"/api/documents/{docs['PO-7701']}").status_code == 204
    services.llm.agent_turns.append(FakeTurn(TextPart("Compared.")))
    write(services)
    given = services.llm.agent_requests[-1][0].parts[0].content
    assert f"Document {docs['INV-1']}:" in given and f"Document {docs['PO-7701']}:" not in given
    assert "1 more of the documents asked about were deleted since" in given
    names = [file["name"] for file in listed(client, project["id"])["files"]]
    assert names == ["Comparison table about INV-1.md and 1 more.docx"]


def test_a_job_from_before_the_drafts_went_writes_nothing(session, services):
    enqueue(session, WRITE_DRAFT, org_id=None, payload={"draft_id": 7, "user_id": 1})
    session.commit()
    job = claim("w:1", [WRITE_DRAFT])
    assert HANDLERS[WRITE_DRAFT].run(job, services) is None
    HANDLERS[WRITE_DRAFT].abandon(job, services)
    assert session.scalar(select(func.count()).select_from(MyFileWriting)) == 0


def test_a_member_leaving_takes_their_writings(client, session, services, account):
    project = create_project(client)
    docs = invoices(session, project["id"])
    ask(client, project["id"], [docs["INV-1"]])
    drop_files(session, account.organization.id, account.user.id)
    session.commit()
    assert session.scalar(select(func.count()).select_from(MyFileWriting)) == 0
