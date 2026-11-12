"""The weekly digest of what arrived (app/digests.py): a notice a member turns on, told on Monday
morning of their zone, naming for each project they see the documents that arrived in it over
the week, by their names and counts; the hourly job that tells it, and the housekeeper that
queues that job when it is lost."""

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, select

from app import digests
from app.digests import DIGEST_HOUR, due_monday, queue_digests, send_digests
from app.jobs import LIVE, SEND_DIGESTS
from app.models import Document, Job, Notification, Organization, Project
from tests.test_automations import next_job
from tests.test_notices import APP, at_app, told  # noqa: F401 (a fixture)

BERLIN = ZoneInfo("Europe/Berlin")
# Monday 26 October 2026 at 08:30 in Berlin (CET since the 25th), 07:30 UTC.
MONDAY = datetime(2026, 10, 26, 7, 30, tzinfo=UTC)


@pytest.fixture
def ann(make_account, act_as, session):
    """The API acts as Ann, the owner of Globex, who works in Berlin's time and turned the digest
    on, in the app and by email."""
    account = make_account("ann@globex.example")
    account.user.time_zone = "Europe/Berlin"
    session.commit()
    act_as(account)
    return account


def turned_on(client, **ways: bool) -> None:
    ways = ways or {"app": True, "email": True}
    response = client.patch(
        "/api/auth/profile", json={"notices": [{"kind": "weekly_digest", **ways}]}
    )
    assert response.status_code == 200, response.text


def project(session, org_id: int, name: str, **columns) -> Project:
    row = Project(org_id=org_id, name=name, **columns)
    session.add(row)
    session.commit()
    return row


def arrived(session, project_id: int, at: datetime, *names: str, **columns) -> None:
    """Documents of the project that arrived at the moment, by their names."""
    for place, name in enumerate(names):
        session.add(
            Document(
                project_id=project_id,
                filename=name,
                mime_type="application/pdf",
                size_bytes=1,
                sha256=uuid.uuid4().hex,
                storage_key="",
                status="ready",
                stages={},
                created_at=at + timedelta(seconds=place),
                **columns,
            )
        )
    session.commit()


def queued(session) -> list[Job]:
    session.expire_all()
    return list(
        session.scalars(select(Job).where(Job.kind == SEND_DIGESTS, Job.status == "queued"))
    )


def test_the_digest_is_off_until_a_member_turns_it_on(client, ann):
    choices = {choice["kind"]: choice for choice in client.get("/api/auth/me").json()["notices"]}
    assert choices["weekly_digest"] == {"kind": "weekly_digest", "app": False, "email": False}
    # The other kinds stay on until turned off.
    assert choices["report_ready"] == {"kind": "report_ready", "app": True, "email": True}
    turned_on(client, email=True)
    choices = {choice["kind"]: choice for choice in client.get("/api/auth/me").json()["notices"]}
    assert choices["weekly_digest"] == {"kind": "weekly_digest", "app": False, "email": True}


def test_monday_morning_tells_what_arrived_over_the_week_in_the_projects_a_member_sees(
    client, session, mailer, ann, make_account
):
    org_id = ann.organization.id
    contracts = project(session, org_id, "Contracts")
    invoices = project(session, org_id, "Invoices")
    private = project(session, org_id, "Board", visibility="private")
    week_ago = MONDAY - timedelta(days=6)
    arrived(session, invoices.id, week_ago, "INV-1.pdf", "INV-2.pdf", "INV-3.pdf")
    arrived(session, contracts.id, week_ago, "Lease.pdf")
    arrived(session, private.id, week_ago, "Minutes.pdf")
    # Left out: in the trash, from before the week, and after Monday's hour.
    arrived(session, contracts.id, week_ago, "Old draft.pdf", deleted_at=MONDAY)
    arrived(session, contracts.id, MONDAY - timedelta(days=8), "Last week.pdf")
    arrived(session, contracts.id, MONDAY + timedelta(hours=1), "Today.pdf")
    bob = make_account("bob@globex.example", ann.organization, "member")
    turned_on(client)
    bob.user.notices = {"weekly_digest": {"email": True}}
    session.commit()

    # Sunday evening and Monday before the hour, nothing; at 08:30 in Berlin, Ann's.
    assert send_digests(now=MONDAY - timedelta(hours=12)) == 0
    assert send_digests(now=MONDAY - timedelta(hours=1)) == 0
    assert send_digests(now=MONDAY) == 1
    (notice,) = told(mailer, "ann@globex.example")
    assert notice.subject == "What arrived in your projects this week"
    assert (
        "Over the past week, 5 documents arrived in 3 projects of Globex.\n\n"
        "Invoices: INV-1.pdf, INV-2.pdf, INV-3.pdf.\n\n"
        "Board: Minutes.pdf.\n\n"
        "Contracts: Lease.pdf." in notice.text
    )
    assert f"{APP}/\n" in notice.text
    (row,) = session.scalars(select(Notification).where(Notification.kind == "weekly_digest"))
    assert (row.user_id, row.link) == (ann.user.id, "/")
    assert row.body.startswith("Over the past week, 5 documents arrived")

    # Bob, in UTC, is told at 08:00 there, by email alone, without the private project.
    assert send_digests(now=MONDAY + timedelta(minutes=30)) == 1
    (notice,) = told(mailer, "bob@globex.example")
    assert "4 documents arrived in 2 projects of Globex" in notice.text
    assert "Minutes.pdf" not in notice.text
    in_app = session.scalars(select(Notification).where(Notification.user_id == bob.user.id))
    assert in_app.all() == []

    # Each is told the week once, which the account's export says.
    assert send_digests(now=MONDAY + timedelta(hours=3)) == 0
    assert len(told(mailer)) == 2
    (membership,) = client.get("/api/auth/account/export").json()["organizations"]
    assert membership["digested_on"] == "2026-10-26"


def test_an_organisation_may_tell_it_until_its_members_choose_and_an_empty_week_tells_nothing(
    session, mailer, ann, make_account
):
    org_id = ann.organization.id
    contracts = project(session, org_id, "Contracts")
    bob = make_account("bob@globex.example", ann.organization, "member")
    # The owners turn it on in the app for the members who did not choose.
    session.get(Organization, org_id).choose("weekly_digest", "app", True)
    session.commit()
    # The Monday before, nothing had arrived: nothing is told.
    assert send_digests(now=MONDAY - timedelta(days=7, minutes=-30)) == 0
    arrived(session, contracts.id, MONDAY - timedelta(days=2), "Lease.pdf")
    assert send_digests(now=MONDAY + timedelta(minutes=30)) == 2
    notified = session.scalars(select(Notification.user_id).order_by(Notification.user_id)).all()
    assert notified == [ann.user.id, bob.user.id]
    # In the app alone: its email is off until chosen too.
    assert told(mailer) == []


def test_a_digest_that_breaks_holds_up_nobody_elses_and_is_told_the_next_hour(
    session, mailer, ann, make_account, monkeypatch
):
    contracts = project(session, ann.organization.id, "Contracts")
    arrived(session, contracts.id, MONDAY - timedelta(days=2), "Lease.pdf")
    bob = make_account("bob@globex.example", ann.organization, "member")
    for account in (ann, bob):
        account.user.notices = {"weekly_digest": {"email": True}}
        account.user.time_zone = "Europe/Berlin"
    session.commit()
    read = digests.arrivals_of
    calls = []

    def breaking_once(*args):
        calls.append(args)
        if len(calls) == 1:
            raise RuntimeError("The database went away.")
        return read(*args)

    monkeypatch.setattr(digests, "arrivals_of", breaking_once)
    assert send_digests(now=MONDAY) == 1
    assert [message.to for message in told(mailer)] == ["bob@globex.example"]
    assert send_digests(now=MONDAY + timedelta(hours=1)) == 1
    assert {message.to for message in told(mailer)} == {"ann@globex.example", "bob@globex.example"}


def test_the_digests_job_wakes_every_hour_and_the_housekeeper_queues_a_lost_one(session, services):
    assert queue_digests(session) is True
    assert queue_digests(session) is False
    assert next_job(services, SEND_DIGESTS) is not None
    # It queues itself for the top of the next hour.
    (job,) = queued(session)
    now = datetime.now(UTC)
    assert job.run_at.minute == 0 and now < job.run_at <= now + timedelta(hours=1)
    assert (job.org_id, job.priority) == (None, LIVE)
    assert queue_digests(session) is False
    session.execute(delete(Job).where(Job.kind == SEND_DIGESTS))
    session.commit()
    assert queue_digests(session) is True


def test_a_monday_is_due_from_the_hour_of_the_members_zone():
    assert due_monday(MONDAY, BERLIN) == MONDAY.date()
    assert due_monday(MONDAY - timedelta(hours=1), BERLIN) is None
    # 07:30 UTC is before the hour there.
    assert due_monday(MONDAY, UTC) is None
    assert DIGEST_HOUR == 8
    # Tokyo's Monday is Sunday evening in UTC; Tuesday morning there is too late.
    tokyo = ZoneInfo("Asia/Tokyo")
    assert due_monday(datetime(2026, 10, 25, 23, 30, tzinfo=UTC), tokyo) == MONDAY.date()
    assert due_monday(datetime(2026, 10, 26, 23, 30, tzinfo=UTC), tokyo) is None
