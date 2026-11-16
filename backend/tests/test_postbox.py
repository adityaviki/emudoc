"""The mail the app posts: a job in the queue with its message encrypted, sent by the worker's
mail lane, tried again while the relay says "later", given up and told when it will not take
it; and the notices, whose reader may stop their kind from the message itself."""

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app.bounces import Undeliverable, spare, suppressed
from app.config import MAIL_THREADS
from app.encrypted import TOTP_SECRETS, keys
from app.jobs import LIVE, SEND_EMAIL, claim
from app.mail import Content, Link, MailError, Message
from app.metrics import MAILER
from app.models import AuditEvent, Job, User
from app.postbox import (
    MAX_TRIES,
    STOP_NOTICES,
    UNSUBSCRIBE_PAGE,
    UNSUBSCRIBE_PLACE,
    notice,
    post,
    post_notice,
    unsubscribe_token,
)
from app.worker import Worker
from tests.conftest import FakeMailer
from tests.test_auth import signup
from tests.test_encrypted import keyed, new_key  # noqa: F401
from tests.test_jobs import wait_until
from tests.test_metrics import Counted

APP = "https://emudoc.example.com"
NEWS = Content("Your documents are ready", ("Hi Ada,", "12 of 12 are ready.", Link(APP, "Open")))


def mail_jobs(session) -> list[Job]:
    session.expire_all()
    return session.scalars(select(Job).where(Job.kind == SEND_EMAIL).order_by(Job.id)).all()


def due_now(session) -> None:
    """Bring the waits of the queued mail to an end."""
    session.execute(text("UPDATE jobs SET run_at = now() WHERE status = 'queued'"))
    session.commit()


class Refusing(FakeMailer):
    """A relay that refuses every message, for now or for good."""

    def __init__(self, later: bool):
        super().__init__()
        self.later = later
        self.tried: list[Message] = []

    def send(self, message: Message) -> None:
        self.tried.append(message)
        raise MailError("The email could not be sent.", transient=self.later)


def test_a_link_is_queued_encrypted_and_sent_by_the_worker(client, session, mailer):
    signup(client)
    counted = Counted(("emudoc_mail_sent_total", {"outcome": "sent"}))
    assert (
        client.post("/api/auth/password/forgot", json={"email": "ada@example.com"}).json() is None
    )

    (job,) = [job for job in mail_jobs(session) if job.status == "queued"]
    # An account's mail is no organisation's, and a person waits on it.
    assert (job.org_id, job.priority) == (None, LIVE)
    assert set(job.payload) == {"email", "subject", "message", "try", "trace_id"}
    assert job.payload["subject"] == "Reset your password" and job.payload["try"] == 1
    # The link signs in, so the queue holds it encrypted, and nothing of whom it is for: the
    # value is base64url, which has no "@" of an address nor "#" of a link.
    sealed = job.payload["message"]
    assert sealed.startswith("v1.") and "@" not in sealed and "#" not in sealed

    to, subject, body = mailer.sent[-1]
    assert (to, subject) == ("ada@example.com", "Reset your password")
    assert "/reset-password#token=" in body
    assert mailer.messages[-1].html is not None
    assert counted("emudoc_mail_sent_total", outcome="sent") == 1
    # Once sent, the job keeps the subject alone, for the operator.
    (done,) = [found for found in mail_jobs(session) if found.id == job.id]
    assert done.status == "done" and "message" not in done.payload


def test_a_relay_that_answers_later_is_tried_again_then_given_up(session, caplog):
    counted = Counted(
        ("emudoc_mail_sent_total", {"outcome": "retried"}),
        ("emudoc_mail_sent_total", {"outcome": "failed"}),
    )
    post(session, "ada@example.com", NEWS)
    session.commit()
    relay = Refusing(later=True)
    for tries in range(1, MAX_TRIES + 1):
        before = datetime.now(UTC)
        assert relay.deliver() == 1
        after = datetime.now(UTC)
        jobs = mail_jobs(session)
        assert all("message" not in job.payload for job in jobs[:-1])
        if tries < MAX_TRIES:
            # Queued again after a wait that doubles, the message's key kept for the provider.
            waiting = jobs[-1]
            assert waiting.status == "queued" and waiting.payload["try"] == tries + 1
            wait = timedelta(minutes=2 * 2 ** (tries - 1))
            assert before + wait <= waiting.run_at <= after + wait
            assert relay.deliver() == 0
            due_now(session)
    assert len({message.key for message in relay.tried}) == 1
    assert len(relay.tried) == MAX_TRIES
    last = mail_jobs(session)[-1]
    assert last.status == "failed" and "message" not in last.payload
    assert last.last_error == (
        "The email 'Your documents are ready' was refused 5 times over 30 minutes"
    )
    assert counted("emudoc_mail_sent_total", outcome="retried") == MAX_TRIES - 1
    assert counted("emudoc_mail_sent_total", outcome="failed") == 1
    assert "it is tried again in 2 minutes (try 2 of 5)" in caplog.text


def test_a_message_refused_for_good_is_not_tried_again(session):
    post(session, "ada@example.com", NEWS, org_id=None)
    session.commit()
    relay = Refusing(later=False)
    assert relay.deliver() == 1
    (job,) = mail_jobs(session)
    assert job.status == "failed"
    assert job.last_error == "The relay refused the email 'Your documents are ready' for good"


def test_a_message_whose_key_is_gone_is_given_up(session, keyed):  # noqa: F811
    first = new_key()
    keyed(first)
    post(session, "ada@example.com", NEWS)
    session.commit()
    keyed(new_key())
    mailer = FakeMailer()
    assert mailer.deliver() == 1 and mailer._sent == []
    (job,) = mail_jobs(session)
    assert job.status == "failed" and "message" not in job.payload
    assert job.last_error.startswith("The email 'Your documents are ready' could not be read")
    assert "ENCRYPTION_KEYS does not hold" in job.last_error


def test_mail_of_no_organisation_is_claimed_beside_an_organisation_s(session, account):
    post(session, "bob@example.com", NEWS, org_id=account.organization.id)
    post(session, "ada@example.com", NEWS)
    session.commit()
    claimed = [claim("test", [SEND_EMAIL]), claim("test", [SEND_EMAIL])]
    assert sorted(job.org_id or 0 for job in claimed) == [0, account.organization.id]
    assert claim("test", [SEND_EMAIL]) is None


def test_the_worker_sends_the_mail_in_a_lane_of_its_own(session, services):
    mailer = FakeMailer()
    services.mailer = mailer
    worker = Worker(services, concurrency=1, answer_concurrency=1)
    worker.start()
    try:
        assert worker.lanes()[MAILER] == (MAIL_THREADS, 0)
        post(session, "ada@example.com", NEWS)
        session.commit()
        worker.notify()
        wait_until(lambda: len(mailer._sent) == 1)
    finally:
        worker.stop()
    assert mailer._sent[0][:2] == ("ada@example.com", "Your documents are ready")


def test_a_notice_carries_the_way_to_stop_it(session, account):
    message = notice(account.user, "checks_run", NEWS, APP)
    assert message.text.endswith(
        f"{STOP_NOTICES}\n\n{APP}{UNSUBSCRIBE_PAGE}#token=" + token_of(message) + "\n"
    )
    # A mail program unsubscribes in one click, by a request it posts itself (RFC 8058).
    assert message.headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert message.headers["List-Unsubscribe"] == (
        f"<{APP}/api/mail/unsubscribe?token={token_of(message)}>"
    )
    assert ">Stop these emails</a>" in message.html


def token_of(message: Message) -> str:
    """The unsubscribe token a notice's header names, as it is written in the address."""
    url = message.headers["List-Unsubscribe"].strip("<>")
    return urlsplit(url).query.removeprefix("token=")


def test_a_notice_is_posted_to_whoever_wants_it_at_an_address_that_takes_it(session, account):
    user = account.user
    assert post_notice(session, user, "checks_run", NEWS, APP) is not None
    # Each kind's email is chosen apart.
    user.choose("checks_run", "email", False)
    assert post_notice(session, user, "checks_run", NEWS, APP) is None
    assert post_notice(session, user, "upload_indexed", NEWS, APP) is not None
    user.choose("checks_run", "email", True)
    # However the provider writes the address.
    spare(session, [Undeliverable(f" {user.email.upper()}", "bounce")])
    assert post_notice(session, user, "checks_run", NEWS, APP) is None
    session.rollback()


def test_the_link_in_a_notice_stops_its_kind_by_email_without_signing_in(client, session, account):
    message = notice(account.user, "checks_run", NEWS, APP)
    token = parse_qs(urlsplit(message.headers["List-Unsubscribe"].strip("<>")).query)["token"][0]
    # A mail program posts the form RFC 8058 names, with no header of the app's.
    program = TestClient(client.app)
    one_click = program.post(
        "/api/mail/unsubscribe",
        params={"token": token},
        data={"List-Unsubscribe": "One-Click"},
    )
    assert one_click.status_code == 204
    session.expire_all()
    user = session.get(User, account.user.id)
    assert user.notices == {"checks_run": {"email": False}}
    # The other kind still comes by email, and this one still in the app.
    assert user.wants("upload_indexed", "email") and user.wants("checks_run", "app")
    # Again changes nothing, and says nothing either.
    assert program.post("/api/mail/unsubscribe", params={"token": token}).status_code == 204

    for forged in (
        "1",
        unsubscribe_token(account.user.id, "checks_run")[:-4] + "AAAA",
        "v1.0000.x",
        # A kind there is none of.
        keys().encrypt(f"{account.user.id}:digest", UNSUBSCRIBE_PLACE),
    ):
        refused = program.post("/api/mail/unsubscribe", params={"token": forged})
        assert refused.status_code == 400
        assert refused.json()["detail"].startswith("This link to stop the notices is not valid")
    # A token is the user's id encrypted for this purpose alone: the same id encrypted for
    # another place, an authenticator secret's, passes for none.
    elsewhere = keys().encrypt(str(account.user.id), TOTP_SECRETS.place(account.user.id))
    assert program.post("/api/mail/unsubscribe", params={"token": elsewhere}).status_code == 400

    # A notice of a kind no longer told, mailed before comments went, has nothing left to stop.
    past = keys().encrypt(f"{account.user.id}:mentioned", UNSUBSCRIBE_PLACE)
    assert program.post("/api/mail/unsubscribe", params={"token": past}).status_code == 204
    session.expire_all()
    assert session.get(User, account.user.id).notices == {"checks_run": {"email": False}}

    # A notice mailed before the kinds were chosen apart names the user alone, and stops every
    # kind's email.
    before = keys().encrypt(str(account.user.id), UNSUBSCRIBE_PLACE)
    assert program.post("/api/mail/unsubscribe", params={"token": before}).status_code == 204
    session.expire_all()
    user = session.get(User, account.user.id)
    assert not user.wants("upload_indexed", "email") and not user.wants("checks_run", "email")
    assert user.wants("upload_indexed", "app") and user.wants("checks_run", "app")


def test_the_account_page_chooses_how_each_kind_of_notice_is_told(client, session, signed_in_only):
    signup(client)
    assert client.get("/api/auth/me").json()["notices"] == [
        {"kind": "upload_indexed", "app": True, "email": True},
        {"kind": "project_export", "app": True, "email": True},
        {"kind": "checks_run", "app": True, "email": True},
        {"kind": "report_ready", "app": True, "email": True},
        {"kind": "weekly_digest", "app": False, "email": False},
    ]
    off = client.patch(
        "/api/auth/profile", json={"notices": [{"kind": "checks_run", "email": False}]}
    )
    assert off.status_code == 200
    assert off.json()["notices"][2] == {"kind": "checks_run", "app": True, "email": False}
    rows = session.scalars(select(AuditEvent).where(AuditEvent.action == "profile_changed")).all()
    assert [row.detail for row in rows] == [{"changed": ["notices"]}]
    # What is so already changes nothing, and records nothing.
    same = client.patch(
        "/api/auth/profile", json={"notices": [{"kind": "checks_run", "app": True}]}
    )
    assert same.status_code == 200
    assert session.scalar(select(func.count()).where(AuditEvent.action == "profile_changed")) == 1
    refused = client.patch("/api/auth/profile", json={"notices": [{"kind": "digest"}]})
    assert refused.status_code == 422
    # Nor is a kind no longer told chosen.
    past = client.patch("/api/auth/profile", json={"notices": [{"kind": "mentioned"}]})
    assert past.status_code == 422

    # The provider said to spare the address: turning a kind off in the app writes to it no
    # more than before, and asking for one by email again writes to it again.
    spare(session, [Undeliverable("ada@example.com", "complaint")])
    session.commit()
    client.patch("/api/auth/profile", json={"notices": [{"kind": "upload_indexed", "app": False}]})
    assert suppressed(session, "ada@example.com")
    on = client.patch(
        "/api/auth/profile", json={"notices": [{"kind": "checks_run", "email": True}]}
    )
    assert on.json()["notices"] == [
        {"kind": "upload_indexed", "app": False, "email": True},
        {"kind": "project_export", "app": True, "email": True},
        {"kind": "checks_run", "app": True, "email": True},
        {"kind": "report_ready", "app": True, "email": True},
        {"kind": "weekly_digest", "app": False, "email": False},
    ]
    assert not suppressed(session, "ada@example.com")


@pytest.mark.parametrize("days", [364, 366])
def test_an_address_is_spared_for_a_year(session, days):
    spare(session, [Undeliverable("ada@example.com", "bounce")])
    session.execute(
        text("UPDATE mail_suppressions SET recorded_at = now() - make_interval(days => :days)"),
        {"days": days},
    )
    session.commit()
    assert suppressed(session, "ada@example.com") is (days < 365)
