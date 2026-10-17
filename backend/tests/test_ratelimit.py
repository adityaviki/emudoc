"""Rate limits: so many uploads, questions and searches a minute per user, so many sign-in
requests per email address and per client address, counted in the database, refused with 429
and the seconds until the minute turns, and pruned by the housekeeper."""

from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.api.auth import CODE_INVALID, SIGN_IN_FAILED
from app.auth import totp_code, totp_step
from app.config import get_settings
from app.maintenance import housekeeping
from app.ratelimit import (
    ADDRESSES,
    QUESTIONS,
    SEARCHES,
    SIGN_INS,
    UPLOADS,
    by_email,
    seconds_left,
    window_start,
)
from tests.test_api import create_project
from tests.test_auth import another_browser, login, signup
from tests.test_chats import ask, new_chat

FROM_ACCOUNT = "from your account"
FROM_ADDRESS = "from your address"
FOR_EMAIL = "for this email address"


@pytest.fixture(autouse=True)
def one_minute(monkeypatch):
    """Hold the limiter's clock in the middle of this minute, as if a test took no time: the
    minute is a fixed window, so one that turned while a test signed in would start its counts
    over, which only `minute_turned` should do."""
    now = datetime.now(UTC).replace(second=30, microsecond=0)
    monkeypatch.setattr("app.ratelimit.datetime", SimpleNamespace(now=lambda tz: now))


def counts(session) -> list[tuple[str, str, int]]:
    """What the table holds: the scope, the key and the count of each minute, oldest first."""
    return [
        tuple(row)
        for row in session.execute(
            text("SELECT scope, key, count FROM rate_limits ORDER BY scope, key, window_start")
        ).all()
    ]


def minute_turned(session, scope: str | None = None) -> None:
    """Move the counts to the minute before, so that the next request starts a fresh one; all
    of them, or one scope's."""
    where = "" if scope is None else " WHERE scope = :scope"
    session.execute(
        text(f"UPDATE rate_limits SET window_start = window_start - interval '1 minute'{where}"),
        {"scope": scope},
    )
    session.commit()


def refused(response, sentence: str) -> None:
    assert response.status_code == 429, response.text
    assert response.json() == {"detail": sentence}
    assert 1 <= int(response.headers["Retry-After"]) <= 60


def test_questions_past_the_rate_are_refused_until_the_minute_turns(
    client, session, monkeypatch, caplog
):
    monkeypatch.setattr(get_settings(), "rate_questions", 1)
    project = create_project(client)
    first, second = new_chat(client, project["id"]), new_chat(client, project["id"])
    ask(client, first["id"], "Notice period?")
    with caplog.at_level("WARNING", logger="app.ratelimit"):
        response = client.post(f"/api/chats/{second['id']}/messages", json={"content": "Billing?"})
    refused(response, QUESTIONS.refusal(1, FROM_ACCOUNT))
    assert "user:1 has made 2 questions in the minute" in caplog.text
    assert "more than RATE_QUESTIONS allows" in caplog.text
    # The refused question is not in the chat, and both requests were counted.
    assert client.get(f"/api/chats/{second['id']}").json()["messages"] == []
    assert counts(session) == [("question", "user:1", 2)]

    minute_turned(session)
    ask(client, second["id"], "Billing?")
    assert counts(session) == [("question", "user:1", 2), ("question", "user:1", 1)]


def test_the_rate_is_each_users_own(client, session, make_account, act_as, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_questions", 1)
    project = create_project(client)
    chat = new_chat(client, project["id"])
    ask(client, chat["id"], "Notice period?")
    again = client.post(f"/api/chats/{chat['id']}/messages", json={"content": "Billing?"})
    assert again.status_code == 429

    act_as(make_account("ann@globex.example"))
    theirs = new_chat(client, create_project(client, "Theirs")["id"])
    ask(client, theirs["id"], "Hello?")
    assert counts(session) == [("question", "user:1", 2), ("question", "user:2", 1)]


def test_uploads_and_searches_have_rates_of_their_own(client, session, sample_csv, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_uploads", 1)
    monkeypatch.setattr(get_settings(), "rate_searches", 2)
    project = create_project(client)
    files = [("files", (sample_csv.name, sample_csv.read_bytes()))]
    assert client.post(f"/api/projects/{project['id']}/documents", files=files).status_code == 201
    second = client.post(f"/api/projects/{project['id']}/documents", files=files)
    refused(second, UPLOADS.refusal(1, FROM_ACCOUNT))
    assert client.get(f"/api/projects/{project['id']}").json()["document_count"] == 1

    # The search and facts routes share one rate.
    assert client.get(f"/api/projects/{project['id']}/search", params={"q": "x"}).status_code == 200
    assert client.get(f"/api/projects/{project['id']}/facts").status_code == 200
    refused(
        client.get(f"/api/projects/{project['id']}/facts/names"), SEARCHES.refusal(2, FROM_ACCOUNT)
    )
    assert counts(session) == [("search", "user:1", 3), ("upload", "user:1", 2)]


def test_sign_in_requests_are_counted_per_address_and_per_email(
    client, session, mailer, signed_in_only, monkeypatch
):
    monkeypatch.setattr(get_settings(), "rate_sign_ins", 2)
    monkeypatch.setattr(get_settings(), "rate_sign_ins_per_address", 3)
    signup(client, "ada@example.com")
    assert counts(session) == [("address", "ip:testclient", 1)]

    # A sign-in that fails counts like one that succeeds; that is the point.
    wrong = {"email": "ada@example.com", "password": "guess"}
    failed = client.post("/api/auth/login", json=wrong)
    assert failed.status_code == 401 and failed.json()["detail"] == SIGN_IN_FAILED
    assert client.post("/api/auth/login", json=wrong).status_code == 401
    refused(client.post("/api/auth/login", json=wrong), ADDRESSES.refusal(3, FROM_ADDRESS))
    assert counts(session) == [
        ("address", "ip:testclient", 4),
        ("sign_in", by_email("ada@example.com").value, 3),
    ]

    # With the address free again, the account is still held, however its address is typed,
    # and so is a reset link for it; another account from the same address is not.
    minute_turned(session, "address")
    typed = {"email": "Ada@Example.com", "password": "guess"}
    refused(client.post("/api/auth/login", json=typed), SIGN_INS.refusal(2, FOR_EMAIL))
    refused(
        client.post("/api/auth/password/forgot", json={"email": "ada@example.com"}),
        SIGN_INS.refusal(2, FOR_EMAIL),
    )
    assert len(mailer.sent) == 1
    other = client.post("/api/auth/login", json={"email": "bob@example.com", "password": "guess"})
    assert other.status_code == 401 and other.json()["detail"] == SIGN_IN_FAILED
    assert counts(session) == [
        ("address", "ip:testclient", 4),
        ("address", "ip:testclient", 3),
        # The keys are hashes, which sort Bob's first.
        ("sign_in", by_email("bob@example.com").value, 1),
        ("sign_in", by_email("ada@example.com").value, 5),
    ]

    # When the minute turns, the account may try again.
    minute_turned(session)
    assert login(client, "ada@example.com") == {"mfa_required": False}


def test_the_authenticator_code_is_held_to_the_accounts_rate(
    client, session, signed_in_only, monkeypatch
):
    signup(client)
    secret = client.post("/api/auth/mfa/setup").json()["secret"]
    enabled = client.post("/api/auth/mfa/enable", json={"code": totp_code(secret, totp_step())})
    assert enabled.status_code == 200
    phone = another_browser(client)
    assert login(phone) == {"mfa_required": True}
    monkeypatch.setattr(get_settings(), "rate_sign_ins", 3)
    # Two guesses so far: the sign-in, and now the first wrong code.
    first = phone.post("/api/auth/mfa/verify", json={"code": "000000"})
    assert first.status_code == 401 and first.json()["detail"] == CODE_INVALID
    assert phone.post("/api/auth/mfa/verify", json={"code": "000001"}).status_code == 401
    refused(
        phone.post("/api/auth/mfa/verify", json={"code": "000002"}), SIGN_INS.refusal(3, FOR_EMAIL)
    )
    # Even the right code is refused until the minute turns.
    right = totp_code(secret, totp_step() + 1)
    assert phone.post("/api/auth/mfa/verify", json={"code": right}).status_code == 429
    minute_turned(session)
    assert phone.post("/api/auth/mfa/verify", json={"code": right}).status_code == 200


def test_resending_the_verification_link_is_held_per_user(
    client, mailer, signed_in_only, monkeypatch
):
    monkeypatch.setattr(get_settings(), "rate_sign_ins", 2)
    signup(client)
    assert client.post("/api/auth/verify/resend").status_code == 202
    assert client.post("/api/auth/verify/resend").status_code == 202
    refused(client.post("/api/auth/verify/resend"), SIGN_INS.refusal(2, FROM_ACCOUNT))
    assert len(mailer.sent) == 3


def test_zero_removes_a_limit_and_counts_nothing(client, session, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_searches", 0)
    project = create_project(client)
    for _ in range(3):
        assert client.get(f"/api/projects/{project['id']}/facts").status_code == 200
    assert counts(session) == []


def test_the_housekeeper_removes_the_counts_of_minutes_long_past(client, session, services):
    project = create_project(client)
    assert client.get(f"/api/projects/{project['id']}/facts").status_code == 200
    minute_turned(session)
    housekeeping(services.storage)
    assert counts(session) == [("search", "user:1", 1)]
    session.execute(text("UPDATE rate_limits SET window_start = window_start - interval '2 hours'"))
    session.commit()
    housekeeping(services.storage)
    assert counts(session) == []


def test_the_minute_is_fixed_and_the_wait_is_the_rest_of_it():
    now = datetime(2026, 10, 13, 12, 0, 15, tzinfo=UTC)
    start = window_start(now)
    assert start == datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
    assert seconds_left(now, start) == 45
    assert seconds_left(start, start) == 60
    assert seconds_left(start + timedelta(seconds=59, microseconds=500_000), start) == 1
    # A moment in another zone falls in the same minute as its UTC reading.
    east = datetime(2026, 10, 13, 14, 0, 15, tzinfo=timezone(timedelta(hours=2)))
    assert window_start(east) == start


def test_a_refused_sign_in_logs_no_email_address(client, session, monkeypatch, caplog):
    monkeypatch.setattr(get_settings(), "rate_sign_ins", 1)
    wrong = {"email": "Ada@Example.com", "password": "guess"}
    assert client.post("/api/auth/login", json=wrong).status_code == 401
    with caplog.at_level("WARNING", logger="app.ratelimit"):
        refused(client.post("/api/auth/login", json=wrong), SIGN_INS.refusal(1, FOR_EMAIL))
    # The address is counted and logged by its hash, however it was typed, never as it is.
    key = by_email("ada@example.com").value
    assert key == by_email(" ADA@example.com ").value and len(key) == len("email:") + 64
    assert f"A request was refused: {key} has made 2 sign-in requests" in caplog.text
    assert "@" not in caplog.text
    assert "example.com" not in str(counts(session))
