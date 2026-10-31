"""Drafts (app/drafts.py, app/api/drafts.py): documents the agent writes from a project's documents
for its members to edit and send, from the facts read from them and what the project's newest run
of cross-checks found about them, citing the passages those were read from, and kept with every
version the agent wrote or a member saved."""

import datetime as dt
from decimal import Decimal

import pytest
from pydantic_ai.messages import TextPart
from sqlalchemy import delete, func, select, update

from app import drafts
from app.config import get_settings
from app.db import engine
from app.draft_templates import BUILT_IN, kind_task
from app.drafts import (
    DRAFT_PROMPT_VERSION,
    DRAFT_TASK,
    NOTHING_WRITTEN,
    PROJECT_GONE,
    WRITE_FAILED,
    abandon_draft,
)
from app.flags import DRAFTS
from app.jobs import WRITE_DRAFT, claim
from app.models import Draft, Passage, UsageEvent
from app.retention import prune_drafts
from app.worker import HANDLERS
from tests.conftest import FakeTurn
from tests.test_api import create_project
from tests.test_audit import actions
from tests.test_chats import questions_counted
from tests.test_check_runs import TOTAL, cite, invoices, made, run, work
from tests.test_entitlements import member, plan  # noqa: F401 (fixtures)
from tests.test_spending import platform_refusal, spend
from tests.test_templates import turn_on

LETTER = {"kind": "dispute_letter", "instructions": "Ask for a credit note."}


@pytest.fixture(autouse=True)
def flags_on(monkeypatch):
    turn_on(monkeypatch)
    turn_on(monkeypatch, DRAFTS)


def ask(client, project_id: int, **body) -> dict:
    response = client.post(f"/api/projects/{project_id}/drafts", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def write(services) -> None:
    """Claim the next draft's job and run it as the worker's answerer would."""
    job = claim("w:1", [WRITE_DRAFT])
    assert job is not None
    HANDLERS[WRITE_DRAFT].run(job, services)


def shown(client, draft_id: int) -> dict:
    response = client.get(f"/api/drafts/{draft_id}")
    assert response.status_code == 200, response.text
    return response.json()


def saved(client, draft_id: int, text: str, base: int):
    return client.post(f"/api/drafts/{draft_id}/versions", json={"text": text, "base": base})


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


def test_a_draft_is_written_from_its_documents_facts_and_findings(
    client, session, services, account
):
    project = create_project(client)
    docs = disputed(client, session, services, project["id"])
    letter = (
        f"We dispute invoice INV-1: its total of 1,255.00 EUR [P{docs['invoiced']}] is 5 EUR "
        f"over the order's 1,250.00 EUR [P{docs['ordered']}]. [Your name]"
    )
    services.llm.agent_turns.append(FakeTurn(TextPart(letter)))

    queued = ask(client, project["id"], **LETTER, document_ids=[docs["INV-1"]])
    assert (queued["status"], queued["kind"], queued["title"]) == (
        "queued",
        "dispute_letter",
        "Dispute letter: INV-1.md",
    )
    assert (queued["versions"], queued["latest"], queued["history"]) == (0, None, [])
    assert queued["documents"] == [{"id": docs["INV-1"], "title": "INV-1.md", "gone": False}]
    assert queued["created_by"] == account.user.name
    # Asking for a draft counts as asking a question.
    assert questions_counted(session) == 1

    write(services)
    done = shown(client, queued["id"])
    assert (done["status"], done["error"], done["versions"]) == ("done", None, 1)
    assert done["run_id"] == docs["run"]
    latest = done["latest"]
    assert (latest["number"], latest["by_model"], latest["author"]) == (1, True, account.user.name)
    assert latest["text"] == letter
    # The passages it cites, as they were, in the order it first cites them.
    assert [source["id"] for source in latest["sources"]] == [docs["invoiced"], docs["ordered"]]
    assert latest["sources"][1]["excerpt"] == "Order total: 1,250.00 EUR."
    assert [version["number"] for version in done["history"]] == [1]
    assert done["usage"]["draft"]["calls"] == 1
    # Its calls were asked with the drafts' prompts.
    versions = session.scalars(
        select(UsageEvent.prompt_version).where(
            UsageEvent.ref_type == "draft", UsageEvent.ref_id == queued["id"]
        )
    ).all()
    assert set(versions) == {DRAFT_PROMPT_VERSION}

    # The agent was given the kind's task, the documents' facts with their passages, and what
    # the newest run found about them, in the server's words.
    assert services.llm.agent_prompts[-1].endswith(
        DRAFT_TASK + kind_task(BUILT_IN["dispute_letter"])
    )
    asked = services.llm.agent_requests[-1][0].parts[0].content
    assert asked.startswith("The draft to write: dispute letter.")
    assert "What the member asked of it:\nAsk for a credit note." in asked
    assert f'Document {docs["INV-1"]}: "INV-1.md" (INV-1.md), an invoice, disputed' in asked
    assert "- total_amount: 1,255.00 EUR (EUR)" in asked and f"[P{docs['invoiced']}]" in asked
    assert "Rule: Total within its purchase order" in asked
    assert f'"PO-7701.md" total_amount "1,250.00 EUR" [P{docs["ordered"]}]' in asked
    charged = session.scalars(
        select(UsageEvent).where(UsageEvent.kind == "draft", UsageEvent.unit == "calls")
    ).all()
    assert [(row.ref_type, row.ref_id) for row in charged] == [("draft", queued["id"])]


def test_a_draft_is_never_done_without_what_its_writing_used(
    client, session, services, monkeypatch
):
    # The page stops asking for a draft once it is done, so its usage is there by then.
    project = create_project(client)
    services.llm.agent_turns.append(FakeTurn(TextPart("Written.")))
    draft = ask(client, project["id"], kind="report")
    at_settle = []

    def settle(*args, **kwargs):
        with engine.connect() as connection:
            at_settle.append(connection.scalar(select(Draft.usage).where(Draft.id == draft["id"])))
        return settled(*args, **kwargs)

    settled = drafts._settle
    monkeypatch.setattr(drafts, "_settle", settle)
    write(services)
    assert at_settle[0]["draft"]["calls"] == 1
    done = shown(client, draft["id"])
    assert (done["status"], done["usage"]["draft"]["calls"]) == ("done", 1)


def test_a_member_saves_versions_and_an_older_one_reads_as_it_was(
    client, session, services, account
):
    project = create_project(client)
    docs = disputed(client, session, services, project["id"])
    written = f"Over by 5 EUR [P{docs['invoiced']}]."
    services.llm.agent_turns.append(FakeTurn(TextPart(written)))
    draft = ask(client, project["id"], **LETTER, document_ids=[docs["INV-1"]])
    write(services)

    # A passage the versions never cited stays plain text, kept by none.
    mine = f"Dear supplier, the total is over by 5 EUR [P{docs['invoiced']}] [P{docs['ordered']}]."
    kept = saved(client, draft["id"], mine, base=1)
    assert kept.status_code == 201, kept.text
    latest = kept.json()["latest"]
    assert (latest["number"], latest["by_model"], latest["text"]) == (2, False, mine)
    assert [source["id"] for source in latest["sources"]] == [docs["invoiced"]]
    assert [(v["number"], v["by_model"]) for v in kept.json()["history"]] == [(2, False), (1, True)]

    # Edited from the first while the second was saved: refused, nothing kept.
    stale = saved(client, draft["id"], "Another text.", base=1)
    assert stale.status_code == 409 and "newer version" in stale.json()["detail"]
    assert shown(client, draft["id"])["versions"] == 2

    # The documents are read again: their passages go, and the versions read as they were.
    session.execute(delete(Passage).where(Passage.id == docs["invoiced"]))
    session.commit()
    first = client.get(f"/api/drafts/{draft['id']}/versions/1").json()
    assert (first["text"], first["by_model"]) == (written, True)
    assert first["sources"][0]["excerpt"] == "Total due: 1,255.00 EUR."
    assert client.get(f"/api/drafts/{draft['id']}/versions/3").status_code == 404
    assert client.get(f"/api/drafts/{draft['id']}/versions/0").status_code == 422
    empty = saved(client, draft["id"], "", base=2)
    assert empty.status_code == 422


def test_a_draft_written_again_adds_a_version_with_what_is_asked_now(client, session, services):
    project = create_project(client)
    docs = disputed(client, session, services, project["id"])
    services.llm.agent_turns += [FakeTurn(TextPart("First.")), FakeTurn(TextPart("Shorter."))]
    draft = ask(client, project["id"], **LETTER, document_ids=[docs["INV-1"]])
    path = f"/api/drafts/{draft['id']}/rewrite"
    # Nothing is saved or written again while the model writes it.
    early = client.post(path, json={})
    assert early.status_code == 409 and "writing this draft now" in early.json()["detail"]
    assert saved(client, draft["id"], "Mine.", base=0).status_code == 409
    write(services)

    again = client.post(path, json={"instructions": "  Make it shorter.  "})
    assert again.status_code == 200, again.text
    assert (again.json()["status"], again.json()["instructions"]) == ("queued", "Make it shorter.")
    write(services)
    done = shown(client, draft["id"])
    assert [(v["number"], v["by_model"]) for v in done["history"]] == [(2, True), (1, True)]
    assert done["latest"]["text"] == "Shorter."
    assert done["usage"]["draft"]["calls"] == 2
    assert "Make it shorter." in services.llm.agent_requests[-1][0].parts[0].content
    # Kept as it was when none is given.
    services.llm.agent_turns.append(FakeTurn(TextPart("Third.")))
    assert client.post(path, json={}).json()["instructions"] == "Make it shorter."
    write(services)
    assert questions_counted(session) == 3


def test_a_draft_about_no_document_has_the_agent_find_them(client, session, services):
    project = create_project(client)
    services.llm.agent_turns.append(FakeTurn(TextPart("The contract allows 90 days.")))
    question = (
        "What does the contract say about late delivery, and what may we claim from the supplier "
        "for it?"
    )
    draft = ask(client, project["id"], kind="summary_memo", instructions=question)
    assert draft["title"] == (
        "Summary memo: What does the contract say about late delivery, and what may we claim from "
        "the…"
    )
    write(services)
    done = shown(client, draft["id"])
    assert (done["latest"]["text"], done["run_id"], done["documents"]) == (
        "The contract allows 90 days.",
        None,
        [],
    )
    asked = services.llm.agent_requests[-1][0].parts[0].content
    assert "No document was named: find those the request is about with your tools." in asked
    assert services.llm.agent_prompts[-1].endswith(kind_task(BUILT_IN["summary_memo"]))
    assert ask(client, project["id"], kind="report")["title"] == "Report"


def test_documents_named_are_the_projects_and_those_gone_since_are_said_so(
    client, session, services
):
    project = create_project(client)
    docs = invoices(session, project["id"])
    other = create_project(client, "Other")
    elsewhere = invoices(session, other["id"])
    path = f"/api/projects/{project['id']}/drafts"
    body = {"kind": "comparison_table", "document_ids": [docs["INV-1"], elsewhere["INV-2"]]}
    assert client.post(path, json=body).status_code == 404
    assert client.delete(f"/api/documents/{docs['INV-2']}").status_code == 204
    body["document_ids"] = [docs["INV-2"]]
    assert client.post(path, json=body).status_code == 404
    too_many = client.post(path, json={**body, "document_ids": list(range(1, 12))})
    assert too_many.status_code == 422

    services.llm.agent_turns.append(FakeTurn(TextPart("| Invoice | Total |")))
    draft = ask(
        client,
        project["id"],
        kind="comparison_table",
        document_ids=[docs["INV-1"], docs["PO-7701"]],
    )
    assert draft["title"] == "Comparison table: INV-1.md and 1 more"
    assert client.delete(f"/api/documents/{docs['PO-7701']}").status_code == 204
    write(services)
    done = shown(client, draft["id"])
    assert [(named["title"], named["gone"]) for named in done["documents"]] == [
        ("INV-1.md", False),
        ("PO-7701.md", True),
    ]
    asked = services.llm.agent_requests[-1][0].parts[0].content
    assert '"INV-1.md"' in asked and '"PO-7701.md"' not in asked
    assert "1 more of the documents asked about were deleted since, or are in the trash" in asked
    # No run had a finding about them.
    assert done["run_id"] is None and "cross-checks" not in asked


def test_drafts_are_listed_newest_first(client, session, services):
    project = create_project(client)
    ids = [
        ask(client, project["id"], kind="report", instructions=f"Part {n}")["id"] for n in (1, 2, 3)
    ]
    path = f"/api/projects/{project['id']}/drafts"
    listed = client.get(path, params={"limit": 2}).json()
    assert [d["id"] for d in listed["drafts"]] == [ids[2], ids[1]]
    assert (listed["drafts"][0]["title"], listed["drafts"][0]["versions"]) == ("Report: Part 3", 0)
    rest = client.get(path, params={"limit": 2, "before": listed["next_before"]}).json()
    assert ([d["id"] for d in rest["drafts"]], rest["next_before"]) == ([ids[0]], None)
    other = create_project(client, "Other")
    assert client.get(f"/api/projects/{other['id']}/drafts").json()["drafts"] == []


def test_a_draft_is_held_to_what_holds_a_question(
    client,
    session,
    services,
    member,  # noqa: F811
    plan,  # noqa: F811
    monkeypatch,
):
    project = create_project(client)
    path = f"/api/projects/{project['id']}/drafts"
    monkeypatch.setattr(get_settings(), "max_answers", 0)
    assert client.post(path, json={"kind": "report"}).status_code == 429
    monkeypatch.setattr(get_settings(), "max_answers", 20)

    plan(questions_per_month=0)
    refused = client.post(path, json={"kind": "report"})
    assert (refused.status_code, refused.json()["limit"]) == (402, "questions_per_month")
    plan(questions_per_month=100)

    monkeypatch.setattr(get_settings(), "daily_spend_cap", Decimal("2"))
    spend(session, member.organization.id, "3")
    over = client.post(path, json={"kind": "report"})
    assert (over.status_code, over.json()["detail"]) == (503, platform_refusal("3.00", "2.00"))
    # Nothing was asked for, and no question counted.
    assert session.scalar(select(func.count()).select_from(Draft)) == 0
    assert questions_counted(session) == 0


def test_a_writing_the_days_spending_catches_ends_with_its_sentence(
    client, session, services, monkeypatch, account
):
    project = create_project(client)
    draft = ask(client, project["id"], kind="report")
    monkeypatch.setattr(get_settings(), "daily_spend_cap", Decimal("2"))
    spend(session, account.organization.id, "3")
    write(services)
    done = shown(client, draft["id"])
    assert (done["status"], done["error"]) == ("failed", platform_refusal("3.00", "2.00"))
    assert (done["latest"], done["usage"]) == (None, {})


def test_a_writing_that_breaks_or_is_lost_ends_failed_keeping_the_versions(
    client, session, services, monkeypatch
):
    project = create_project(client)
    services.llm.agent_turns.append(FakeTurn(TextPart("Kept.")))
    draft = ask(client, project["id"], kind="report")
    write(services)

    def wordless(*args, **kwargs):
        yield {"type": "notice", "message": "Stopped after 12 rounds of tool calls."}
        yield {"type": "done", "usage": {}}

    # A writing that ends with no text fails rather than keep an empty version.
    monkeypatch.setattr("app.drafts.run_agent", wordless)
    client.post(f"/api/drafts/{draft['id']}/rewrite", json={})
    write(services)
    empty = shown(client, draft["id"])
    assert (empty["status"], empty["error"]) == ("failed", NOTHING_WRITTEN)
    assert empty["notice"] == "Stopped after 12 rounds of tool calls."

    def broken(*args, **kwargs):
        raise RuntimeError("the model loop broke")

    monkeypatch.setattr("app.drafts.run_agent", broken)
    client.post(f"/api/drafts/{draft['id']}/rewrite", json={})
    with pytest.raises(RuntimeError):
        write(services)
    failed = shown(client, draft["id"])
    assert (failed["status"], failed["error"]) == ("failed", WRITE_FAILED)
    assert (failed["versions"], failed["latest"]["text"]) == (1, "Kept.")

    client.post(f"/api/drafts/{draft['id']}/rewrite", json={})
    abandon_draft(draft["id"], 3)
    assert shown(client, draft["id"])["error"] == (
        "It stopped unexpectedly 3 times and was not tried again. Try again in a moment."
    )


def test_a_project_put_in_the_trash_before_the_writing_fails_it(client, session, services):
    project = create_project(client)
    draft = ask(client, project["id"], kind="report")
    assert client.delete(f"/api/projects/{project['id']}").status_code == 204
    write(services)
    kept = session.get(Draft, draft["id"], populate_existing=True)
    assert (kept.status, kept.error) == ("failed", PROJECT_GONE)
    assert client.get(f"/api/drafts/{draft['id']}").status_code == 404


def test_a_draft_is_audited_by_its_kind_never_what_it_says(client, session, services, account):
    project = create_project(client)
    docs = invoices(session, project["id"])
    services.llm.agent_turns.append(FakeTurn(TextPart("INV-1 is over.")))
    draft = ask(client, project["id"], **LETTER, document_ids=[docs["INV-1"]])
    write(services)
    assert saved(client, draft["id"], "INV-1 is over by 5 EUR.", base=1).status_code == 201
    assert client.post(f"/api/drafts/{draft['id']}/rewrite", json={}).status_code == 200
    # Deleted before the model writes it again: the job finds nothing to write, and the first
    # writing's calls stay counted with the organisation.
    assert client.delete(f"/api/drafts/{draft['id']}").status_code == 204
    write(services)
    assert client.get(f"/api/drafts/{draft['id']}").status_code == 404
    assert session.scalar(select(func.count()).select_from(Draft)) == 0
    assert (
        session.scalar(
            select(func.count())
            .select_from(UsageEvent)
            .where(UsageEvent.kind == "draft", UsageEvent.unit == "calls")
        )
        == 1
    )
    logged = actions(session, account.organization.id)
    assert logged[-4:] == [
        ("draft_created", {"kind": "dispute_letter", "documents": 1}),
        ("draft_changed", {"kind": "dispute_letter", "version": 2}),
        ("draft_changed", {"kind": "dispute_letter", "rewritten": True}),
        ("draft_deleted", {"kind": "dispute_letter"}),
    ]
    assert "INV-1" not in str(logged) and "credit note" not in str(logged)


def test_drafts_are_kept_as_long_as_chats(client, session, services, member):  # noqa: F811
    project = create_project(client)
    services.llm.agent_turns.append(FakeTurn(TextPart("Old.")))
    old = ask(client, project["id"], kind="report")
    write(services)
    queued = ask(client, project["id"], kind="report")
    # The free plan keeps a chat 30 days; the written draft is older, the queued one too.
    session.execute(update(Draft).values(updated_at=func.now() - dt.timedelta(days=31)))
    session.commit()
    with engine.begin() as connection:
        assert prune_drafts(connection) == 1
    session.expire_all()
    assert session.get(Draft, old["id"]) is None
    assert session.get(Draft, queued["id"]) is not None


def test_drafts_are_behind_their_flag(client, monkeypatch):
    project = create_project(client)
    monkeypatch.undo()
    turn_on(monkeypatch)
    assert (
        client.post(f"/api/projects/{project['id']}/drafts", json={"kind": "report"}).status_code
        == 404
    )
    assert client.get(f"/api/projects/{project['id']}/drafts").status_code == 404
