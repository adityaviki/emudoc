"""A project's automations (app/automations.py, app/api/automations.py): rules that run its
cross-checks on their own once the documents they watch have arrived and gone quiet, and tell
whom they name what the run found about them, as a member's run is made and kept."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic_ai.messages import TextPart
from sqlalchemy import select, text, update

from app.api.automations import NOBODY_WITHOUT_REVIEW
from app.automations import (
    CHECKS_GONE,
    FOLDER_TRASHED,
    MAKER_GONE,
    MAX_AUTOMATIONS,
    MOST_HELD,
    NO_CHECKS,
    partner_kinds,
)
from app.config import get_settings
from app.jobs import RUN_AUTOMATION, RUN_WORKFLOW, claim
from app.mail import automation_review_email
from app.models import (
    AuditEvent,
    AutomationArrival,
    CrossCheck,
    Document,
    Job,
    Membership,
    Notification,
    Upload,
    WorkflowRun,
)
from app.notices import settle_upload
from app.reviews import NOT_A_REVIEWER
from app.usage import ANSWER, record
from app.worker import Worker
from tests.conftest import FakeTurn
from tests.test_api import create_project
from tests.test_chats import questions_counted
from tests.test_check_runs import TOTAL, invoices, made, shown
from tests.test_cross_checks import DUE, read
from tests.test_folders import make_folder
from tests.test_notices import told
from tests.test_templates import turn_on

APP = "https://emudoc.example.com"
AUTOMATION = {"title": "New invoices", "kind": "invoice"}


@pytest.fixture(autouse=True)
def templates_on(monkeypatch):
    turn_on(monkeypatch)


@pytest.fixture(autouse=True)
def at_once(monkeypatch):
    """No wait for the arrivals to go quiet, and the links where the notices lead."""
    monkeypatch.setattr(get_settings(), "automation_wait", 0)
    monkeypatch.setattr(get_settings(), "app_url", APP)


@pytest.fixture
def ann(make_account, act_as):
    """The API acts as Ann, the owner of Globex, a fresh organisation."""
    account = make_account("ann@globex.example")
    act_as(account)
    return account


def make(client, project_id: int, **body) -> dict:
    response = client.post(f"/api/projects/{project_id}/automations", json={**AUTOMATION, **body})
    assert response.status_code == 201, response.text
    return response.json()


def listed(client, project_id: int) -> list[dict]:
    response = client.get(f"/api/projects/{project_id}/automations")
    assert response.status_code == 200, response.text
    return response.json()["automations"]


def arrive(session, project_id: int, user_id: int | None, *document_ids: int) -> int:
    """The documents brought in by one upload of the member's, settled, as the worker settles it
    once the last of their index jobs ends."""
    upload = Upload(project_id=project_id, user_id=user_id)
    session.add(upload)
    session.flush()
    session.execute(
        update(Document).where(Document.id.in_(document_ids)).values(upload_id=upload.id)
    )
    session.commit()
    assert settle_upload(session, upload.id, APP)
    return upload.id


def next_job(services, kind: str) -> Job | None:
    """Run the next due job of the kind as the worker does, its row ended after."""
    worker = Worker(services, concurrency=1)
    job = claim(worker.id, [kind])
    if job is not None:
        worker._run(job)
    return job


def queued(session, kind: str = RUN_AUTOMATION) -> list[Job]:
    session.expire_all()
    return session.scalars(
        select(Job).where(Job.kind == kind, Job.status == "queued").order_by(Job.id)
    ).all()


def runs(session) -> list[WorkflowRun]:
    session.expire_all()
    return session.scalars(select(WorkflowRun).order_by(WorkflowRun.id)).all()


def test_an_automation_runs_the_checks_once_what_it_watches_has_arrived_and_tells_its_maker(
    client, session, services, mailer, ann
):
    project = create_project(client)
    made(client, project["id"], TOTAL)
    automation = make(client, project["id"])
    assert automation["problem"] is None
    assert automation["made_by"] == "Ann"
    documents = invoices(session, project["id"])

    # Two invoices arrive in one upload; the purchase order was there before.
    arrive(session, project["id"], ann.user.id, documents["INV-1"], documents["INV-2"])
    assert len(queued(session)) == 1
    next_job(services, RUN_AUTOMATION)

    # The run is made as a member's is, with no member behind it, naming what made it.
    (run,) = runs(session)
    assert run.user_id is None and run.automation_id == automation["id"]
    kept = shown(client, run.id)
    assert kept["run_by"] is None
    assert kept["trigger"] == {
        "automation_id": automation["id"],
        "automation": "New invoices",
        "documents": 2,
        "partners": 0,
        "names": ["INV-1.md", "INV-2.md"],
    }
    assert [rule["title"] for rule in kept["rules"]] == ["Total within its purchase order"]
    assert told(mailer) == []

    # Worked out, it tells Ann what it found about the two that arrived, once.
    next_job(services, RUN_WORKFLOW)
    kept = shown(client, run.id)
    assert kept["status"] == "done"
    assert kept["counts"] == {"differs": 1, "missing": 1, "holds": 0}
    (notice,) = told(mailer, "ann@globex.example")
    assert notice.subject == "The cross-checks found differences"
    assert (
        "The automation “New invoices” of Contracts in Globex ran the project's cross-checks after "
        "2 documents arrived. Of what they found about the documents that arrived, 1 value "
        "differs, 1 could not be checked and 0 hold." in notice.text
    )
    link = f"{APP}/projects/{project['id']}?run={run.id}"
    assert link in notice.text
    assert "1,255" not in notice.text
    (shown_notice,) = session.scalars(select(Notification)).all()
    assert shown_notice.kind == "checks_run"
    assert shown_notice.link == f"/projects/{project['id']}?run={run.id}"

    # The organisation's log has the run by the app, naming the automation and the rules.
    row = session.scalar(select(AuditEvent).where(AuditEvent.action == "cross_checks_run"))
    assert row.user_id is None
    assert row.detail == {
        "names": ["Total within its purchase order"],
        "report": False,
        "automation": "New invoices",
    }
    assert listed(client, project["id"])[0]["last_run_at"] is not None


def test_arrivals_that_keep_coming_make_one_run_once_they_go_quiet(
    client, session, services, mailer, ann, monkeypatch
):
    monkeypatch.setattr(get_settings(), "automation_wait", 120)
    project = create_project(client)
    made(client, project["id"], TOTAL)
    make(client, project["id"])
    documents = invoices(session, project["id"])
    arrive(session, project["id"], ann.user.id, documents["INV-1"])
    arrive(session, project["id"], ann.user.id, documents["INV-2"])

    # One job for both, waiting out the quiet after the last.
    (job,) = queued(session)
    assert job.run_at > datetime.now(UTC) + timedelta(seconds=100)

    # Woken early (a member's page sent more since it was queued), it queues the next for the
    # moment they have gone quiet and makes no run.
    session.execute(update(Job).where(Job.id == job.id).values(run_at=datetime.now(UTC)))
    session.commit()
    next_job(services, RUN_AUTOMATION)
    assert runs(session) == []
    (later,) = queued(session)
    assert later.id != job.id and later.run_at > datetime.now(UTC)

    # Once they have, one run takes both.
    session.execute(
        text("UPDATE automation_arrivals SET arrived_at = arrived_at - interval '3 minutes'")
    )
    session.execute(update(Job).where(Job.id == later.id).values(run_at=datetime.now(UTC)))
    session.commit()
    next_job(services, RUN_AUTOMATION)
    (run,) = runs(session)
    assert run.trigger["documents"] == [documents["INV-1"], documents["INV-2"]]
    assert queued(session) == []
    assert session.scalars(select(AutomationArrival)).all() == []

    # Run again, as after a lost lease, the job finds nothing left to run.
    next_job(services, RUN_AUTOMATION)
    assert len(runs(session)) == 1


def test_arrivals_that_never_go_quiet_run_within_a_quarter_of_an_hour_of_the_first(
    client, session, services, ann, monkeypatch
):
    monkeypatch.setattr(get_settings(), "automation_wait", 120)
    project = create_project(client)
    made(client, project["id"], TOTAL)
    make(client, project["id"])
    documents = invoices(session, project["id"])
    arrive(session, project["id"], ann.user.id, documents["INV-1"])
    session.execute(
        update(AutomationArrival).values(
            arrived_at=AutomationArrival.arrived_at - MOST_HELD - timedelta(minutes=1)
        )
    )
    session.commit()
    arrive(session, project["id"], ann.user.id, documents["INV-2"])
    session.execute(update(Job).values(run_at=datetime.now(UTC)))
    session.commit()
    next_job(services, RUN_AUTOMATION)
    (run,) = runs(session)
    assert sorted(run.trigger["documents"]) == sorted([documents["INV-1"], documents["INV-2"]])


def test_an_automation_watches_its_kind_in_its_folder_and_what_its_checks_compare_them_with(
    client, session, services, ann
):
    project = create_project(client)
    made(client, project["id"], TOTAL)
    inbox = make_folder(client, project["id"], "Inbox")
    urgent = make_folder(client, project["id"], "Urgent", inbox["id"])
    watching = make(client, project["id"], folder_id=inbox["id"])
    rechecking = make(client, project["id"], title="Again", folder_id=inbox["id"], recheck=True)
    documents = invoices(session, project["id"])
    elsewhere = read(session, project["id"], "INV-3.md", "invoice", po_number="PO-7701")
    contract = read(session, project["id"], "contract.md", "contract", party="Acme")
    session.execute(
        update(Document)
        .where(Document.id.in_([documents["INV-1"], documents["INV-2"]]))
        .values(folder_id=urgent["id"])
    )
    session.commit()

    arrive(
        session,
        project["id"],
        ann.user.id,
        documents["INV-1"],
        documents["INV-2"],
        documents["PO-7701"],
        elsewhere,
        contract,
    )
    kept = {
        (row.automation_id, row.document_id): row.recheck
        for row in session.scalars(select(AutomationArrival))
    }
    # The invoices in the folder's subfolder, for both; the purchase order the rule compares
    # them with, anywhere, for the one that checks again; neither the invoice outside the
    # folder, nor the contract no rule reads.
    assert kept == {
        (watching["id"], documents["INV-1"]): False,
        (watching["id"], documents["INV-2"]): False,
        (rechecking["id"], documents["INV-1"]): False,
        (rechecking["id"], documents["INV-2"]): False,
        (rechecking["id"], documents["PO-7701"]): True,
    }


def test_what_runs_an_automation_again_is_the_kinds_its_checks_compare_with_its_own():
    rules = [
        CrossCheck(left_kind="invoice", right_kind="purchase order"),
        CrossCheck(left_kind="delivery note", right_kind="invoice"),
        CrossCheck(left_kind="invoice", right_kind="invoice"),
        CrossCheck(left_kind="contract", right_kind="price list"),
    ]
    assert partner_kinds("invoice", rules) == {"purchase order", "delivery note"}
    assert partner_kinds(None, rules[:2]) == {"invoice", "purchase order", "delivery note"}
    assert partner_kinds("receipt", rules) == set()


def test_a_purchase_order_arriving_later_checks_the_invoices_again_and_tells_what_it_found(
    client, session, services, mailer, ann
):
    project = create_project(client)
    made(client, project["id"], TOTAL)
    make(client, project["id"], recheck=True)
    invoices(session, project["id"])
    order = read(
        session,
        project["id"],
        "PO-7702.md",
        "purchase order",
        po_number="PO-7702",
        total_amount=("100.00 EUR", 100.0, "EUR"),
    )
    arrive(session, project["id"], ann.user.id, order)
    next_job(services, RUN_AUTOMATION)
    next_job(services, RUN_WORKFLOW)
    (run,) = runs(session)
    assert run.trigger["documents"] == [] and run.trigger["partners"] == [order]
    # What it found about INV-2, whose order it is, not about INV-1.
    (notice,) = told(mailer)
    assert notice.subject == "The cross-checks found nothing amiss"
    assert (
        "after PO-7702.md, which its cross-checks compare with the documents it watches, "
        "arrived. Of what they found about it, 0 values differ, 0 could not be checked and 1 "
        "holds." in notice.text
    )


def test_whom_an_automation_tells(
    client,
    session,
    services,
    mailer,
    ann,
    make_account,
):
    make_account("grace@globex.example", ann.organization, role="viewer")
    project = create_project(client)
    made(client, project["id"], TOTAL)
    automation = make(client, project["id"], audience="project")
    documents = invoices(session, project["id"])

    def round_of(*document_ids: int) -> list[str]:
        """To whom a run of what arrived was told."""
        seen = len(told(mailer))
        arrive(session, project["id"], ann.user.id, *document_ids)
        next_job(services, RUN_AUTOMATION)
        next_job(services, RUN_WORKFLOW)
        return sorted(message.to for message in told(mailer)[seen:])

    # Everyone who sees the project, a viewer among them.
    assert round_of(documents["INV-1"]) == ["ann@globex.example", "grace@globex.example"]

    # A private project: those chosen for it, and its owners and admins.
    response = client.put(
        f"/api/projects/{project['id']}/access", json={"visibility": "private", "user_ids": []}
    )
    assert response.status_code == 200, response.text
    assert round_of(documents["INV-2"]) == ["ann@globex.example"]

    # Nobody.
    response = client.put(
        f"/api/automations/{automation['id']}", json={**AUTOMATION, "audience": "nobody"}
    )
    assert response.status_code == 200, response.text
    again = read(session, project["id"], "INV-4.md", "invoice", po_number="PO-7701")
    assert round_of(again) == []


def test_a_maker_who_left_is_told_nothing_and_the_automation_says_so(
    client,
    session,
    services,
    mailer,
    ann,
    make_account,
    act_as,
):
    bob = make_account("bob@globex.example", ann.organization, role="member")
    act_as(bob)
    project = create_project(client)
    made(client, project["id"], TOTAL)
    make(client, project["id"])
    act_as(ann)
    session.execute(Membership.__table__.delete().where(Membership.user_id == bob.user.id))
    session.commit()
    assert listed(client, project["id"])[0]["problem"] == MAKER_GONE
    documents = invoices(session, project["id"])
    arrive(session, project["id"], ann.user.id, documents["INV-1"])
    next_job(services, RUN_AUTOMATION)
    next_job(services, RUN_WORKFLOW)
    assert len(runs(session)) == 1
    assert told(mailer) == []


def test_nothing_is_told_of_a_run_that_found_nothing_about_what_arrived(
    client, session, services, mailer, ann
):
    project = create_project(client)
    made(client, project["id"], TOTAL)
    make(client, project["id"], kind=None)
    invoices(session, project["id"])
    contract = read(session, project["id"], "contract.md", "contract", party="Acme")
    arrive(session, project["id"], ann.user.id, contract)
    next_job(services, RUN_AUTOMATION)
    next_job(services, RUN_WORKFLOW)
    (run,) = runs(session)
    assert run.told_at is not None
    assert told(mailer) == []


def test_an_automation_s_report_counts_a_question_and_a_refusal_leaves_the_run_without_it(
    client,
    session,
    services,
    mailer,
    ann,
    monkeypatch,
):
    project = create_project(client)
    made(client, project["id"], TOTAL)
    make(client, project["id"], report=True)
    documents = invoices(session, project["id"])
    arrive(session, project["id"], ann.user.id, documents["INV-1"])
    next_job(services, RUN_AUTOMATION)
    (run,) = runs(session)
    assert run.report_status == "queued"
    assert questions_counted(session) == 1
    services.llm.agent_turns.append(FakeTurn(TextPart("INV-1 is over its order.")))
    next_job(services, RUN_WORKFLOW)
    assert shown(client, run.id)["report_status"] == "done"
    (notice,) = told(mailer)
    assert "The model's discrepancy report is written." in notice.text

    # With the day's spending reached, the run goes ahead and says why it has no report.
    monkeypatch.setattr(get_settings(), "daily_spend_cap", Decimal("0.000001"))
    monkeypatch.setattr(get_settings(), "model_prices", f"{get_settings().agent_model}=1/1")
    record(
        session,
        org_id=ann.organization.id,
        project_id=None,
        user_id=None,
        kind=ANSWER,
        measures={"input_tokens": 1_000},
        model=get_settings().agent_model,
    )
    session.commit()
    arrive(session, project["id"], ann.user.id, documents["INV-2"])
    next_job(services, RUN_AUTOMATION)
    _, refused = runs(session)
    assert refused.report_status == "failed"
    assert refused.report_error.startswith("Emudoc has reached its spending limit")
    assert questions_counted(session) == 1
    next_job(services, RUN_WORKFLOW)
    _, notice = told(mailer)
    assert "The discrepancy report could not be written: Emudoc has reached" in notice.text
    row = session.scalars(
        select(AuditEvent).where(AuditEvent.action == "cross_checks_run").order_by(AuditEvent.id)
    ).all()[-1]
    assert row.detail["report"] is False


def test_an_automation_that_is_off_or_whose_project_is_in_the_trash_runs_nothing(
    client, session, services, ann
):
    project = create_project(client)
    made(client, project["id"], TOTAL)
    automation = make(client, project["id"], enabled=False)
    documents = invoices(session, project["id"])
    arrive(session, project["id"], ann.user.id, documents["INV-1"])
    assert queued(session) == []

    # Turned on, then off again while its arrivals wait: they go with the job.
    client.put(f"/api/automations/{automation['id']}", json=AUTOMATION)
    arrive(session, project["id"], ann.user.id, documents["INV-2"])
    client.put(f"/api/automations/{automation['id']}", json={**AUTOMATION, "enabled": False})
    next_job(services, RUN_AUTOMATION)
    assert runs(session) == []
    assert session.scalars(select(AutomationArrival)).all() == []

    # On, with its project put in the trash before it runs.
    client.put(f"/api/automations/{automation['id']}", json=AUTOMATION)
    arrive(session, project["id"], ann.user.id, documents["PO-7701"], documents["INV-1"])
    assert client.delete(f"/api/projects/{project['id']}").status_code == 204
    next_job(services, RUN_AUTOMATION)
    assert runs(session) == []


def test_an_automation_with_nothing_to_run_or_whose_flag_is_off_runs_nothing(
    client, session, services, ann, monkeypatch
):
    project = create_project(client)
    check = made(client, project["id"], TOTAL)[0]
    automation = make(client, project["id"], check_ids=[check])
    documents = invoices(session, project["id"])
    assert client.delete(f"/api/cross-checks/{check}").status_code == 204
    assert listed(client, project["id"])[0]["problem"] == CHECKS_GONE
    arrive(session, project["id"], ann.user.id, documents["INV-1"])
    next_job(services, RUN_AUTOMATION)
    assert runs(session) == []

    made(client, project["id"], TOTAL)
    client.put(f"/api/automations/{automation['id']}", json=AUTOMATION)
    arrive(session, project["id"], ann.user.id, documents["INV-2"])
    monkeypatch.undo()
    monkeypatch.setattr(get_settings(), "automation_wait", 0)
    next_job(services, RUN_AUTOMATION)
    assert runs(session) == []


def test_a_document_deleted_before_the_run_is_not_named(client, session, services, ann):
    project = create_project(client)
    made(client, project["id"], TOTAL)
    make(client, project["id"])
    documents = invoices(session, project["id"])
    arrive(session, project["id"], ann.user.id, documents["INV-1"], documents["INV-2"])
    assert client.delete(f"/api/documents/{documents['INV-2']}").status_code == 204
    next_job(services, RUN_AUTOMATION)
    (run,) = runs(session)
    assert run.trigger["documents"] == [documents["INV-1"]]
    assert run.trigger["names"] == ["INV-1.md"]


def test_a_run_outlives_its_automation(client, session, services, ann):
    project = create_project(client)
    made(client, project["id"], TOTAL)
    automation = make(client, project["id"])
    documents = invoices(session, project["id"])
    arrive(session, project["id"], ann.user.id, documents["INV-1"])
    next_job(services, RUN_AUTOMATION)
    assert client.delete(f"/api/automations/{automation['id']}").status_code == 204
    (run,) = runs(session)
    response = client.get(f"/api/projects/{project['id']}/check-runs")
    (summary,) = response.json()["runs"]
    assert summary["trigger"]["automation_id"] is None
    assert summary["trigger"]["automation"] == "New invoices"
    assert run.automation_id is None


def test_an_automation_is_written_whole_and_refused_what_the_project_does_not_hold(
    client,
    session,
    ann,
):
    project = create_project(client)
    other = create_project(client, "Other")
    check, due = made(client, project["id"], TOTAL, DUE)
    foreign = made(client, other["id"], TOTAL)[0]
    inbox = make_folder(client, project["id"], "Inbox")
    elsewhere = make_folder(client, other["id"], "Inbox")
    path = f"/api/projects/{project['id']}/automations"

    for body, status, said in (
        ({"kind": "tax return"}, 400, "no template for that kind"),
        ({"folder_id": elsewhere["id"]}, 404, "Folder not found"),
        ({"check_ids": [foreign]}, 404, "Cross-check not found"),
        ({"check_ids": []}, 422, ""),
        ({"audience": "everyone"}, 422, ""),
        ({"title": " "}, 422, ""),
    ):
        response = client.post(path, json={**AUTOMATION, **body})
        assert response.status_code == status, body
        assert said in response.text

    automation = make(
        client,
        project["id"],
        kind="Invoice",
        folder_id=inbox["id"],
        check_ids=[due, check],
        report=True,
        audience="project",
        recheck=True,
    )
    assert automation["kind"] == "invoice"
    assert automation["folder"] == "Inbox"
    assert automation["checks"] == [
        "Due within 30 days of issue",
        "Total within its purchase order",
    ]
    assert sorted(automation["check_ids"]) == sorted([check, due])

    # Written whole: what is left out goes back to its default.
    response = client.put(f"/api/automations/{automation['id']}", json={"title": "Every invoice"})
    assert response.status_code == 200, response.text
    changed = response.json()
    assert changed["kind"] is None and changed["folder_id"] is None
    assert changed["check_ids"] is None and changed["checks"] is None
    assert not changed["report"] and changed["audience"] == "maker"

    rows = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.action.in_(("automation_created", "automation_changed")))
        .order_by(AuditEvent.id)
    ).all()
    assert rows[0].detail == {
        "name": "New invoices",
        "kind": "invoice",
        "folder": "Inbox",
        "checks": ["Due within 30 days of issue", "Total within its purchase order"],
        "report": True,
        "audience": "project",
        "recheck": True,
        "enabled": True,
        "review": None,
        "assignee": None,
    }
    assert rows[1].detail["changed"] == [
        "title",
        "watches",
        "checks",
        "report",
        "audience",
        "recheck",
    ]

    # Nothing changed, nothing logged.
    client.put(f"/api/automations/{automation['id']}", json={"title": "Every invoice"})
    assert (
        session.scalar(
            select(AuditEvent.id).where(AuditEvent.action == "automation_changed").offset(1)
        )
        is None
    )

    # Deleted for good, which the log names.
    assert client.delete(f"/api/automations/{automation['id']}").status_code == 204
    assert listed(client, project["id"]) == []
    row = session.scalar(select(AuditEvent).where(AuditEvent.action == "automation_deleted"))
    assert row.detail["name"] == "Every invoice"


def test_what_an_automation_would_run_nothing_for(client, session, ann):
    project = create_project(client)
    automation = make(client, project["id"], kind=None)
    assert automation["problem"] == NO_CHECKS
    made(client, project["id"], TOTAL)
    inbox = make_folder(client, project["id"], "Inbox")
    client.put(
        f"/api/automations/{automation['id']}", json={**AUTOMATION, "folder_id": inbox["id"]}
    )
    assert listed(client, project["id"])[0]["problem"] is None
    assert client.delete(f"/api/folders/{inbox['id']}").status_code == 204
    (shown_automation,) = listed(client, project["id"])
    assert shown_automation["problem"] == FOLDER_TRASHED
    assert shown_automation["folder"] == "Inbox"


def test_a_project_has_at_most_so_many_automations(client, session, ann):
    project = create_project(client)
    for number in range(MAX_AUTOMATIONS):
        make(client, project["id"], title=f"Automation {number}")
    response = client.post(f"/api/projects/{project['id']}/automations", json=AUTOMATION)
    assert response.status_code == 409
    assert f"at most {MAX_AUTOMATIONS} automations" in response.text


def test_automations_are_behind_the_templates_flag(client, session, ann, monkeypatch):
    project = create_project(client)
    monkeypatch.undo()
    response = client.get(f"/api/projects/{project['id']}/automations")
    assert response.status_code == 404


def test_the_notice_says_what_arrived_in_so_many_words():
    from app.mail import checks_run_email

    def said(landed, partners, names, report=None, error=None, **counts):
        return checks_run_email(
            "Ann",
            "Globex",
            "Contracts",
            "New invoices",
            landed,
            partners,
            names,
            {"differs": 0, "missing": 0, "holds": 0, **counts},
            report,
            error,
            "https://x",
        )

    one = said(1, 0, ["INV-1.md"], differs=2)
    assert one.subject == "The cross-checks found differences"
    assert "after INV-1.md arrived. Of what they found about it, 2 values differ" in one.brief
    some = said(3, 2, ["a.md", "b.md", "c.md"], missing=1)
    assert some.subject == "The cross-checks could not check everything"
    assert "after 3 documents arrived, and 2 that its cross-checks compare them with." in some.brief
    assert said(0, 4, ["a.md"], holds=1).brief.count("4 documents that its cross-checks") == 1
    failed = said(2, 0, ["a.md", "b.md"], "failed", "The day's spending is reached.", holds=2)
    assert failed.subject == "The cross-checks found nothing amiss"
    assert failed.brief.endswith(
        "The discrepancy report could not be written: The day's spending is reached."
    )


@pytest.fixture
def bob(session, make_account, ann):
    """Bob, a member of Ann's organisation."""
    return make_account("bob@globex.example", ann.organization, role="member")


def standing(session, document_id: int) -> tuple:
    session.expire_all()
    document = session.get(Document, document_id)
    return (document.review, document.assignee, document.review_changer)


def test_an_automation_puts_what_its_checks_found_differing_in_review_for_its_assignee(
    client, session, services, mailer, ann, bob
):
    project = create_project(client)
    made(client, project["id"], TOTAL)
    automation = make(client, project["id"], review="differs", assignee_id=bob.user.id)
    assert (automation["review"], automation["assignee"]) == ("differs", "Bob")
    documents = invoices(session, project["id"])
    arrive(session, project["id"], ann.user.id, documents["INV-1"], documents["INV-2"])
    next_job(services, RUN_AUTOMATION)
    # Nothing moves until the run is worked out.
    assert standing(session, documents["INV-1"]) == (None, None, None)
    next_job(services, RUN_WORKFLOW)

    # INV-1 is 5 EUR over its order; INV-2, with no order, could not be checked, and stays out.
    assert standing(session, documents["INV-1"]) == ("to_review", "Bob", None)
    assert standing(session, documents["INV-2"]) == (None, None, None)
    assert standing(session, documents["PO-7701"]) == (None, None, None)
    (notice,) = told(mailer, "bob@globex.example")
    assert notice.subject == "A document was assigned to you"
    assert (
        "The automation “New invoices” of Contracts in Globex put INV-1.md in review for you."
        in notice.text
    )
    page = f"/projects/{project['id']}/documents/{documents['INV-1']}"
    assert f"{APP}{page}" in notice.text
    (shown_notice,) = session.scalars(
        select(Notification).where(Notification.kind == "review_assigned")
    ).all()
    assert (shown_notice.user_id, shown_notice.link) == (bob.user.id, page)

    # By the app, naming the automation, never a value.
    rows = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.action.in_(("document_review_changed", "document_assigned")))
        .order_by(AuditEvent.id)
    ).all()
    assert [(row.action, row.user_id, row.detail) for row in rows] == [
        (
            "document_review_changed",
            None,
            {"filename": "INV-1.md", "automation": "New invoices", "from": None, "to": "to_review"},
        ),
        (
            "document_assigned",
            None,
            {"filename": "INV-1.md", "automation": "New invoices", "from": None, "to": bob.user.id},
        ),
    ]


def test_an_automation_puts_everything_that_arrived_in_review_but_what_a_member_moved(
    client, session, services, mailer, ann
):
    project = create_project(client)
    made(client, project["id"], TOTAL)
    make(client, project["id"], review="arrived")
    documents = invoices(session, project["id"])
    response = client.put(f"/api/documents/{documents['INV-1']}/review", json={"state": "approved"})
    assert response.status_code == 200, response.text
    arrive(session, project["id"], ann.user.id, documents["INV-1"], documents["INV-2"])
    next_job(services, RUN_AUTOMATION)
    next_job(services, RUN_WORKFLOW)

    # A member's word stands; the other is in review, assigned to nobody, and nobody is told so.
    assert standing(session, documents["INV-1"]) == ("approved", None, "Ann")
    assert standing(session, documents["INV-2"]) == ("to_review", None, None)
    assert (
        session.scalars(select(Notification).where(Notification.kind == "review_assigned")).all()
        == []
    )


def test_an_automation_assigns_a_member_who_may_review_while_they_may(
    client, session, services, ann, bob, make_account
):
    project = create_project(client)
    made(client, project["id"], TOTAL)
    refused = client.post(
        f"/api/projects/{project['id']}/automations",
        json={**AUTOMATION, "assignee_id": bob.user.id},
    )
    assert (refused.status_code, refused.json()["detail"]) == (400, NOBODY_WITHOUT_REVIEW)
    viewer = make_account("vic@globex.example", ann.organization, role="viewer")
    refused = client.post(
        f"/api/projects/{project['id']}/automations",
        json={**AUTOMATION, "review": "arrived", "assignee_id": viewer.user.id},
    )
    assert (refused.status_code, refused.json()["detail"]) == (400, NOT_A_REVIEWER)
    automation = make(client, project["id"], review="arrived", assignee_id=bob.user.id)

    # Made a viewer since, Bob is kept when it is written again, and assigned nothing it puts in
    # review.
    session.execute(
        update(Membership)
        .where(Membership.user_id == bob.user.id, Membership.org_id == ann.organization.id)
        .values(role="viewer")
    )
    session.commit()
    body = {**AUTOMATION, "review": "arrived", "assignee_id": bob.user.id, "enabled": False}
    response = client.put(f"/api/automations/{automation['id']}", json=body)
    assert response.status_code == 200, response.text
    changed = session.scalar(
        select(AuditEvent.detail).where(AuditEvent.action == "automation_changed")
    )
    assert changed["changed"] == ["enabled"]
    client.put(f"/api/automations/{automation['id']}", json={**body, "enabled": True})
    documents = invoices(session, project["id"])
    arrive(session, project["id"], ann.user.id, documents["INV-2"])
    next_job(services, RUN_AUTOMATION)
    next_job(services, RUN_WORKFLOW)
    assert standing(session, documents["INV-2"]) == ("to_review", None, None)

    # Removed from the organisation, he is no longer its assignee.
    assert client.delete(f"/api/organization/members/{bob.user.id}").status_code == 204
    (shown,) = listed(client, project["id"])
    assert (shown["review"], shown["assignee_id"], shown["assignee"]) == ("arrived", None, None)


def test_the_notice_of_what_an_automation_put_in_review_names_the_first_few():
    def said(*names: str) -> str:
        content = automation_review_email(
            "Bob", "New invoices", list(names), "Contracts", "Globex", f"{APP}/x"
        )
        return content.brief

    by = "The automation “New invoices” of Contracts in Globex"
    assert said("a.md", "b.md") == f"{by} put 2 documents in review for you: a.md and b.md."
    assert said("a.md", "b.md", "c.md") == (
        f"{by} put 3 documents in review for you: a.md, b.md and c.md."
    )
    assert said("a.md", "b.md", "c.md", "d.md", "e.md") == (
        f"{by} put 5 documents in review for you: a.md, b.md, c.md and 2 more."
    )
    several = automation_review_email("Bob", "N", ["a", "b"], "P", "O", f"{APP}/x")
    assert several.subject == "Documents were assigned to you"
