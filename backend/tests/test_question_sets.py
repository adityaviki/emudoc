"""A project's question sets (app/question_sets.py, app/api/question_sets.py) and the reports of
their answers (app/question_reports.py): questions asked on a schedule in the maker's zone, as
documents arrive, or by a member there and then, each answered by the agent and counted as one
of the month's questions, the report kept and told to whom the set names."""

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic_ai.messages import TextPart
from sqlalchemy import delete, select, update

from app.api.question_sets import NO_WEEKDAY, NOTHING_RUNS_IT, STILL_RUNNING, STILL_WRITTEN
from app.db import engine
from app.entitlements import check_question
from app.flags import FLAGS, QUESTION_SETS
from app.jobs import RUN_QUESTION_SET, WRITE_QUESTION_REPORT
from app.models import (
    AuditEvent,
    Document,
    Job,
    Notification,
    Organization,
    QuestionReport,
    QuestionSet,
    UsageEvent,
)
from app.question_reports import (
    ASKED_ALONE,
    PROJECT_GONE,
    QUESTION_SET_PROMPT_VERSION,
    abandon_report,
    write_report,
)
from app.question_sets import queue_due_sets
from app.retention import prune_reports
from tests.conftest import FakeTurn
from tests.test_api import create_project
from tests.test_automations import APP, arrive, at_once, next_job  # noqa: F401 (a fixture)
from tests.test_chats import questions_counted
from tests.test_entitlements import plan  # noqa: F401 (a fixture)
from tests.test_notices import told
from tests.test_templates import turn_on

BERLIN = ZoneInfo("Europe/Berlin")
SET = {
    "title": "Every morning",
    "questions": ["What is due this week?", "Who signed the agreement?"],
    "schedule": "daily",
    "hour": 8,
}


@pytest.fixture(autouse=True)
def question_sets_on(monkeypatch, plan):  # noqa: F811
    """The flag on, and the free plan given question sets, as Pro has them."""
    turn_on(monkeypatch, QUESTION_SETS)
    plan(question_sets=True)


@pytest.fixture
def ann(make_account, act_as, session):
    """The API acts as Ann, the owner of Globex, who works in Berlin's time."""
    account = make_account("ann@globex.example")
    account.user.time_zone = "Europe/Berlin"
    session.commit()
    act_as(account)
    return account


def make(client, project_id: int, **body) -> dict:
    response = client.post(f"/api/projects/{project_id}/question-sets", json={**SET, **body})
    assert response.status_code == 201, response.text
    return response.json()


def listed(client, project_id: int) -> list[dict]:
    response = client.get(f"/api/projects/{project_id}/question-sets")
    assert response.status_code == 200, response.text
    return response.json()["question_sets"]


def shown(client, report_id: int) -> dict:
    response = client.get(f"/api/reports/{report_id}")
    assert response.status_code == 200, response.text
    return response.json()


def waking(session, set_id: int) -> list[Job]:
    """The set's jobs still queued."""
    session.expire_all()
    return [
        job
        for job in session.scalars(
            select(Job).where(Job.kind == RUN_QUESTION_SET, Job.status == "queued")
        )
        if job.payload["set_id"] == set_id
    ]


def due(session, set_id: int) -> None:
    """The set's schedule due a minute ago, and its job with it."""
    past = datetime.now(UTC) - timedelta(minutes=1)
    session.execute(update(QuestionSet).where(QuestionSet.id == set_id).values(next_run_at=past))
    for job in waking(session, set_id):
        job.run_at = past
    session.commit()


def ran(client, set_id: int) -> dict:
    """The set run by the member there and then: its report, queued."""
    response = client.post(f"/api/question-sets/{set_id}/run")
    assert response.status_code == 201, response.text
    return response.json()


def ready(session, project_id: int, *names: str) -> list[int]:
    """Documents of the project read and ready, by their names."""
    ids = []
    for name in names:
        document = Document(
            project_id=project_id,
            filename=name,
            mime_type="text/plain",
            size_bytes=1,
            sha256=uuid.uuid4().hex,
            storage_key="",
            status="ready",
            stages={},
        )
        session.add(document)
        session.flush()
        ids.append(document.id)
    session.commit()
    return ids


def audited(session, action: str) -> list[AuditEvent]:
    session.expire_all()
    return list(
        session.scalars(
            select(AuditEvent).where(AuditEvent.action == action).order_by(AuditEvent.id)
        )
    )


def answers(services, *replies: str) -> None:
    """The agent's next answers, one a question."""
    services.llm.agent_turns.extend(FakeTurn(TextPart(reply)) for reply in replies)


def reports(session) -> list[QuestionReport]:
    session.expire_all()
    return list(session.scalars(select(QuestionReport).order_by(QuestionReport.id)))


def test_a_schedule_names_its_hour_on_the_days_it_says_in_the_makers_zone():
    from app.question_sets import next_run

    # 07:30 in Berlin (CEST, 05:30 UTC): today at 08:00 there, 06:00 UTC.
    morning = datetime(2026, 10, 20, 5, 30, tzinfo=UTC)
    assert next_run("daily", None, 8, BERLIN, morning) == datetime(2026, 10, 20, 6, tzinfo=UTC)
    # Past the hour, tomorrow; at the hour itself, tomorrow too, since that run is this one.
    assert next_run("daily", None, 7, BERLIN, morning) == datetime(2026, 10, 21, 5, tzinfo=UTC)
    exactly = datetime(2026, 10, 20, 6, tzinfo=UTC)
    assert next_run("daily", None, 8, BERLIN, exactly) == datetime(2026, 10, 21, 6, tzinfo=UTC)
    # Weekly: the next Monday (20 October 2026 is a Tuesday), at 08:00 there.
    assert next_run("weekly", 0, 8, BERLIN, morning) == datetime(2026, 10, 26, 7, tzinfo=UTC)
    assert next_run("weekly", 1, 8, BERLIN, morning) == datetime(2026, 10, 20, 6, tzinfo=UTC)
    # The clocks went back on 25 October: the run stays at 08:00 there, an hour later in UTC.
    saturday = datetime(2026, 10, 24, 7, tzinfo=UTC)
    assert next_run("daily", None, 8, BERLIN, saturday) == datetime(2026, 10, 25, 7, tzinfo=UTC)
    # No schedule, no moment; UTC for a maker who chose no zone.
    assert next_run(None, None, 8, BERLIN, morning) is None
    assert next_run("daily", None, 8, UTC, morning) == datetime(2026, 10, 20, 8, tzinfo=UTC)


def test_a_member_makes_a_set_whose_schedule_queues_its_first_run(client, session, ann):
    project = create_project(client)
    made = make(client, project["id"])
    assert made["made_by"] == "Ann" and made["time_zone"] == "Europe/Berlin"
    assert made["problem"] is None and made["running"] is False
    # Its questions are its maker's words, which their account's export holds.
    (exported,) = client.get("/api/auth/account/export").json()["question_sets"]
    assert (exported["project"], exported["questions"]) == ("Contracts", SET["questions"])
    moment = datetime.fromisoformat(made["next_run_at"])
    assert moment.astimezone(BERLIN).hour == 8 and moment > datetime.now(UTC)
    (job,) = waking(session, made["id"])
    assert job.run_at == moment
    assert listed(client, project["id"]) == [made]


def test_the_schedule_makes_a_report_answers_each_question_and_tells_the_maker(
    client, session, services, mailer, ann
):
    project = create_project(client)
    made = make(client, project["id"])
    due(session, made["id"])
    assert next_job(services, RUN_QUESTION_SET) is not None
    (report,) = reports(session)
    assert (report.trigger, report.user_id, report.status) == ("schedule", None, "queued")
    # The schedule's next run is queued as this one is made, at 08:00 in Berlin.
    (following,) = waking(session, made["id"])
    assert following.run_at == session.get(QuestionSet, made["id"]).next_run_at
    assert following.run_at > datetime.now(UTC)
    assert following.run_at.astimezone(BERLIN).hour == 8

    answers(services, "The invoice is due on Friday.", "Ada signed it.")
    assert next_job(services, WRITE_QUESTION_REPORT) is not None
    kept = shown(client, report.id)
    assert kept["status"] == "done" and (kept["answered"], kept["failed"]) == (2, 0)
    assert [entry["content"] for entry in kept["answers"]] == [
        "The invoice is due on Friday.",
        "Ada signed it.",
    ]
    # Each question counts as one of the month's, and the model calls are the report's.
    assert questions_counted(session) == 2
    assert kept["usage"]["answer"]["calls"] == 2
    # Answers of a set's own prompts: an answer's, with the task of a question asked alone.
    versions = session.scalars(
        select(UsageEvent.prompt_version).where(
            UsageEvent.ref_type == "question_report", UsageEvent.ref_id == report.id
        )
    ).all()
    assert set(versions) == {QUESTION_SET_PROMPT_VERSION}
    # Each was asked of the project with the task of a question asked alone.
    assert all(ASKED_ALONE in prompt for prompt in services.llm.agent_prompts)

    (notice,) = told(mailer, "ann@globex.example")
    assert "Every morning" in notice.text and "2 of its 2 questions were answered" in notice.text
    assert "due on Friday" not in notice.text
    assert f"{APP}/projects/{project['id']}?report={report.id}" in notice.text
    (row,) = session.scalars(select(Notification).where(Notification.kind == "report_ready"))
    assert row.link == f"/projects/{project['id']}?report={report.id}"
    (audited,) = session.scalars(select(AuditEvent).where(AuditEvent.action == "question_set_run"))
    assert audited.user_id is None
    assert audited.detail == {
        "name": "Every morning",
        "questions": 2,
        "trigger": "schedule",
        "schedule": "daily at 08:00",
        "arrived": 0,
    }


def test_documents_arriving_run_a_set_once_they_go_quiet_and_tell_everyone_who_sees_the_project(
    client, session, services, mailer, ann, make_account
):
    project = create_project(client)
    made = make(client, project["id"], schedule=None, on_arrivals=True, audience="project")
    assert made["next_run_at"] is None and waking(session, made["id"]) == []
    make_account("bob@globex.example", ann.organization, "viewer")
    first, second = ready(session, project["id"], "INV-1.pdf", "INV-2.pdf")

    # Two uploads, one waking of the set, one report of what both brought.
    arrive(session, project["id"], ann.user.id, first)
    arrive(session, project["id"], ann.user.id, second)
    assert len(waking(session, made["id"])) == 1
    assert next_job(services, RUN_QUESTION_SET) is not None
    (report,) = reports(session)
    assert (report.trigger, report.user_id) == ("arrivals", None)
    assert report.arrived == {"documents": [first, second], "names": ["INV-1.pdf", "INV-2.pdf"]}
    assert waking(session, made["id"]) == []

    answers(services, "INV-1 is due on Friday.", "Nobody signed INV-2.")
    assert next_job(services, WRITE_QUESTION_REPORT) is not None
    # The model is told which arrived, by their ids alone.
    told_ids = f"those with the ids {first}, {second}."
    assert all(told_ids in prompt for prompt in services.llm.agent_prompts)
    summary = client.get(f"/api/projects/{project['id']}/reports").json()["reports"][0]
    assert (summary["trigger"], summary["arrived"], summary["names"]) == (
        "arrivals",
        2,
        ["INV-1.pdf", "INV-2.pdf"],
    )
    for address in ("ann@globex.example", "bob@globex.example"):
        (notice,) = told(mailer, address)
        assert "was asked after 2 documents arrived: 2 of its 2 questions" in notice.text
    (row,) = audited(session, "question_set_run")
    assert (row.user_id, row.detail["trigger"], row.detail["arrived"]) == (None, "arrivals", 2)


def test_a_member_runs_a_set_there_and_then_and_alone_is_told(
    client,
    session,
    services,
    mailer,
    ann,
    make_account,
    plan,  # noqa: F811
):
    plan(question_sets=True, members=2)
    project = create_project(client)
    made = make(client, project["id"], audience="project")
    make_account("bob@globex.example", ann.organization, "viewer")
    started = ran(client, made["id"])
    assert (started["trigger"], started["run_by"], started["status"]) == ("member", "Ann", "queued")
    assert [entry["status"] for entry in started["answers"]] == ["queued", "queued"]
    assert [entry["question"] for entry in started["answers"]] == SET["questions"]
    assert listed(client, project["id"])[0]["running"] is True
    again = client.post(f"/api/question-sets/{made['id']}/run")
    assert (again.status_code, again.json()["detail"]) == (409, STILL_RUNNING)

    answers(services, "Nothing is due.", "Ada signed it.")
    assert next_job(services, WRITE_QUESTION_REPORT) is not None
    (notice,) = told(mailer)
    assert notice.to == "ann@globex.example"
    assert "was asked when you ran it: 2 of its 2 questions were answered." in notice.text
    page = client.get(f"/api/projects/{project['id']}/reports").json()
    assert [report["id"] for report in page["reports"]] == [started["id"]]
    assert page["next_before"] is None
    (shown_set,) = listed(client, project["id"])
    assert shown_set["running"] is False and shown_set["last_report_at"] is not None
    (row,) = audited(session, "question_set_run")
    assert (row.user_id, row.detail["trigger"]) == (ann.user.id, "member")


def test_the_plan_and_the_flag_hold_question_sets(client, session, ann, plan, monkeypatch):  # noqa: F811
    project = create_project(client)
    made = make(client, project["id"])
    plan(question_sets=False)
    for method, path, body in (
        ("POST", f"/api/projects/{project['id']}/question-sets", SET),
        ("PUT", f"/api/question-sets/{made['id']}", SET),
        ("POST", f"/api/question-sets/{made['id']}/run", None),
    ):
        refused = client.request(method, path, json=body)
        assert (refused.status_code, refused.json()["limit"]) == (402, "question_sets"), path
    # What the set would do is said where it is listed, and its schedule makes no report.
    (shown_set,) = listed(client, project["id"])
    assert shown_set["problem"].startswith("The Free plan does not include question sets.")
    due(session, made["id"])
    assert next_job(None, RUN_QUESTION_SET) is not None
    assert reports(session) == []
    # Off for the organisation, none of it is there.
    monkeypatch.setitem(FLAGS, QUESTION_SETS, replace(FLAGS[QUESTION_SETS], share=0))
    assert client.get(f"/api/projects/{project['id']}/question-sets").status_code == 404
    assert client.get(f"/api/projects/{project['id']}/reports").status_code == 404


def test_a_report_stops_asking_once_the_months_questions_are_used(
    client,
    session,
    services,
    mailer,
    ann,
    plan,  # noqa: F811
):
    plan(question_sets=True, questions_per_month=1)
    project = create_project(client)
    made = make(client, project["id"])
    started = ran(client, made["id"])
    answers(services, "Nothing is due.")
    assert next_job(services, WRITE_QUESTION_REPORT) is not None
    kept = shown(client, started["id"])
    assert (kept["status"], kept["answered"], kept["failed"]) == ("done", 1, 1)
    refusal = check_question(session, session.get(Organization, ann.organization.id))
    assert kept["answers"][1]["error"] == refusal.sentence
    assert questions_counted(session) == 1
    (notice,) = [m for m in told(mailer) if m.subject.startswith("A question set")]
    assert notice.subject == "A question set's report is ready, with questions unanswered"
    assert "1 of its 2 questions were answered. The report says why the others" in notice.text


def test_a_set_is_written_whole_and_what_changed_is_audited(client, session, services, ann):
    project = create_project(client)
    made = make(client, project["id"])
    path = f"/api/question-sets/{made['id']}"
    nothing = client.put(path, json={**SET, "schedule": None})
    assert (nothing.status_code, nothing.json()["detail"]) == (400, NOTHING_RUNS_IT)
    weekly = client.put(path, json={**SET, "schedule": "weekly"})
    assert (weekly.status_code, weekly.json()["detail"]) == (400, NO_WEEKDAY)
    mondays = {**SET, "title": "Mondays", "schedule": "weekly", "weekday": 0, "hour": 9}
    changed = client.put(path, json=mondays)
    assert changed.status_code == 200, changed.text
    moment = datetime.fromisoformat(changed.json()["next_run_at"]).astimezone(BERLIN)
    assert (moment.weekday(), moment.hour) == (0, 9)

    # The job queued for the old moment wakes, finds nothing due and queues the new one.
    for job in waking(session, made["id"]):
        job.run_at = datetime.now(UTC) - timedelta(minutes=1)
    session.commit()
    assert next_job(services, RUN_QUESTION_SET) is not None
    assert reports(session) == []
    (job,) = waking(session, made["id"])
    assert job.run_at == datetime.fromisoformat(changed.json()["next_run_at"])

    # Off, nothing is due: its job wakes and queues none.
    off = client.put(path, json={**mondays, "enabled": False})
    assert off.json()["next_run_at"] is None
    job.run_at = datetime.now(UTC) - timedelta(minutes=1)
    session.commit()
    assert next_job(services, RUN_QUESTION_SET) is not None
    assert waking(session, made["id"]) == [] and reports(session) == []
    # The same again changes nothing, and is no row.
    assert client.put(path, json={**mondays, "enabled": False}).status_code == 200
    assert [row.detail["changed"] for row in audited(session, "question_set_changed")] == [
        ["title", "schedule"],
        ["enabled"],
    ]
    (created,) = audited(session, "question_set_created")
    assert created.detail == {
        "name": "Every morning",
        "questions": 2,
        "schedule": "daily at 08:00",
        "arrivals": False,
        "audience": "maker",
        "enabled": True,
    }


def test_a_deleted_set_runs_no_more_and_the_reports_it_made_stay(client, session, services, ann):
    project = create_project(client)
    made = make(client, project["id"])
    started = ran(client, made["id"])
    early = client.delete(f"/api/reports/{started['id']}")
    assert (early.status_code, early.json()["detail"]) == (409, STILL_WRITTEN)
    answers(services, "Nothing is due.", "Ada signed it.")
    assert next_job(services, WRITE_QUESTION_REPORT) is not None

    assert client.delete(f"/api/question-sets/{made['id']}").status_code == 204
    assert listed(client, project["id"]) == []
    # Its job wakes to find it gone, and makes nothing.
    for job in waking(session, made["id"]):
        job.run_at = datetime.now(UTC) - timedelta(minutes=1)
    session.commit()
    assert next_job(services, RUN_QUESTION_SET) is not None
    assert len(reports(session)) == 1
    kept = shown(client, started["id"])
    assert (kept["question_set_id"], kept["title"]) == (None, "Every morning")

    assert client.delete(f"/api/reports/{started['id']}").status_code == 204
    assert client.get(f"/api/reports/{started['id']}").status_code == 404
    (deleted,) = audited(session, "question_set_deleted")
    assert deleted.detail["name"] == "Every morning"
    (gone,) = audited(session, "question_report_deleted")
    assert gone.detail == {"name": "Every morning", "questions": 2}


def test_a_set_of_a_project_in_the_trash_asks_nothing_until_it_is_restored(
    client, session, services, ann
):
    project = create_project(client)
    made = make(client, project["id"])
    started = ran(client, made["id"])
    assert client.delete(f"/api/projects/{project['id']}").status_code == 204
    # Its schedule makes no report, and stays queued for the next morning.
    due(session, made["id"])
    assert next_job(services, RUN_QUESTION_SET) is not None
    assert len(reports(session)) == 1 and len(waking(session, made["id"])) == 1
    # The report queued before is refused as each question is asked.
    assert next_job(services, WRITE_QUESTION_REPORT) is not None
    report = reports(session)[0]
    assert report.id == started["id"]
    assert [entry["error"] for entry in report.answers] == [PROJECT_GONE, PROJECT_GONE]


def test_the_housekeeper_queues_the_job_of_a_set_that_lost_it(client, session, ann):
    project = create_project(client)
    scheduled = make(client, project["id"])
    arriving = make(client, project["id"], title="Arrivals", schedule=None, on_arrivals=True)
    make(client, project["id"], title="Quiet", schedule=None, on_arrivals=True)
    (document,) = ready(session, project["id"], "INV-1.pdf")
    session.execute(update(QuestionSet).where(QuestionSet.title == "Quiet").values(enabled=False))
    session.commit()
    arrive(session, project["id"], ann.user.id, document)
    session.execute(delete(Job).where(Job.kind == RUN_QUESTION_SET))
    session.commit()

    assert queue_due_sets(session) == 2
    (job,) = waking(session, scheduled["id"])
    assert job.run_at == datetime.fromisoformat(scheduled["next_run_at"])
    assert len(waking(session, arriving["id"])) == 1
    assert queue_due_sets(session) == 0


def test_a_report_lost_while_a_question_was_asked_asks_it_again_and_counts_it_once(
    client, session, services, ann
):
    project = create_project(client)
    made = make(client, project["id"])
    started = ran(client, made["id"])
    # As a worker that died asking the first: counted then, and marked asked.
    row = session.get(QuestionReport, started["id"])
    row.status = "running"
    row.answers = [{**row.answers[0], "status": "running"}, row.answers[1]]
    session.commit()
    answers(services, "Nothing is due.", "Ada signed it.")
    write_report(started["id"], services)
    kept = shown(client, started["id"])
    assert (kept["status"], kept["answered"]) == ("done", 2)
    assert questions_counted(session) == 1

    # One every run of which was lost ends failed, for a member to run it again.
    lost = ran(client, made["id"])
    abandon_report(lost["id"], 3)
    failed = shown(client, lost["id"])
    assert failed["status"] == "failed" and "3 times" in failed["error"]
    assert {entry["status"] for entry in failed["answers"]} == {"failed"}


def test_a_report_is_kept_as_long_as_a_chat(client, session, services, ann):
    project = create_project(client)
    made = make(client, project["id"])
    kept = ran(client, made["id"])
    answers(services, "Nothing is due.", "Ada signed it.")
    assert next_job(services, WRITE_QUESTION_REPORT) is not None
    waiting = ran(client, made["id"])
    long_ago = datetime.now(UTC) - timedelta(days=31)
    session.execute(update(QuestionReport).values(created_at=long_ago))
    session.commit()
    with engine.begin() as connection:
        assert prune_reports(connection) == 1
    assert [report.id for report in reports(session)] == [waiting["id"]]
    assert client.get(f"/api/reports/{kept['id']}").status_code == 404
