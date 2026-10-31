"""Runs of a project's cross-checks (app/check_runs.py, app/api/check_runs.py): the rules worked
out by the worker at a moment and kept with what they found, as it was found, and the discrepancy
report the agent writes from that, citing the passages the values were read from."""

import datetime as dt
from decimal import Decimal

import pytest
from pydantic_ai.messages import TextPart
from sqlalchemy import func, select, update

from app.check_runs import (
    PROJECT_GONE,
    REPORT_PROMPT_VERSION,
    REPORT_TASK,
    RUN_FAILED,
    abandon_run,
)
from app.config import get_settings
from app.db import engine
from app.jobs import RUN_WORKFLOW, claim
from app.models import Fact, Passage, Section, UsageEvent, WorkflowFinding, WorkflowRun
from app.retention import prune_runs
from app.worker import HANDLERS
from tests.conftest import FakeTurn
from tests.test_api import create_project
from tests.test_audit import actions
from tests.test_chats import questions_counted
from tests.test_cross_checks import DUE, read
from tests.test_entitlements import member, plan  # noqa: F401 (fixtures)
from tests.test_spending import platform_refusal, spend
from tests.test_templates import turn_on

TOTAL = {
    "title": "Total within its purchase order",
    "left_kind": "invoice",
    "left_field": "total_amount",
    "right_kind": "purchase order",
    "right_field": "total_amount",
    "pairing": "key",
    "left_key": "po_number",
    "right_key": "po_number",
    "comparison": "at_most",
}


@pytest.fixture(autouse=True)
def templates_on(monkeypatch):
    turn_on(monkeypatch)


def invoices(session, project_id: int) -> dict[str, int]:
    """Two invoices and the purchase order of one: INV-1 is 5 EUR over its order and due 45 days
    after it was issued; INV-2 has no order in the project and is due in 19 days."""
    return {
        "INV-1": read(
            session,
            project_id,
            "INV-1.md",
            "invoice",
            po_number="PO-7701",
            total_amount=("1,255.00 EUR", 1255.0, "EUR"),
            issue_date=("1 March 2026", dt.date(2026, 3, 1)),
            due_date=("15 April 2026", dt.date(2026, 4, 15)),
        ),
        "INV-2": read(
            session,
            project_id,
            "INV-2.md",
            "invoice",
            po_number="PO-7702",
            total_amount=("90.00 EUR", 90.0, "EUR"),
            issue_date=("1 March 2026", dt.date(2026, 3, 1)),
            due_date=("20 March 2026", dt.date(2026, 3, 20)),
        ),
        "PO-7701": read(
            session,
            project_id,
            "PO-7701.md",
            "purchase order",
            po_number="po 7701",
            total_amount=("1,250.00 EUR", 1250.0, "EUR"),
        ),
    }


def made(client, project_id: int, *rules: dict) -> list[int]:
    ids = []
    for rule in rules:
        response = client.post(f"/api/projects/{project_id}/cross-checks", json=rule)
        assert response.status_code == 201, response.text
        ids.append(response.json()["id"])
    return ids


def run(client, project_id: int, **body) -> dict:
    response = client.post(f"/api/projects/{project_id}/check-runs", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def work(services) -> None:
    """Claim the next run's job and run it as the worker's answerer would."""
    job = claim("w:1", [RUN_WORKFLOW])
    assert job is not None
    HANDLERS[RUN_WORKFLOW].run(job, services)


def shown(client, run_id: int) -> dict:
    response = client.get(f"/api/check-runs/{run_id}")
    assert response.status_code == 200, response.text
    return response.json()


def found(client, run_id: int, rule: int, **params) -> dict:
    response = client.get(f"/api/check-runs/{run_id}/findings", params={"rule": rule, **params})
    assert response.status_code == 200, response.text
    return response.json()


def cite(session, document_id: int, name: str, text: str) -> int:
    """A passage of the document holding `text`, which its fact `name` was read from."""
    project_id = session.scalar(
        select(Fact.project_id).where(Fact.document_id == document_id).limit(1)
    )
    section = Section(
        document_id=document_id,
        project_id=project_id,
        ordinal=0,
        level=1,
        title="Totals",
        path="Totals",
        block_start=0,
        block_end=9,
    )
    session.add(section)
    session.flush()
    passage = Passage(
        document_id=document_id,
        project_id=project_id,
        section_id=section.id,
        ordinal=0,
        text=text,
        context="Totals",
        page_start=1,
        page_end=1,
        block_start=0,
        block_end=9,
    )
    session.add(passage)
    session.flush()
    session.execute(
        update(Fact)
        .where(Fact.document_id == document_id, Fact.name == name)
        .values(passage_id=passage.id)
    )
    session.commit()
    return passage.id


def test_a_run_keeps_what_each_rule_found_as_it_was_found(client, session, services, account):
    project = create_project(client)
    docs = invoices(session, project["id"])
    due, total = made(client, project["id"], DUE, TOTAL)

    queued = run(client, project["id"])
    assert (queued["status"], queued["report_status"], queued["counts"]) == ("queued", None, None)
    assert queued["titles"] == ["Due within 30 days of issue", "Total within its purchase order"]
    assert [rule["check_id"] for rule in queued["rules"]] == [due, total]
    assert queued["rules"][1]["left_type"] == "money" and queued["rules"][1]["counts"] is None
    assert queued["run_by"] == account.user.name

    work(services)
    done = shown(client, queued["id"])
    assert done["status"] == "done" and done["finished_at"] is not None
    assert done["counts"] == {"differs": 2, "missing": 1, "holds": 1}
    assert [rule["counts"] for rule in done["rules"]] == [
        {"differs": 1, "missing": 0, "holds": 1},
        {"differs": 1, "missing": 1, "holds": 0},
    ]
    totals = found(client, queued["id"], 1)
    assert [(f["title"], f["outcome"], f["reason"]) for f in totals["findings"]] == [
        ("INV-1.md", "differs", "differs"),
        ("INV-2.md", "missing", "no_partner"),
    ]
    first = totals["findings"][0]
    assert first["left"]["value"] == "1,255.00 EUR" and first["difference"] == 5.0
    assert [fact["value"] for fact in first["right"]] == ["1,250.00 EUR"]
    assert first["partners"] == [{"document_id": docs["PO-7701"], "title": "PO-7701.md"}]
    assert (totals["total"], totals["gone"], totals["rule"]["title"]) == (2, [], TOTAL["title"])
    differing = found(client, queued["id"], 1, outcome="differs")
    assert [f["title"] for f in differing["findings"]] == ["INV-1.md"]
    assert differing["total"] == 1

    # The order is read again with another total and the rules change: the run is what it was.
    session.execute(
        update(Fact)
        .where(Fact.document_id == docs["PO-7701"], Fact.name == "total_amount")
        .values(value="1,300.00 EUR", number=1300.0)
    )
    session.commit()
    client.put(f"/api/cross-checks/{total}", json={**TOTAL, "title": "Renamed"})
    assert client.delete(f"/api/cross-checks/{due}").status_code == 204
    again = found(client, queued["id"], 1)
    assert again["rule"]["title"] == TOTAL["title"]
    assert [fact["value"] for fact in again["findings"][0]["right"]] == ["1,250.00 EUR"]
    assert shown(client, queued["id"])["titles"] == done["titles"]
    live = client.get(f"/api/cross-checks/{total}/findings").json()
    assert {f["title"]: f["reason"] for f in live["findings"]}["INV-1.md"] == "holds"


def test_the_projects_runs_are_listed_newest_first(client, session, services):
    project = create_project(client)
    invoices(session, project["id"])
    made(client, project["id"], DUE, TOTAL)
    ids = []
    for _ in range(3):
        ids.append(run(client, project["id"])["id"])
        work(services)
    listed = client.get(f"/api/projects/{project['id']}/check-runs", params={"limit": 2}).json()
    assert [r["id"] for r in listed["runs"]] == [ids[2], ids[1]]
    assert listed["runs"][0]["counts"] == {"differs": 2, "missing": 1, "holds": 1}
    rest = client.get(
        f"/api/projects/{project['id']}/check-runs",
        params={"limit": 2, "before": listed["next_before"]},
    ).json()
    assert ([r["id"] for r in rest["runs"]], rest["next_before"]) == ([ids[0]], None)


def test_a_run_of_some_rules_and_the_refusals_of_one(client, session, services):
    project = create_project(client)
    path = f"/api/projects/{project['id']}/check-runs"
    nothing = client.post(path, json={})
    assert (nothing.status_code, nothing.json()["detail"]) == (
        400,
        "This project has no cross-checks to run. Add one first.",
    )
    invoices(session, project["id"])
    due, _ = made(client, project["id"], DUE, TOTAL)
    assert client.post(path, json={"check_ids": [due, 999999]}).status_code == 404

    one = run(client, project["id"], check_ids=[due])
    assert one["titles"] == [DUE["title"]]
    # One run of a project at a time: the next waits for the worker to finish this one.
    busy = client.post(path, json={})
    assert busy.status_code == 409 and "being run already" in busy.json()["detail"]
    work(services)
    assert run(client, project["id"])["titles"] == [DUE["title"], TOTAL["title"]]

    other = create_project(client, "Other")
    made(client, other["id"], DUE)
    assert (
        client.post(
            f"/api/projects/{other['id']}/check-runs", json={"check_ids": [due]}
        ).status_code
        == 404
    )


def test_a_rule_the_templates_cannot_work_out_is_kept_with_why(client, session, services):
    project = create_project(client)
    invoices(session, project["id"])
    made(client, project["id"], DUE)
    queued = run(client, project["id"])
    # Before the worker gets to it the rule's field leaves the project's invoice template.
    template = client.get(f"/api/projects/{project['id']}/templates").json()["templates"]
    invoice = next(t for t in template if t["doc_type"] == "invoice")
    fields = [f for f in invoice["fields"] if f["name"] != "issue_date"]
    body = {key: invoice[key] for key in ("doc_type", "title", "description")}
    assert (
        client.post(
            f"/api/projects/{project['id']}/templates", json={**body, "fields": fields}
        ).status_code
        == 201
    )

    work(services)
    done = shown(client, queued["id"])
    assert done["rules"][0]["problem"] == "The Invoice template has no field named issue_date."
    assert done["rules"][0]["counts"] is None
    assert done["counts"] == {"differs": 0, "missing": 0, "holds": 0}
    assert found(client, queued["id"], 0)["findings"] == []
    assert (
        client.get(f"/api/check-runs/{queued['id']}/findings", params={"rule": 1}).status_code
        == 404
    )


def test_documents_gone_since_the_run_are_named_so(client, session, services):
    project = create_project(client)
    docs = invoices(session, project["id"])
    made(client, project["id"], TOTAL)
    queued = run(client, project["id"])
    work(services)
    assert client.delete(f"/api/documents/{docs['PO-7701']}").status_code == 204
    page = found(client, queued["id"], 0)
    assert page["gone"] == [docs["PO-7701"]]
    assert page["findings"][0]["right"][0]["value"] == "1,250.00 EUR"


def test_a_project_put_in_the_trash_before_its_run_fails_it(client, session, services):
    project = create_project(client)
    invoices(session, project["id"])
    made(client, project["id"], TOTAL)
    queued = run(client, project["id"], report=True)
    assert client.delete(f"/api/projects/{project['id']}").status_code == 204
    work(services)
    kept = session.get(WorkflowRun, queued["id"], populate_existing=True)
    assert (kept.status, kept.error) == ("failed", PROJECT_GONE)
    assert (kept.report_status, kept.report_error) == ("failed", PROJECT_GONE)
    assert client.get(f"/api/check-runs/{queued['id']}").status_code == 404


def test_the_report_is_written_from_the_findings_citing_their_passages(client, session, services):
    project = create_project(client)
    docs = invoices(session, project["id"])
    invoiced = cite(session, docs["INV-1"], "total_amount", "Total due: 1,255.00 EUR.")
    ordered = cite(session, docs["PO-7701"], "total_amount", "Order total: 1,250.00 EUR.")
    made(client, project["id"], TOTAL)
    services.llm.agent_turns.append(
        FakeTurn(TextPart(f"INV-1.md is 5 EUR over its order [P{invoiced}] [P{ordered}]."))
    )

    queued = run(client, project["id"], report=True)
    assert queued["report_status"] == "queued"
    # Asking for a report counts as asking a question.
    assert questions_counted(session) == 1
    work(services)

    done = shown(client, queued["id"])
    assert (done["status"], done["report_status"]) == ("done", "done")
    assert done["report"] == f"INV-1.md is 5 EUR over its order [P{invoiced}] [P{ordered}]."
    sources = {source["id"]: source for source in done["report_sources"]}
    assert sources[invoiced]["document_title"] == "INV-1.md"
    assert sources[ordered]["excerpt"] == "Order total: 1,250.00 EUR."
    assert done["usage"]["report"]["calls"] == 1
    assert done["usage"]["report"]["input_tokens"] == 10
    versions = session.scalars(
        select(UsageEvent.prompt_version).where(
            UsageEvent.ref_type == "workflow_run", UsageEvent.ref_id == queued["id"]
        )
    ).all()
    assert set(versions) == {REPORT_PROMPT_VERSION}

    # The agent was given the task and the findings in the server's words, with their passages.
    assert services.llm.agent_prompts[-1].endswith(REPORT_TASK)
    asked = services.llm.agent_requests[-1][0].parts[0].content
    assert "Rule 1: Total within its purchase order" in asked
    assert f'total_amount "1,255.00 EUR" [P{invoiced}]' in asked
    assert f'"PO-7701.md" total_amount "1,250.00 EUR" [P{ordered}]' in asked
    assert "Could not be checked:" in asked and "no purchase order is paired with it" in asked
    charged = session.scalars(
        select(UsageEvent).where(UsageEvent.kind == "report", UsageEvent.unit == "calls")
    ).all()
    assert [(row.ref_type, row.ref_id) for row in charged] == [("workflow_run", queued["id"])]


def test_a_report_asked_for_later_is_written_and_written_again(client, session, services):
    project = create_project(client)
    invoices(session, project["id"])
    made(client, project["id"], TOTAL)
    queued = run(client, project["id"])
    path = f"/api/check-runs/{queued['id']}/report"
    early = client.post(path)
    assert early.status_code == 409 and "still being worked out" in early.json()["detail"]
    work(services)

    services.llm.agent_turns.append(FakeTurn(TextPart("One invoice is over its order.")))
    asked = client.post(path)
    assert asked.status_code == 200 and asked.json()["report_status"] == "queued"
    writing = client.post(path)
    assert writing.status_code == 409 and "being written already" in writing.json()["detail"]
    work(services)
    assert shown(client, queued["id"])["report"] == "One invoice is over its order."

    services.llm.agent_turns.append(FakeTurn(TextPart("Written again.")))
    assert client.post(path).json()["report"] == ""
    work(services)
    again = shown(client, queued["id"])
    assert (again["report"], again["usage"]["report"]["calls"]) == ("Written again.", 2)
    assert questions_counted(session) == 2


def test_a_report_is_held_to_what_holds_a_question(
    client,
    session,
    services,
    member,  # noqa: F811
    plan,  # noqa: F811
    monkeypatch,
):
    project = create_project(client)
    invoices(session, project["id"])
    made(client, project["id"], TOTAL)
    queued = run(client, project["id"])
    work(services)
    path = f"/api/check-runs/{queued['id']}/report"

    monkeypatch.setattr(get_settings(), "max_answers", 0)
    crowded = client.post(path)
    assert crowded.status_code == 429
    monkeypatch.setattr(get_settings(), "max_answers", 20)

    plan(questions_per_month=0)
    refused = client.post(path)
    assert (refused.status_code, refused.json()["limit"]) == (402, "questions_per_month")
    plan(questions_per_month=100)

    monkeypatch.setattr(get_settings(), "daily_spend_cap", Decimal("2"))
    spend(session, member.organization.id, "3")
    over = client.post(path)
    assert over.status_code == 503
    assert over.json()["detail"] == platform_refusal("3.00", "2.00")
    # Nothing was asked for, and no question counted.
    assert shown(client, queued["id"])["report_status"] is None
    assert questions_counted(session) == 0


def test_a_report_the_days_spending_catches_ends_with_its_sentence(
    client, session, services, monkeypatch, account
):
    project = create_project(client)
    invoices(session, project["id"])
    made(client, project["id"], TOTAL)
    queued = run(client, project["id"], report=True)
    monkeypatch.setattr(get_settings(), "daily_spend_cap", Decimal("2"))
    spend(session, account.organization.id, "3")
    work(services)
    done = shown(client, queued["id"])
    assert (done["status"], done["report_status"]) == ("done", "failed")
    assert done["report_error"] == platform_refusal("3.00", "2.00")
    assert done["usage"] == {}


def test_a_run_that_breaks_or_is_lost_ends_failed(client, session, services, monkeypatch):
    project = create_project(client)
    invoices(session, project["id"])
    made(client, project["id"], TOTAL)
    queued = run(client, project["id"])

    def broken(*args, **kwargs):
        raise RuntimeError("the pairing broke")

    monkeypatch.setattr("app.check_runs.findings_of", broken)
    with pytest.raises(RuntimeError):
        work(services)
    failed = shown(client, queued["id"])
    assert (failed["status"], failed["error"]) == ("failed", RUN_FAILED)
    # Its findings went with the transaction that broke.
    assert session.scalar(select(func.count()).select_from(WorkflowFinding)) == 0

    monkeypatch.undo()
    turn_on(monkeypatch)
    lost = run(client, project["id"])
    abandon_run(lost["id"], 3)
    assert shown(client, lost["id"])["error"] == (
        "It stopped unexpectedly 3 times and was not tried again. Try again in a moment."
    )
    late = client.post(f"/api/check-runs/{lost['id']}/report")
    assert late.status_code == 409 and late.json()["detail"].startswith("This run failed")


def test_a_run_is_audited_by_its_rules_names_never_a_value(client, session, services, account):
    project = create_project(client)
    invoices(session, project["id"])
    made(client, project["id"], TOTAL)
    services.llm.agent_turns += [FakeTurn(TextPart("Over.")), FakeTurn(TextPart("Again."))]
    queued = run(client, project["id"], report=True)
    work(services)
    assert client.post(f"/api/check-runs/{queued['id']}/report").status_code == 200
    assert client.delete(f"/api/check-runs/{queued['id']}").status_code == 204
    assert client.get(f"/api/check-runs/{queued['id']}").status_code == 404
    assert session.scalar(select(func.count()).select_from(WorkflowFinding)) == 0
    names = [TOTAL["title"]]
    logged = actions(session, account.organization.id)
    assert logged[-3:] == [
        ("cross_checks_run", {"names": names, "report": True}),
        ("check_report_requested", {"names": names}),
        ("check_run_deleted", {"names": names}),
    ]
    assert "1,255" not in str(logged)


def test_runs_are_kept_as_long_as_chats(client, session, services, member):  # noqa: F811
    project = create_project(client)
    invoices(session, project["id"])
    made(client, project["id"], TOTAL)
    old = run(client, project["id"])
    work(services)
    queued = run(client, project["id"])
    # The free plan keeps a chat 30 days; the worked-out run is older, the queued one too.
    session.execute(update(WorkflowRun).values(created_at=func.now() - dt.timedelta(days=31)))
    session.commit()
    with engine.begin() as connection:
        assert prune_runs(connection) == 1
    session.expire_all()
    assert session.get(WorkflowRun, old["id"]) is None
    assert session.get(WorkflowRun, queued["id"]) is not None


def test_runs_are_behind_the_extraction_templates_flag(client, session, monkeypatch):
    project = create_project(client)
    made(client, project["id"], TOTAL)
    monkeypatch.undo()
    assert client.post(f"/api/projects/{project['id']}/check-runs", json={}).status_code == 404
    assert client.get(f"/api/projects/{project['id']}/check-runs").status_code == 404
