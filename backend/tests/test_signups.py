"""Who may make an account (app/signups.py): anyone while SIGNUP_OPEN, and while it is closed,
as for a private beta, only whoever an organisation invites or the operator sends a sign-up
link."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.maintenance import main
from app.signups import INVITED_FOR, SIGNUP_CLOSED
from tests.test_auth import another_browser, finish_signup, login, signup
from tests.test_operator import operator_on


@pytest.fixture
def closed(monkeypatch, signed_in_only):
    """Sign-up closed, as on a deployment in its private beta."""
    monkeypatch.setattr(get_settings(), "signup_open", False)


def test_the_sign_in_pages_are_told_whether_anyone_may_sign_up(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "support_email", "")
    monkeypatch.setattr(settings, "operator_email", "")
    options = client.get("/api/auth/options").json()
    assert options["signup_open"] is True
    # Where a visitor asks for an account: the booking page, else a message to support, else
    # (development alone) the sign-up page while it is open and the sign-in page while not.
    assert (options["book_url"], options["book_label"]) == ("/signup", "Request access")
    monkeypatch.setattr(settings, "signup_open", False)
    options = client.get("/api/auth/options").json()
    assert (options["book_url"], options["book_label"]) == ("/login", "Sign in")
    monkeypatch.setattr(settings, "support_email", "help@emudoc.test")
    options = client.get("/api/auth/options").json()
    assert (options["book_url"], options["book_label"]) == (
        "mailto:help@emudoc.test?subject=Access%20to%20Emudoc",
        "Request access",
    )
    monkeypatch.setattr(settings, "booking_url", "https://cal.com/emudoc/30min")
    monkeypatch.setattr(settings, "signup_open", False)
    options = client.get("/api/auth/options").json()
    assert options["signup_open"] is False
    assert (options["book_url"], options["book_label"]) == (
        "https://cal.com/emudoc/30min",
        "Book a demo",
    )


def test_a_closed_sign_up_refuses_every_address_alike(client, mailer, closed):
    for address in ("ada@example.com", "Ada@Example.com"):
        refused = client.post("/api/auth/signup", json={"email": address})
        assert (refused.status_code, refused.json()["detail"]) == (403, SIGNUP_CLOSED)
    assert mailer.sent == []


def test_a_closed_sign_up_still_answers_an_address_with_an_account_alike(
    client, mailer, monkeypatch, signed_in_only
):
    signup(client, "ada@example.com")
    client.post("/api/auth/logout")
    sent = len(mailer.sent)
    monkeypatch.setattr(get_settings(), "signup_open", False)
    # The same refusal as for an address without one, so that nobody learns who has an account.
    refused = client.post("/api/auth/signup", json={"email": "ada@example.com"})
    assert (refused.status_code, refused.json()["detail"]) == (403, SIGNUP_CLOSED)
    assert len(mailer.sent) == sent
    # The account signs in as before.
    login(client, "ada@example.com")


def test_a_link_sent_before_sign_up_closed_still_makes_its_account(
    client, mailer, monkeypatch, signed_in_only
):
    client.post("/api/auth/signup", json={"email": "ada@example.com"})
    token = mailer.token()
    monkeypatch.setattr(get_settings(), "signup_open", False)
    assert finish_signup(client, token).status_code == 201


def test_the_operator_sends_a_sign_up_link_that_works_while_sign_up_is_closed(
    client, session, mailer, monkeypatch, signed_in_only
):
    operator = operator_on(client.app, session)
    monkeypatch.setattr(get_settings(), "signup_open", False)
    before = datetime.now(UTC)
    sent = operator.post("/api/operator/sign-up-links", json={"email": "Grace@Example.com"})
    assert sent.status_code == 201, sent.text
    assert sent.json()["email"] == "grace@example.com"
    expires_at = datetime.fromisoformat(sent.json()["expires_at"])
    assert before + INVITED_FOR <= expires_at <= datetime.now(UTC) + INVITED_FOR

    to, subject, body = mailer.sent[-1]
    assert (to, subject) == ("grace@example.com", "Your invitation to Emudoc")
    assert "works for 14 days" in body
    assert mailer.link().startswith("http://testserver/finish-signup#token=")
    grace = another_browser(client)
    made = finish_signup(grace, mailer.token(), name="Grace")
    assert made.status_code == 201, made.text
    assert made.json()["role"] == "owner" and made.json()["organization"]["slug"] == "grace"

    # An address with an account needs no link, which the operator is told.
    again = operator.post("/api/operator/sign-up-links", json={"email": "grace@example.com"})
    assert again.status_code == 409
    assert again.json()["detail"] == (
        "grace@example.com has an Emudoc account already, so it needs no sign-up link."
    )
    malformed = operator.post("/api/operator/sign-up-links", json={"email": "grace"})
    assert malformed.status_code == 422


def test_a_second_link_stops_the_first(client, session, mailer, monkeypatch, signed_in_only):
    operator = operator_on(client.app, session)
    monkeypatch.setattr(get_settings(), "signup_open", False)
    operator.post("/api/operator/sign-up-links", json={"email": "grace@example.com"})
    token = mailer.token()
    # A second link to the address stops the first.
    operator.post("/api/operator/sign-up-links", json={"email": "grace@example.com"})
    assert finish_signup(another_browser(client), token).status_code == 400
    assert finish_signup(another_browser(client), mailer.token()).status_code == 201


def test_the_invite_command_sends_the_link(client, mailer, capsys, closed):
    main(["invite", "Grace@Example.com"])
    out = capsys.readouterr().out
    assert out.startswith("A sign-up link is on its way to grace@example.com. It works until ")
    assert "Sign-up is open" not in out
    # From the worker, the link leads to the frontend's development server, as APP_URL is unset.
    assert mailer.sent[-1][:2] == ("grace@example.com", "Your invitation to Emudoc")
    assert mailer.link().startswith("http://localhost:5173/finish-signup#token=")
    assert finish_signup(another_browser(client), mailer.token()).status_code == 201

    with pytest.raises(SystemExit, match="has an Emudoc account already"):
        main(["invite", "grace@example.com"])
    with pytest.raises(SystemExit, match="'grace' is not an email address"):
        main(["invite", "grace"])


def test_the_invite_command_says_when_sign_up_is_open_anyway(client, mailer, capsys):
    main(["invite", "grace@example.com"])
    assert "Sign-up is open (SIGNUP_OPEN)" in capsys.readouterr().out


@pytest.mark.parametrize("url", ["http://cal.com/emudoc", "javascript:alert(1)", "cal.com/x"])
def test_the_booking_page_is_a_web_page(url):
    with pytest.raises(ValidationError, match="BOOKING_URL is the https:// address"):
        Settings(database_url="postgresql://x", booking_url=url)
    assert Settings(
        database_url="postgresql://x", booking_url=" https://cal.com/x "
    ).booking_url == ("https://cal.com/x")
