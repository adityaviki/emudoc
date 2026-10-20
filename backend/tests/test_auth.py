"""Sign-in: passwords, verification and reset links, authenticator codes, sessions, and sign-in
through Google and Microsoft."""

import base64
import logging
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from app.accounts import LOCAL_OWNER_EMAIL, create_account
from app.api.auth import (
    ADDRESS_TAKEN,
    CODE_INVALID,
    LINK_INVALID,
    LOCAL_ADDRESS,
    MFA_FIRST,
    SAME_ADDRESS,
    SIGN_IN_FAILED,
    SIGNED_UP_ALREADY,
    TYPE_EMAIL,
    UNKNOWN_ZONE,
    WRONG_PASSWORD,
)
from app.api.deps import CROSS_SITE, SAME_APP_HEADER, SIGN_IN
from app.auth import totp_code, totp_step
from app.config import Settings, get_settings
from app.legal import TERMS_VERSION
from app.mail import MailError
from app.models import User
from app.oidc import Handshake
from tests.conftest import APP_HEADERS
from tests.test_api import listed

PASSWORD = "correct horse battery"
NEW_PASSWORD = "staple of a new life"


def sign_up_link(client, email="ada@example.com") -> str:
    """Ask for an account with the address, and return the token of the link mailed to it."""
    asked = client.post("/api/auth/signup", json={"email": email})
    assert asked.status_code == 202, asked.text
    return client.app.state.mailer.token()


def finish_signup(
    client, token, password=PASSWORD, name="Ada", terms_version=TERMS_VERSION, time_zone=""
):
    """Make the account on the page the link opens, as its form sends it."""
    return client.post(
        "/api/auth/signup/finish",
        json={
            "token": token,
            "name": name,
            "password": password,
            "terms_version": terms_version,
            "time_zone": time_zone,
        },
    )


def signup(client, email="ada@example.com", password=PASSWORD, name="Ada", time_zone="") -> dict:
    """Sign up as a person does: ask for the link with the address, then make the account on
    the page it opens, with the box ticked for the current terms."""
    token = sign_up_link(client, email)
    response = finish_signup(client, token, password, name, time_zone=time_zone)
    assert response.status_code == 201, response.text
    return response.json()


def login(client, email="ada@example.com", password=PASSWORD) -> dict:
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


def me(client):
    return client.get("/api/auth/me")


def another_browser(client, **headers) -> TestClient:
    """A second client against the same app, with its own cookies."""
    return TestClient(client.app, headers={**APP_HEADERS, **headers})


# Accounts, passwords and sessions


def test_signup_mails_a_link_whose_page_makes_the_account(client, mailer, signed_in_only):
    assert me(client).status_code == 401
    asked = client.post("/api/auth/signup", json={"email": "Ada@Example.com"})
    assert asked.status_code == 202 and asked.content == b"null"
    # Nothing is made until the link is opened.
    assert client.post(
        "/api/auth/login", json={"email": "ada@example.com", "password": PASSWORD}
    ).json() == {"detail": SIGN_IN_FAILED}

    ((to, subject, body),) = mailer.sent
    assert to == "ada@example.com" and subject == "Finish creating your Emudoc account"
    assert body.startswith("Hi,\n") and "works for 24 hours" in body
    # The token rides in the fragment, which no request carries to a server or its log.
    assert mailer.link().startswith("http://testserver/finish-signup#token=")
    token = mailer.token()

    # The page shows the address first, as often as it is opened, and the link is opened in
    # whichever browser holds the mail.
    other = another_browser(client)
    for _ in range(2):
        shown = other.post("/api/auth/signup/link", json={"token": token})
        assert shown.status_code == 200 and shown.json() == {"email": "ada@example.com"}
    made = finish_signup(other, token, time_zone="Europe/Berlin")
    assert made.status_code == 201, made.text
    account = made.json()
    assert account["email"] == "ada@example.com" and account["name"] == "Ada"
    assert account["time_zone"] == "Europe/Berlin"
    # Opening the link proved the address, so there is nothing more to confirm.
    assert account["email_verified"] is True and account["mfa_enabled"] is False
    assert account["identities"] == [] and account["role"] == "owner"
    assert account["organization"]["name"] == "Ada's workspace"
    assert account["organization"]["slug"] == "ada"
    assert me(other).json() == account and me(client).status_code == 401
    assert len(mailer.sent) == 1
    # The new organisation is empty, and its owner may fill it.
    assert other.get("/api/projects").json() == []
    assert other.post("/api/projects", json={"name": "Audit"}).status_code == 201
    login(client)

    for used in (
        other.post("/api/auth/signup/link", json={"token": token}),
        finish_signup(other, token),
    ):
        assert used.status_code == 400 and used.json()["detail"] == LINK_INVALID
    assert client.post("/api/auth/signup", json={"email": "not-an-email"}).status_code == 422
    # A refusal says where and why, never what was sent: a password that is too short, or a
    # link's token, would otherwise come back in the body.
    short = finish_signup(client, "a-sign-up-token", password="s3cret!")
    assert short.json()["detail"] == [
        {
            "type": "string_too_short",
            "loc": ["body", "password"],
            "msg": "String should have at least 8 characters",
        }
    ]
    assert "a-sign-up-token" not in short.text
    unfinished = client.post("/api/auth/password/reset", json={"token": "a-reset-token"})
    assert unfinished.status_code == 422 and "a-reset-token" not in unfinished.text
    # Names make the organisation; a second Ada gets the next slug.
    second = signup(another_browser(client), "ada@elsewhere.org", name="Ada")
    assert second["organization"]["slug"] == "ada-2"


def test_signup_is_answered_alike_for_an_address_with_an_account(client, mailer, signed_in_only):
    signup(client)
    stranger = another_browser(client)
    free = stranger.post("/api/auth/signup", json={"email": "eve@example.com"})
    taken = stranger.post("/api/auth/signup", json={"email": "ADA@example.com"})
    assert taken.status_code == free.status_code == 202 and taken.content == free.content
    # The address that has an account is told so instead, with the ways back into it.
    to, subject, body = mailer.sent[-1]
    assert to == "ada@example.com" and subject == "You have an Emudoc account already"
    assert "Hi Ada," in body and "nothing changed" in body
    assert mailer.link() == "http://testserver/login"
    assert "http://testserver/forgot-password" in body and "#token=" not in body
    login(another_browser(client))

    # A relay that is down answers the same: the mail goes out after the reply, and its failure
    # is the log's. No account is left behind waiting for a link that never came.
    def relay_down(to: str, subject: str, body: str) -> None:
        raise MailError("The email could not be sent.")

    sent = len(mailer.sent)
    mailer.send = relay_down
    down = stranger.post("/api/auth/signup", json={"email": "grace@example.com"})
    assert down.status_code == 202 and down.content == free.content
    del mailer.send
    assert len(mailer.sent) == sent

    # Asking again sends a new link and voids the earlier one.
    first = sign_up_link(stranger, "grace@example.com")
    second = sign_up_link(stranger, "grace@example.com")
    assert finish_signup(stranger, first).json()["detail"] == LINK_INVALID

    # An account made with the address since the link was sent, here by moving one to it, is
    # refused at the page and at the form, and the link makes nothing.
    assert change_email(client, "grace@example.com", password=PASSWORD).status_code == 202
    assert confirm_email(client, mailer.token()).status_code == 204
    for refused in (
        stranger.post("/api/auth/signup/link", json={"token": second}),
        finish_signup(stranger, second),
    ):
        assert refused.status_code == 409 and refused.json()["detail"] == SIGNED_UP_ALREADY
    assert me(stranger).status_code == 401


def test_an_account_made_with_the_address_at_the_same_moment_is_the_databases_to_see(
    client, session, monkeypatch, signed_in_only
):
    token = sign_up_link(client)

    def made_meanwhile(*args, **kwargs):
        # Another sign-up, through a provider say, commits between the check and the insert.
        session.add(User(email="ada@example.com", name="Ada"))
        session.commit()
        return create_account(*args, **kwargs)

    monkeypatch.setattr("app.api.auth.create_account", made_meanwhile)
    raced = finish_signup(client, token)
    assert raced.status_code == 409 and raced.json()["detail"] == SIGNED_UP_ALREADY
    assert me(client).status_code == 401


def test_resending_the_verification_link_voids_the_earlier_one(
    client, session, mailer, signed_in_only
):
    # An account through a provider that did not vouch for its address, or one whose address
    # was confirmed before a restore said otherwise.
    signup(client)
    session.execute(update(User).values(email_verified_at=None))
    session.commit()
    assert client.post("/api/auth/verify/resend").status_code == 202
    first = mailer.token()
    assert client.post("/api/auth/verify/resend").status_code == 202
    second = mailer.token()
    assert first != second and mailer.sent[-1][1] == "Confirm your email address"
    assert client.post("/api/auth/verify", json={"token": first}).status_code == 400
    assert client.post("/api/auth/verify", json={"token": second}).status_code == 204
    assert me(client).json()["email_verified"] is True
    # Once verified there is nothing to resend.
    assert client.post("/api/auth/verify/resend").status_code == 202
    assert len(mailer.sent) == 3


def test_login_and_logout(client, signed_in_only):
    signup(client)
    assert client.post("/api/auth/logout").status_code == 204
    assert me(client).status_code == 401

    # An unknown address and a wrong password get the same answer.
    unknown = client.post(
        "/api/auth/login", json={"email": "nobody@example.com", "password": "whatever"}
    )
    wrong = client.post("/api/auth/login", json={"email": "ada@example.com", "password": "wrong"})
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json() == {"detail": SIGN_IN_FAILED}

    assert login(client, "ADA@example.com") == {"mfa_required": False}
    assert me(client).json()["email"] == "ada@example.com"
    cookie = client.cookies["emudoc_session"]
    assert client.post("/api/auth/logout").status_code == 204
    assert me(client).status_code == 401
    # The old cookie is dead, not just forgotten by the browser.
    client.cookies.set("emudoc_session", cookie)
    assert me(client).status_code == 401
    assert me(client).json()["detail"] == SIGN_IN


def test_cookies_are_secure_in_production_whatever_the_scheme(
    client, signed_in_only, oauth, monkeypatch, caplog
):
    # The test client reaches the app as plain HTTP, as uvicorn sees a request from a proxy
    # that ends TLS but does not say so. In development that is a cookie without Secure, so
    # that http://localhost keeps working.
    response = finish_signup(client, sign_up_link(client))
    assert response.status_code == 201, response.text
    assert "secure" not in response.headers["set-cookie"].lower()

    # A deployment is behind TLS whatever the request says, so production marks every cookie
    # and the log says what the proxy and uvicorn should have been told.
    monkeypatch.setattr(get_settings(), "environment", "production")
    with caplog.at_level(logging.WARNING, logger="app.auth"):
        response = client.post(
            "/api/auth/login", json={"email": "ada@example.com", "password": PASSWORD}
        )
        assert response.status_code == 200, response.text
        assert "; secure" in response.headers["set-cookie"].lower()
        response = client.get("/api/auth/oauth/google/start", follow_redirects=False)
        assert response.status_code == 302, response.text
        assert "; secure" in response.headers["set-cookie"].lower()
    assert caplog.text.count("FORWARDED_ALLOW_IPS") == 2
    assert "POST /api/auth/login reached the app as plain HTTP" in caplog.text


def test_sign_in_is_required_unless_the_local_account_is_switched_on():
    # The suite switches it on through the environment; a deployment that sets nothing refuses
    # requests without a session.
    assert Settings.model_fields["local_account"].default is False
    assert get_settings().local_account is True


def test_without_a_session_only_the_local_account_fallback_lets_a_request_through(
    client, monkeypatch
):
    # Development: requests without a session act as the local owner.
    local = me(client).json()
    assert local["email"] == LOCAL_OWNER_EMAIL and local["organization"]["slug"] == "local"
    assert client.get("/api/projects").status_code == 200

    monkeypatch.setattr(get_settings(), "local_account", False)
    refused = client.get("/api/projects")
    assert refused.status_code == 401 and refused.json()["detail"] == SIGN_IN
    assert client.get("/api/status").status_code == 401

    # A signed-in user is themselves, whatever the setting says.
    monkeypatch.setattr(get_settings(), "local_account", True)
    signup(client)
    assert me(client).json()["email"] == "ada@example.com"


def test_a_reset_link_sets_a_new_password_and_signs_out_everywhere(client, mailer, signed_in_only):
    signup(client)
    other = another_browser(client)
    login(other)

    # Asking for an unknown address is accepted without saying so, and sends nothing.
    assert (
        client.post("/api/auth/password/forgot", json={"email": "nobody@example.com"}).status_code
        == 202
    )
    assert len(mailer.sent) == 1

    # A relay that is down answers the same as an address without an account: the mail goes
    # out after the reply, and its failure is the log's.
    def relay_down(to: str, subject: str, body: str) -> None:
        raise MailError("The email could not be sent.")

    sent = len(mailer.sent)
    mailer.send = relay_down
    down = client.post("/api/auth/password/forgot", json={"email": "ada@example.com"})
    assert down.status_code == 202
    del mailer.send
    assert len(mailer.sent) == sent
    assert (
        client.post("/api/auth/password/forgot", json={"email": "Ada@example.com"}).status_code
        == 202
    )
    to, subject, _ = mailer.sent[-1]
    assert to == "ada@example.com" and subject == "Reset your password"

    assert mailer.link().startswith("http://testserver/reset-password#token=")
    token = mailer.token()

    weak = client.post("/api/auth/password/reset", json={"token": token, "password": "short"})
    assert weak.status_code == 422
    reset = client.post("/api/auth/password/reset", json={"token": token, "password": NEW_PASSWORD})
    assert reset.status_code == 204
    assert me(client).status_code == 401 and me(other).status_code == 401
    assert (
        client.post(
            "/api/auth/password/reset", json={"token": token, "password": NEW_PASSWORD}
        ).status_code
        == 400
    )

    assert (
        client.post(
            "/api/auth/login", json={"email": "ada@example.com", "password": PASSWORD}
        ).status_code
        == 401
    )
    login(client, password=NEW_PASSWORD)
    # Opening the link proved the address.
    assert me(client).json()["email_verified"] is True


def test_changing_the_password_keeps_only_the_current_session(client, signed_in_only):
    signup(client)
    other = another_browser(client)
    login(other)

    wrong = client.post(
        "/api/auth/password", json={"current_password": "wrong", "new_password": NEW_PASSWORD}
    )
    assert wrong.status_code == 403
    changed = client.post(
        "/api/auth/password", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    )
    assert changed.status_code == 204
    assert me(client).status_code == 200
    assert me(other).status_code == 401
    login(other, password=NEW_PASSWORD)


# The profile and the address


def change_email(client, email: str, **confirmation):
    return client.post("/api/auth/email", json={"email": email, **confirmation})


def confirm_email(client, token: str):
    return client.post("/api/auth/email/confirm", json={"token": token})


def test_the_name_and_time_zone_change_and_the_zone_starts_as_the_browsers(client, signed_in_only):
    ada = signup(client, time_zone="Europe/Berlin")
    assert ada["time_zone"] == "Europe/Berlin"
    # A zone the app does not know is left out at sign-up rather than refused, and a browser
    # that says none leaves it empty, which is UTC.
    assert (
        signup(another_browser(client), "bob@example.com", time_zone="Mars/Olympus")["time_zone"]
        == ""
    )
    assert signup(another_browser(client), "eve@example.com")["time_zone"] == ""

    renamed = client.patch("/api/auth/profile", json={"name": "  Ada Lovelace "})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Ada Lovelace"
    assert renamed.json()["time_zone"] == "Europe/Berlin"
    # Chrome still reports India's zone by its old name, which the image's own database lacks.
    moved = client.patch("/api/auth/profile", json={"time_zone": "Asia/Calcutta"})
    assert moved.json()["time_zone"] == "Asia/Calcutta"
    assert moved.json()["name"] == "Ada Lovelace"
    unknown = client.patch("/api/auth/profile", json={"time_zone": "Mars/Olympus"})
    assert unknown.status_code == 400 and unknown.json()["detail"] == UNKNOWN_ZONE
    assert me(client).json()["time_zone"] == "Asia/Calcutta"
    assert client.patch("/api/auth/profile", json={"name": "x" * 201}).status_code == 422


def test_an_account_moves_to_a_new_address_through_a_link_sent_there(
    client, session, mailer, signed_in_only
):
    signup(client)
    elsewhere = another_browser(client)
    login(elsewhere)
    session.execute(update(User).values(email_verified_at=None))
    session.commit()
    assert client.post("/api/auth/verify/resend").status_code == 202
    unconfirmed = mailer.token()
    forgot = client.post("/api/auth/password/forgot", json={"email": "ada@example.com"})
    assert forgot.status_code == 202
    reset = mailer.token()

    for confirmation in ({}, {"password": "not it"}, {"current_email": "ada@example.com"}):
        refused = change_email(client, "ada@analytical.org", **confirmation)
        assert refused.status_code == 403 and refused.json()["detail"] == WRONG_PASSWORD
    same = change_email(client, "Ada@Example.com", password=PASSWORD)
    assert same.status_code == 400 and same.json()["detail"] == SAME_ADDRESS
    assert change_email(client, "not-an-email", password=PASSWORD).status_code == 422

    sent = len(mailer.sent)
    asked = change_email(client, "Ada@Analytical.org", password=PASSWORD)
    assert asked.status_code == 202
    (to, subject, body), *_ = mailer.sent[sent:]
    assert to == "ada@analytical.org" and subject == "Confirm your new email address"
    assert "Hi Ada," in body and "keeps its old address" in body
    assert mailer.link().startswith("http://testserver/confirm-email#token=")
    token = mailer.token()
    # Until the link is opened, nothing changes.
    assert me(client).json()["email"] == "ada@example.com"

    # The link works in a browser that is not signed in, once.
    stranger = another_browser(client)
    assert confirm_email(stranger, token).status_code == 204
    again = confirm_email(stranger, token)
    assert again.status_code == 400 and again.json()["detail"] == LINK_INVALID
    account = me(client).json()
    assert account["email"] == "ada@analytical.org" and account["email_verified"] is True
    # The person who changed it, and every other browser, stay signed in.
    assert me(elsewhere).status_code == 200

    # The old address is told, and is no longer anything to the account: its links are void
    # and it signs in nowhere.
    to, subject, body = mailer.sent[-1]
    assert to == "ada@example.com" and subject == "Your email address was changed"
    assert "analytical" not in body and "write to the people who run" in body
    for path, token_ in (("/api/auth/verify", unconfirmed), ("/api/auth/password/reset", reset)):
        void = stranger.post(path, json={"token": token_, "password": NEW_PASSWORD})
        assert void.status_code == 400 and void.json()["detail"] == LINK_INVALID
    refused = stranger.post(
        "/api/auth/login", json={"email": "ada@example.com", "password": PASSWORD}
    )
    assert refused.status_code == 401
    login(stranger, "ada@analytical.org")

    # The address it left is free for an account of its own.
    signup(another_browser(client), "ada@example.com")


def test_a_taken_address_is_answered_alike_and_told_instead(
    client, mailer, monkeypatch, signed_in_only
):
    monkeypatch.setattr(get_settings(), "operator_email", "privacy@emudoc.example")
    signup(another_browser(client), "bob@example.com", name="Bob")
    signup(client)

    free = change_email(client, "ada@analytical.org", password=PASSWORD)
    taken = change_email(client, "Bob@Example.com", password=PASSWORD)
    assert taken.status_code == free.status_code == 202 and taken.content == free.content
    to, subject, body = mailer.sent[-1]
    assert to == "bob@example.com" and subject == "This address has an Emudoc account already"
    assert "Hi Bob," in body and "nothing changed" in body
    assert mailer.link() == "http://testserver/login"
    assert me(client).json()["email"] == "ada@example.com"

    # An address that has become an account's since its link was sent is refused at the link,
    # which the free one's was.
    token = mailer.token(-2)
    signup(another_browser(client), "ada@analytical.org")
    late = confirm_email(client, token)
    assert late.status_code == 409 and late.json()["detail"] == ADDRESS_TAKEN
    assert me(client).json()["email"] == "ada@example.com"

    # A second request voids the first's link.
    change_email(client, "ada@one.org", password=PASSWORD)
    first = mailer.token()
    change_email(client, "ada@two.org", password=PASSWORD)
    assert confirm_email(client, first).status_code == 400
    assert confirm_email(client, mailer.token()).status_code == 204
    assert me(client).json()["email"] == "ada@two.org"
    # With an operator's address set, the old address is told to write there.
    assert "write to privacy@emudoc.example at once" in mailer.sent[-1][2]


def test_an_address_is_asked_for_at_a_rate(client, mailer, monkeypatch, signed_in_only):
    monkeypatch.setattr(get_settings(), "rate_sign_ins", 1)
    signup(client)
    assert change_email(client, "ada@analytical.org", password=PASSWORD).status_code == 202
    # A password is not guessed through the change either.
    again = change_email(client, "ada@other.org", password="guess")
    assert again.status_code == 429 and "from your account" in again.json()["detail"]
    # Another account may not flood the same address.
    bob = another_browser(client)
    signup(bob, "bob@example.com")
    flood = change_email(bob, "Ada@Analytical.org", password=PASSWORD)
    assert flood.status_code == 429 and "for this email address" in flood.json()["detail"]
    assert len(mailer.sent) == 3


def test_an_account_without_a_password_confirms_with_its_address_typed_out(
    client, session, mailer, signed_in_only
):
    signup(client)
    session.execute(update(User).values(password_hash=None))
    session.commit()
    for confirmation in ({}, {"password": PASSWORD}, {"current_email": "ada@analytical.org"}):
        refused = change_email(client, "ada@analytical.org", **confirmation)
        assert refused.status_code == 403 and refused.json()["detail"] == TYPE_EMAIL
    asked = change_email(client, "ada@analytical.org", current_email=" Ada@Example.com ")
    assert asked.status_code == 202
    assert confirm_email(client, mailer.token()).status_code == 204
    assert me(client).json()["email"] == "ada@analytical.org"


def test_the_local_account_keeps_its_address_but_may_be_renamed(client):
    refused = change_email(client, "owner@example.com", password=PASSWORD)
    assert refused.status_code == 409 and refused.json()["detail"] == LOCAL_ADDRESS
    assert client.patch("/api/auth/profile", json={"name": "Me"}).json()["name"] == "Me"
    assert me(client).json()["email"] == LOCAL_OWNER_EMAIL


def test_the_profile_routes_need_a_session(client, signed_in_only):
    assert client.patch("/api/auth/profile", json={"name": "Me"}).status_code == 401
    assert change_email(client, "ada@example.com", password=PASSWORD).status_code == 401


def test_sessions_are_listed_and_revoked(client, signed_in_only):
    signup(client)
    phone = another_browser(client, **{"user-agent": "Phone"})
    login(phone)

    sessions = client.get("/api/auth/sessions").json()
    assert [s["current"] for s in sessions] == [False, True]
    assert sessions[0]["user_agent"] == "Phone" and sessions[0]["ip"] == "testclient"
    assert client.delete(f"/api/auth/sessions/{sessions[0]['id']}").status_code == 204
    assert me(phone).status_code == 401
    assert [s["current"] for s in client.get("/api/auth/sessions").json()] == [True]
    assert client.delete("/api/auth/sessions/999999").status_code == 404

    # Sign out everywhere else.
    login(phone)
    login(another_browser(client))
    assert len(client.get("/api/auth/sessions").json()) == 3
    assert client.delete("/api/auth/sessions").status_code == 204
    assert me(phone).status_code == 401
    (current,) = client.get("/api/auth/sessions").json()
    assert current["current"] is True

    # Revoking the current session signs out.
    assert client.delete(f"/api/auth/sessions/{current['id']}").status_code == 204
    assert me(client).status_code == 401


def test_other_users_sessions_are_out_of_reach(client, signed_in_only):
    signup(client)
    (own,) = client.get("/api/auth/sessions").json()
    other = another_browser(client)
    signup(other, "bob@example.com", name="Bob")
    assert other.delete(f"/api/auth/sessions/{own['id']}").status_code == 404
    assert me(client).status_code == 200


# Requests from other sites


def test_a_change_needs_the_header_the_app_sends(client, signed_in_only):
    """The browser attaches the session cookie to a request from any site; only the app's own
    requests carry the header, so a change without it is refused and reading stays open."""
    signup(client)
    project = client.post("/api/projects", json={"name": "Audit"}).json()
    # The same browser session, driven by a page on another site: the cookie, not the header.
    elsewhere = TestClient(client.app, cookies=client.cookies)
    assert elsewhere.get("/api/projects").status_code == 200
    files = [("files", ("note.txt", b"A note."))]
    for refused in (
        elsewhere.post("/api/projects", json={"name": "Theirs"}),
        elsewhere.delete(f"/api/projects/{project['id']}"),
        elsewhere.post(f"/api/projects/{project['id']}/documents", files=files),
        elsewhere.post("/api/auth/logout"),
        elsewhere.post("/api/auth/login", json={"email": "ada@example.com", "password": PASSWORD}),
    ):
        assert refused.status_code == 403, refused.text
        assert refused.json()["detail"] == CROSS_SITE
    assert [p["name"] for p in client.get("/api/projects").json()] == ["Audit"]
    assert listed(client, project["id"]) == []
    assert me(elsewhere).status_code == 200
    # The header is the proof, whatever its value.
    elsewhere.headers[SAME_APP_HEADER] = "XMLHttpRequest"
    uploaded = elsewhere.post(f"/api/projects/{project['id']}/documents", files=files)
    assert uploaded.status_code == 201, uploaded.text
    assert [d["filename"] for d in uploaded.json()["documents"]] == ["note.txt"]


def test_the_local_account_is_shielded_from_other_sites_too(client):
    elsewhere = TestClient(client.app)
    assert elsewhere.get("/api/projects").status_code == 200
    refused = elsewhere.post("/api/projects", json={"name": "Theirs"})
    assert refused.status_code == 403 and refused.json()["detail"] == CROSS_SITE


# Two-factor sign-in


def test_totp_codes_match_the_reference_vectors():
    # RFC 4226, appendix D, with the 20-byte ASCII secret "12345678901234567890".
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert [totp_code(secret, step) for step in range(4)] == [
        "755224",
        "287082",
        "359152",
        "969429",
    ]


def test_two_factor_sign_in_with_an_authenticator_and_recovery_codes(
    client, signed_in_only, monkeypatch
):
    # This signs the one account in eleven times within a second, more than RATE_SIGN_INS
    # lets an account try in a minute (tests/test_ratelimit.py).
    monkeypatch.setattr(get_settings(), "rate_sign_ins", 100)
    signup(client)
    laptop = another_browser(client)
    login(laptop)
    not_started = client.post("/api/auth/mfa/enable", json={"code": "000000"})
    assert not_started.status_code == 400
    assert client.post("/api/auth/mfa/disable", json={"password": PASSWORD}).status_code == 409

    setup = client.post("/api/auth/mfa/setup").json()
    secret = setup["secret"]
    assert setup["otpauth_url"].startswith(
        f"otpauth://totp/Emudoc%3Aada%40example.com?secret={secret}"
    )
    stale = totp_code(secret, totp_step() - 5)
    assert client.post("/api/auth/mfa/enable", json={"code": stale}).status_code == 400
    code = totp_code(secret, totp_step())
    enabled = client.post("/api/auth/mfa/enable", json={"code": code}).json()
    recovery = enabled["recovery_codes"]
    assert len(recovery) == 8 and len(set(recovery)) == 8
    assert me(client).json()["mfa_enabled"] is True
    assert client.post("/api/auth/mfa/setup").status_code == 409
    # Sessions opened with the password alone are closed; this one proved the second factor.
    assert me(laptop).status_code == 401
    assert me(client).status_code == 200

    # Signing in now takes two steps, and the first grants nothing yet.
    assert login(laptop) == {"mfa_required": True}
    assert me(laptop).status_code == 401
    assert laptop.get("/api/projects").status_code == 401
    assert len(client.get("/api/auth/sessions").json()) == 1
    # The code that turned two-factor on was used; the authenticator's next code works.
    replayed = laptop.post("/api/auth/mfa/verify", json={"code": code})
    assert replayed.status_code == 401 and replayed.json()["detail"] == CODE_INVALID
    following = totp_code(secret, totp_step() + 1)
    verified = laptop.post("/api/auth/mfa/verify", json={"code": following})
    assert verified.status_code == 200 and verified.json()["email"] == "ada@example.com"
    assert me(laptop).status_code == 200
    assert len(client.get("/api/auth/sessions").json()) == 2

    # A recovery code stands in for the authenticator, once.
    phone = another_browser(client)
    login(phone)
    assert phone.post("/api/auth/mfa/verify", json={"code": "nonsense"}).status_code == 401
    assert phone.post("/api/auth/mfa/verify", json={"code": recovery[0]}).status_code == 200
    tablet = another_browser(client)
    login(tablet)
    assert tablet.post("/api/auth/mfa/verify", json={"code": recovery[0]}).status_code == 401
    assert (
        tablet.post("/api/auth/mfa/verify", json={"code": recovery[1].upper()}).status_code == 200
    )
    # The code step is only for sessions that wait for it.
    first = client.post("/api/auth/mfa/verify", json={"code": recovery[2]})
    assert first.status_code == 401 and first.json()["detail"] == MFA_FIRST

    # Turning it off needs the password or a code.
    assert client.post("/api/auth/mfa/disable", json={}).status_code == 403
    assert client.post("/api/auth/mfa/disable", json={"password": "wrong"}).status_code == 403
    assert client.post("/api/auth/mfa/disable", json={"password": PASSWORD}).status_code == 204
    assert me(client).json()["mfa_enabled"] is False
    assert login(another_browser(client)) == {"mfa_required": False}


# Sign-in through Google and Microsoft


@pytest.fixture
def oauth(monkeypatch):
    """Both providers set up, as .env would."""
    settings = get_settings()
    monkeypatch.setattr(settings, "google_oauth_client_id", "google-client")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "google-secret")
    monkeypatch.setattr(settings, "microsoft_oauth_client_id", "microsoft-client")
    monkeypatch.setattr(settings, "microsoft_oauth_client_secret", "microsoft-secret")


def start(client, provider="google", **params) -> tuple[dict, Handshake]:
    """Begin a sign-in: the query the browser carries to the provider, and the cookie kept."""
    response = client.get(
        f"/api/auth/oauth/{provider}/start", params=params, follow_redirects=False
    )
    assert response.status_code == 302, response.text
    location = urlsplit(response.headers["location"])
    query = {key: value for key, (value,) in parse_qs(location.query).items()}
    query["_host"] = location.netloc
    query["_path"] = location.path
    return query, Handshake.decode(client.cookies.get("emudoc_oauth"))


def callback(client, provider="google", **params):
    """The browser comes back from the provider; not following the redirect shows where to."""
    return client.get(f"/api/auth/oauth/{provider}/callback", params=params, follow_redirects=False)


def google_claims(nonce: str, **overrides) -> dict:
    return {
        "iss": "https://accounts.google.com",
        "aud": "google-client",
        "exp": int(time.time()) + 300,
        "sub": "google-123",
        "email": "ada@example.com",
        "email_verified": True,
        "name": "Ada Lovelace",
        "nonce": nonce,
        **overrides,
    }


def microsoft_claims(nonce: str, **overrides) -> dict:
    return {
        "iss": "https://login.microsoftonline.com/72f988bf-86f1-41af-91ab-2d7cd011db47/v2.0",
        "aud": "microsoft-client",
        "exp": int(time.time()) + 300,
        "sub": "microsoft-123",
        "preferred_username": "ada@contoso.com",
        "name": "Ada",
        "nonce": nonce,
        **overrides,
    }


def error_of(response) -> str:
    assert response.status_code == 302
    location = urlsplit(response.headers["location"])
    assert location.path == "/login"
    return parse_qs(location.query)["error"][0]


def test_providers_are_offered_only_when_set_up(client, oauth, monkeypatch):
    assert client.get("/api/auth/options").json() == {
        "providers": [
            {"name": "google", "label": "Google"},
            {"name": "microsoft", "label": "Microsoft"},
        ]
    }
    monkeypatch.setattr(get_settings(), "microsoft_oauth_client_id", "")
    assert [p["name"] for p in client.get("/api/auth/options").json()["providers"]] == ["google"]
    response = client.get("/api/auth/oauth/microsoft/start", follow_redirects=False)
    assert response.status_code == 404
    assert client.get("/api/auth/oauth/github/start", follow_redirects=False).status_code == 404


def test_google_sign_in_creates_an_account_and_links_verified_ones(
    client, session, mailer, identity_provider, oauth, signed_in_only
):
    query, handshake = start(client, next="/projects/3", time_zone="America/New_York")
    assert query["_host"] == "accounts.google.com"
    assert query["client_id"] == "google-client"
    assert query["redirect_uri"] == "http://testserver/api/auth/oauth/google/callback"
    assert query["scope"] == "openid email profile"
    assert query["state"] == handshake.state and query["nonce"] == handshake.nonce
    assert query["code_challenge_method"] == "S256" and query["code_challenge"]
    assert handshake.next == "/projects/3" and handshake.time_zone == "America/New_York"
    # The zone stays with the browser, not in what the provider is sent.
    assert "time_zone" not in query

    # The answer must belong to this very handshake.
    identity_provider.claims = google_claims(handshake.nonce)
    assert error_of(callback(client, code="the-code", state="someone-elses")).startswith(
        "The sign-in took too long"
    )
    _, handshake = start(client, next="/projects/3")
    identity_provider.claims = google_claims("another-nonce")
    assert error_of(callback(client, code="the-code", state=handshake.state)).startswith(
        "The sign-in with Google could not be verified"
    )
    _, handshake = start(client, next="/projects/3", time_zone="America/New_York")
    identity_provider.claims = google_claims(handshake.nonce)
    arrived = callback(client, code="the-code", state=handshake.state)
    assert arrived.status_code == 302 and arrived.headers["location"] == "/projects/3"
    # The browser is the account's from now on, as after a sign-in with a password.
    assert client.cookies["emudoc_browser"]
    assert identity_provider.requests[-1]["code"] == "the-code"
    assert identity_provider.requests[-1]["code_verifier"] == handshake.verifier
    assert identity_provider.requests[-1]["grant_type"] == "authorization_code"
    account = me(client).json()
    assert account["email"] == "ada@example.com" and account["name"] == "Ada Lovelace"
    assert account["email_verified"] is True and account["identities"] == ["google"]
    assert account["organization"]["slug"] == "ada-lovelace"
    assert account["time_zone"] == "America/New_York"
    assert mailer.sent == []

    # The subject names the person: the account stays the same if the address changes.
    client.post("/api/auth/logout")
    _, handshake = start(client)
    identity_provider.claims = google_claims(handshake.nonce, email="ada@newjob.com")
    assert callback(client, code="the-code", state=handshake.state).headers["location"] == "/"
    assert me(client).json()["id"] == account["id"]
    assert me(client).json()["email"] == "ada@example.com"

    # A password account, whose address its sign-up link confirmed, gets Google added to it.
    bob = another_browser(client)
    bob_account = signup(bob, "bob@example.com", name="Bob")
    bob.post("/api/auth/logout")
    _, handshake = start(bob)
    identity_provider.claims = google_claims(
        handshake.nonce, sub="google-456", email="bob@example.com"
    )
    callback(bob, code="the-code", state=handshake.state)
    linked = me(bob).json()
    assert linked["id"] == bob_account["id"] and linked["identities"] == ["google"]
    login(bob, "bob@example.com")

    # An account that never verified its address is not linked: it may not be theirs. One made
    # before a sign-up confirmed it, or through a provider that did not vouch for it, is so.
    eve = another_browser(client)
    signup(eve, "eve@example.com", name="Eve")
    session.execute(
        update(User).where(User.email == "eve@example.com").values(email_verified_at=None)
    )
    session.commit()
    _, handshake = start(eve)
    identity_provider.claims = google_claims(
        handshake.nonce, sub="google-789", email="eve@example.com"
    )
    assert error_of(callback(eve, code="the-code", state=handshake.state)).startswith(
        "An account with this email already exists"
    )
    assert me(eve).json()["identities"] == []


def test_microsoft_addresses_count_as_verified_only_when_the_provider_says_so(
    client, mailer, identity_provider, oauth, signed_in_only
):
    query, handshake = start(client, "microsoft")
    assert query["_host"] == "login.microsoftonline.com"
    assert query["_path"] == "/common/oauth2/v2.0/authorize"
    identity_provider.claims = microsoft_claims(handshake.nonce)
    arrived = callback(client, "microsoft", code="the-code", state=handshake.state)
    assert arrived.headers["location"] == "/"
    account = me(client).json()
    assert account["email"] == "ada@contoso.com" and account["identities"] == ["microsoft"]
    assert account["email_verified"] is False
    to, subject, _ = mailer.sent[-1]
    assert to == "ada@contoso.com" and subject == "Confirm your email address"
    assert client.post("/api/auth/verify", json={"token": mailer.token()}).status_code == 204
    assert me(client).json()["email_verified"] is True

    # With the xms_edov claim the directory vouches for the address.
    bob = another_browser(client)
    _, handshake = start(bob, "microsoft")
    identity_provider.claims = microsoft_claims(
        handshake.nonce, sub="microsoft-456", email="bob@contoso.com", xms_edov=True
    )
    callback(bob, "microsoft", code="the-code", state=handshake.state)
    assert me(bob).json()["email"] == "bob@contoso.com"
    assert me(bob).json()["email_verified"] is True

    # Tokens from elsewhere, and provider refusals, are turned away.
    eve = another_browser(client)
    _, handshake = start(eve, "microsoft")
    identity_provider.claims = microsoft_claims(
        handshake.nonce, iss="https://login.microsoftonline.example/x/v2.0", sub="microsoft-789"
    )
    assert "could not be verified" in error_of(
        callback(eve, "microsoft", code="the-code", state=handshake.state)
    )
    _, handshake = start(eve, "microsoft")
    identity_provider.status = 400
    assert "did not accept the sign-in" in error_of(
        callback(eve, "microsoft", code="the-code", state=handshake.state)
    )
    identity_provider.status = 200
    assert error_of(callback(eve, "microsoft", error="access_denied")).endswith("was cancelled.")
    assert me(eve).status_code == 401


def test_oauth_sign_in_still_asks_for_the_authenticator_code(
    client, identity_provider, oauth, signed_in_only
):
    signup(client)
    setup = client.post("/api/auth/mfa/setup").json()
    client.post("/api/auth/mfa/enable", json={"code": totp_code(setup["secret"], totp_step())})

    _, handshake = start(client, next="/projects/1")
    identity_provider.claims = google_claims(handshake.nonce)
    arrived = callback(client, code="the-code", state=handshake.state)
    assert arrived.headers["location"] == "/login?mfa=1&next=%2Fprojects%2F1"
    assert me(client).status_code == 401
    following = totp_code(setup["secret"], totp_step() + 1)
    assert client.post("/api/auth/mfa/verify", json={"code": following}).status_code == 200
    assert me(client).json()["identities"] == ["google"]
