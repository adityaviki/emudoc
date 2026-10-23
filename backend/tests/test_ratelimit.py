"""Rate limits: so many uploads, questions and searches a minute per user, so many sign-in
requests per email address and per client address, counted in the database, refused with 429
and the seconds until the minute turns, and pruned by the housekeeper; and so many failed
sign-ins an hour, past which the browsers that have not signed in to the account are paused
while its own carry on."""

from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text, update

from app.api.auth import (
    CODE_INVALID,
    PAUSED_BROWSER,
    PAUSED_NEW,
    PAUSED_SESSION,
    SIGN_IN_FAILED,
    WRONG_PASSWORD,
)
from app.auth import BROWSER_COOKIE, BROWSER_KNOWN_FOR, token_hash, totp_code, totp_step
from app.config import get_settings
from app.maintenance import housekeeping
from app.models import Browser, User
from app.ratelimit import (
    ADDRESSES,
    FAILED_SIGN_INS,
    HOUR,
    QUESTIONS,
    SEARCHES,
    SIGN_INS,
    UPLOADS,
    by_email,
    seconds_left,
    window_end,
    window_start,
)
from tests.test_api import create_project
from tests.test_audit import actions
from tests.test_auth import NEW_PASSWORD, PASSWORD, another_browser, login, signup
from tests.test_chats import ask, new_chat, try_again

FROM_ACCOUNT = "from your account"
FROM_ADDRESS = "from your address"
FOR_EMAIL = "for this email address"
FROM_BROWSER = "from this browser"


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


def counted_afresh(session) -> None:
    """Forget every count so far, as if the requests that made them were long past: a test of
    the sign-ins starts after the sign-up's own requests."""
    session.execute(text("DELETE FROM rate_limits"))
    session.commit()


def hour_turned(session) -> None:
    """Move the failed sign-ins to the hour before, so that a pause ends."""
    session.execute(
        text(
            "UPDATE rate_limits SET window_start = window_start - interval '1 hour' "
            "WHERE scope = 'failed_sign_in'"
        )
    )
    session.commit()


def refused(response, sentence: str, within: int = 60) -> None:
    assert response.status_code == 429, response.text
    assert response.json() == {"detail": sentence}
    assert 1 <= int(response.headers["Retry-After"]) <= within


def paused(response, sentence: str, allowed: int) -> None:
    """Refused for the rest of the hour, the minutes left in the sentence."""
    seconds = int(response.headers.get("Retry-After", 0))
    minutes = -(-seconds // 60)
    wait = f"{minutes} {'minute' if minutes == 1 else 'minutes'}"
    refused(response, sentence.format(allowed=allowed, wait=wait), within=3600)


def attempt(client, password="guess", email="ada@example.com"):
    return client.post("/api/auth/login", json={"email": email, "password": password})


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


def test_trying_an_answer_again_counts_as_a_question(client, session, monkeypatch):
    # Each try runs the model as a question does.
    monkeypatch.setattr(get_settings(), "rate_questions", 1)
    chat = new_chat(client, create_project(client)["id"])
    stopped = ask(client, chat["id"], "Notice period?")["answer"]["id"]
    client.post(f"/api/chats/{chat['id']}/messages/{stopped}/stop")
    refused(try_again(client, chat["id"], stopped), QUESTIONS.refusal(1, FROM_ACCOUNT))
    assert client.get(f"/api/chats/{chat['id']}").json()["messages"][-1]["status"] == "stopped"

    minute_turned(session)
    assert try_again(client, chat["id"], stopped).status_code == 200


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
    monkeypatch.setattr(get_settings(), "rate_sign_ins", 4)
    monkeypatch.setattr(get_settings(), "rate_sign_ins_per_address", 4)
    signup(client, "ada@example.com")
    ada, bob = by_email("ada@example.com").value, by_email("bob@example.com").value
    # Both of the sign-up's requests count for the client's address, and asking for the link
    # counts for the address it mails as well, as a reset link's request does.
    assert counts(session) == [("address", "ip:testclient", 2), ("sign_in", ada, 1)]

    # A sign-in that fails counts like one that succeeds; that is the point. The guesser's
    # browser never signed in to the account, so its guesses count under the address.
    guesser = another_browser(client)
    failed = attempt(guesser)
    assert failed.status_code == 401 and failed.json()["detail"] == SIGN_IN_FAILED
    assert attempt(guesser).status_code == 401
    refused(attempt(guesser), ADDRESSES.refusal(4, FROM_ADDRESS))
    # The failures are counted for the hour as well; the refused request is not one.
    assert counts(session) == [
        ("address", "ip:testclient", 5),
        ("failed_sign_in", ada, 2),
        ("sign_in", ada, 4),
    ]

    # With the address free again, the account is still held, however its address is typed,
    # and so is a reset link for it; another account from the same address is not.
    minute_turned(session, "address")
    refused(attempt(guesser, email="Ada@Example.com"), SIGN_INS.refusal(4, FOR_EMAIL))
    refused(
        guesser.post("/api/auth/password/forgot", json={"email": "ada@example.com"}),
        SIGN_INS.refusal(4, FOR_EMAIL),
    )
    assert len(mailer.sent) == 1
    other = attempt(guesser, email="bob@example.com")
    assert other.status_code == 401 and other.json()["detail"] == SIGN_IN_FAILED
    assert counts(session) == [
        ("address", "ip:testclient", 5),
        ("address", "ip:testclient", 3),
        # The keys are hashes, which sort Bob's first.
        ("failed_sign_in", bob, 1),
        ("failed_sign_in", ada, 2),
        ("sign_in", bob, 1),
        ("sign_in", ada, 6),
    ]

    # When the minute turns, the account may try again.
    minute_turned(session)
    assert login(guesser, "ada@example.com") == {"mfa_required": False}


def test_sign_up_links_are_asked_for_at_the_sign_in_rate(client, session, mailer, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_sign_ins", 1)
    signup(another_browser(client), "ada@example.com")
    counted_afresh(session)
    # Nobody's inbox is flooded with links, or with word that the address has an account, and
    # the refusal reads the same for both.
    for address in ("eve@example.com", "ada@example.com"):
        assert client.post("/api/auth/signup", json={"email": address}).status_code == 202
        refused(
            client.post("/api/auth/signup", json={"email": address.upper()}),
            SIGN_INS.refusal(1, FOR_EMAIL),
        )
    assert [to for to, _, _ in mailer.sent[1:]] == ["eve@example.com", "ada@example.com"]


def test_the_authenticator_code_is_held_to_the_accounts_rate(
    client, session, signed_in_only, monkeypatch
):
    signup(client)
    counted_afresh(session)
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


def test_the_browsers_an_account_signed_in_with_are_counted_apart(
    client, session, signed_in_only, monkeypatch
):
    monkeypatch.setattr(get_settings(), "rate_sign_ins", 2)
    signup(client)
    counted_afresh(session)
    assert client.cookies[BROWSER_COOKIE]
    browser = session.scalar(select(Browser.id))
    # Someone who knows the address uses up its minute from a browser of their own...
    stranger = another_browser(client)
    assert attempt(stranger).status_code == 401
    assert attempt(stranger).status_code == 401
    refused(attempt(stranger, PASSWORD), SIGN_INS.refusal(2, FOR_EMAIL))

    # ...and the account's own browser signs in all the same, held to a minute of its own.
    assert client.post("/api/auth/logout").status_code == 204
    assert login(client) == {"mfa_required": False}
    assert attempt(client).status_code == 401
    refused(attempt(client), SIGN_INS.refusal(2, FROM_BROWSER))
    assert counts(session) == [
        ("address", "ip:testclient", 6),
        ("failed_sign_in", f"browser:{browser}", 1),
        ("failed_sign_in", by_email("ada@example.com").value, 2),
        ("sign_in", f"browser:{browser}", 3),
        ("sign_in", by_email("ada@example.com").value, 3),
    ]

    # The cookie is the browser's own: it goes only with the account's routes, outlives the
    # session by its days, and is not what the database holds.
    minute_turned(session, "sign_in")
    response = attempt(client, PASSWORD)
    assert response.status_code == 200, response.text
    (cookie,) = [c for c in response.headers.get_list("set-cookie") if BROWSER_COOKIE in c]
    assert "Path=/api/auth" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert f"Max-Age={BROWSER_KNOWN_FOR.days * 86400}" in cookie
    assert session.get(Browser, browser).token_hash != token_hash(client.cookies[BROWSER_COOKIE])


def test_failed_sign_ins_pause_the_browsers_an_account_never_signed_in_with(
    client, session, mailer, signed_in_only, monkeypatch, caplog
):
    monkeypatch.setattr(get_settings(), "rate_sign_ins", 0)
    monkeypatch.setattr(get_settings(), "rate_failed_sign_ins", 3)
    account = signup(client)
    mailer.sent.clear()
    stranger = another_browser(client)
    for _ in range(3):
        assert attempt(stranger).status_code == 401
    # The third failure paused them: the next try is refused before the password is looked
    # at, the right one too, from that browser or any other new one, until the hour ends.
    with caplog.at_level("WARNING", logger="app.ratelimit"):
        paused(attempt(stranger, PASSWORD), PAUSED_NEW, 3)
    paused(attempt(another_browser(client), PASSWORD), PAUSED_NEW, 3)
    key = by_email("ada@example.com").value
    assert f"{key} has made 3 failed sign-ins in the hour" in caplog.text
    assert "as many as RATE_FAILED_SIGN_INS allows" in caplog.text

    # The owner is told once, after the reply, with the hour's end and the way out.
    assert len(mailer.sent) == 1
    to, subject, body = mailer.sent[0]
    assert to == "ada@example.com" and subject == "Sign-ins to your account were paused"
    assert "3 failed attempts this hour" in body
    assert f"paused until {window_end(FAILED_SIGN_INS):%H:%M} (UTC)" in body
    assert mailer.link() == "http://testserver/forgot-password"
    org_id = account["organization"]["id"]
    assert actions(session, org_id)[-4:] == [
        ("sign_in_failed", {"reason": "password"}),
        ("sign_in_failed", {"reason": "password"}),
        ("sign_in_failed", {"reason": "password"}),
        ("sign_in_paused", {"from": "new_browsers"}),
    ]

    # The account's own browser signs in as before.
    assert login(client) == {"mfa_required": False}

    # An address without an account is answered alike, and nobody is told.
    for _ in range(3):
        assert attempt(stranger, email="nobody@example.com").status_code == 401
    paused(attempt(stranger, email="nobody@example.com"), PAUSED_NEW, 3)
    assert len(mailer.sent) == 1

    # When the hour turns, the new browsers may try again.
    hour_turned(session)
    assert login(stranger) == {"mfa_required": False}


def test_a_browser_that_signed_in_before_is_paused_by_itself(
    client, session, mailer, signed_in_only, monkeypatch
):
    monkeypatch.setattr(get_settings(), "rate_failed_sign_ins", 2)
    account = signup(client)
    for _ in range(2):
        assert attempt(client).status_code == 401
    paused(attempt(client, PASSWORD), PAUSED_BROWSER, 2)
    assert actions(session, account["organization"]["id"])[-1] == (
        "sign_in_paused",
        {"from": "known_browser"},
    )
    # Nobody else is held by it, and the owner is not mailed about their own browser.
    assert login(another_browser(client)) == {"mfa_required": False}
    assert [subject for _, subject, _ in mailer.sent] == ["Finish creating your Emudoc account"]


def test_a_new_password_ends_the_pause_and_the_browser_that_chose_it_is_the_accounts(
    client, session, mailer, signed_in_only, monkeypatch
):
    monkeypatch.setattr(get_settings(), "rate_sign_ins", 0)
    monkeypatch.setattr(get_settings(), "rate_failed_sign_ins", 2)
    signup(client)
    laptop = session.scalar(select(Browser.id))
    stranger, phone = another_browser(client), another_browser(client)
    for _ in range(2):
        assert attempt(stranger).status_code == 401
    paused(attempt(phone, PASSWORD), PAUSED_NEW, 2)

    # The owner, on a phone that never signed in, resets the password there.
    forgot = phone.post("/api/auth/password/forgot", json={"email": "ada@example.com"})
    assert forgot.status_code == 202
    reset = phone.post(
        "/api/auth/password/reset", json={"token": mailer.token(), "password": NEW_PASSWORD}
    )
    assert reset.status_code == 204 and phone.cookies[BROWSER_COOKIE]
    # The pause is over for every browser, and only the phone is the account's now: the laptop
    # signed in with the old password.
    assert [row for row in counts(session) if row[0] == "failed_sign_in"] == []
    assert login(phone, password=NEW_PASSWORD) == {"mfa_required": False}
    (phone_row,) = session.scalars(select(Browser)).all()
    assert phone_row.id != laptop
    assert attempt(stranger).status_code == 401
    assert login(another_browser(client), password=NEW_PASSWORD) == {"mfa_required": False}


def test_authenticator_codes_count_as_failed_sign_ins(client, session, signed_in_only, monkeypatch):
    signup(client)
    secret = client.post("/api/auth/mfa/setup").json()["secret"]
    enabled = client.post("/api/auth/mfa/enable", json={"code": totp_code(secret, totp_step())})
    assert enabled.status_code == 200
    monkeypatch.setattr(get_settings(), "rate_sign_ins", 0)
    monkeypatch.setattr(get_settings(), "rate_failed_sign_ins", 2)
    # Someone with the password, on a browser of their own, guesses at the code.
    thief = another_browser(client)
    assert login(thief) == {"mfa_required": True}
    for code in ("000000", "000001"):
        assert thief.post("/api/auth/mfa/verify", json={"code": code}).status_code == 401
    right = totp_code(secret, totp_step() + 1)
    paused(thief.post("/api/auth/mfa/verify", json={"code": right}), PAUSED_NEW, 2)
    # The owner's browser, with the password and the code, is let through.
    assert login(client) == {"mfa_required": True}
    assert client.post("/api/auth/mfa/verify", json={"code": right}).status_code == 200


def test_a_signed_in_password_check_is_held_to_the_rates_per_user(
    client, session, signed_in_only, monkeypatch
):
    account = signup(client)
    monkeypatch.setattr(get_settings(), "rate_sign_ins", 2)
    wrong = {"current_password": "guess", "new_password": NEW_PASSWORD}
    for _ in range(2):
        assert client.post("/api/auth/password", json=wrong).status_code == 403
    refused(client.post("/api/auth/password", json=wrong), SIGN_INS.refusal(2, FROM_ACCOUNT))
    refused(
        client.post("/api/auth/mfa/disable", json={"password": "guess"}),
        SIGN_INS.refusal(2, FROM_ACCOUNT),
    )

    # A wrong password counts for the hour, whichever change it was to confirm.
    minute_turned(session, "sign_in")
    monkeypatch.setattr(get_settings(), "rate_sign_ins", 0)
    monkeypatch.setattr(get_settings(), "rate_failed_sign_ins", 3)
    deletion = client.post("/api/auth/account/deletion", json={"password": "guess"})
    assert deletion.status_code == 403 and deletion.json()["detail"] == WRONG_PASSWORD
    right = {"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    paused(client.post("/api/auth/password", json=right), PAUSED_SESSION, 3)
    paused(
        client.post("/api/auth/email", json={"email": "ada@new.example", "password": PASSWORD}),
        PAUSED_SESSION,
        3,
    )
    assert actions(session, account["organization"]["id"])[-1] == (
        "sign_in_paused",
        {"from": "session"},
    )
    # The sign-in routes keep their own count: the browser is not paused by it.
    assert client.post("/api/auth/logout").status_code == 204
    assert login(client) == {"mfa_required": False}


def test_resending_the_verification_link_is_held_per_user(
    client, session, mailer, signed_in_only, monkeypatch
):
    monkeypatch.setattr(get_settings(), "rate_sign_ins", 2)
    signup(client)
    # As an account through a provider that did not vouch for its address is.
    session.execute(update(User).values(email_verified_at=None))
    session.commit()
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


def test_the_hour_is_fixed_too():
    now = datetime(2026, 10, 13, 12, 40, 15, tzinfo=UTC)
    start = window_start(now, HOUR)
    assert start == datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
    assert seconds_left(now, start, HOUR) == 19 * 60 + 45
    assert window_end(FAILED_SIGN_INS, now) == datetime(2026, 10, 13, 13, 0, tzinfo=UTC)
    # Half an hour east of UTC, the hour still turns on UTC's.
    india = datetime(2026, 10, 13, 18, 10, 15, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    assert window_start(india, HOUR) == start


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
