"""The breached-password check: a new password that Pwned Passwords lists is refused at sign-up,
from a reset link and on the account page, the service is told the first five characters of
its SHA-1 alone, nothing names them in the log, and a service that does not answer lets the
password through."""

import hashlib
import logging

import httpx
import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.logs import bare_url
from app.models import User
from app.passwords import BREACHED, breached
from tests.conftest import PWNED_ADDRESS
from tests.test_auth import (
    NEW_PASSWORD,
    PASSWORD,
    another_browser,
    finish_signup,
    login,
    me,
    sign_up_link,
    signup,
)

BREACHED_ONE = "password1234"


@pytest.fixture
def checked(monkeypatch, pwned_passwords):
    """Point the check at the fake service, with one breached password."""
    monkeypatch.setattr(get_settings(), "pwned_passwords_url", PWNED_ADDRESS)
    pwned_passwords.breach(BREACHED_ONE)
    return pwned_passwords


def sha1(password: str) -> str:
    return hashlib.sha1(password.encode()).hexdigest().upper()


def test_sign_up_refuses_a_breached_password_and_tells_the_service_only_the_hashs_start(
    client, session, checked, signed_in_only
):
    token = sign_up_link(client)
    response = finish_signup(client, token, password=BREACHED_ONE)
    assert response.status_code == 422 and response.json() == {"detail": BREACHED}
    assert session.scalar(select(func.count()).select_from(User)) == 0

    (request,) = checked.requests
    assert request.method == "GET"
    assert str(request.url) == f"{PWNED_ADDRESS}/range/{sha1(BREACHED_ONE)[:5]}"
    assert request.headers["Add-Padding"] == "true"
    assert BREACHED_ONE not in str(request.url) and sha1(BREACHED_ONE)[5:] not in str(request)

    # The link was not used up by the refusal.
    assert finish_signup(client, token).status_code == 201
    assert me(client).status_code == 200


def test_a_reset_and_a_change_refuse_one_and_keep_the_password(
    client, mailer, checked, signed_in_only
):
    signup(client)
    forgot = client.post("/api/auth/password/forgot", json={"email": "ada@example.com"})
    assert forgot.status_code == 202
    token = mailer.token()
    refused = client.post(
        "/api/auth/password/reset", json={"token": token, "password": BREACHED_ONE}
    )
    assert refused.status_code == 422 and refused.json() == {"detail": BREACHED}
    # The link was not used up by the refusal.
    reset = client.post("/api/auth/password/reset", json={"token": token, "password": NEW_PASSWORD})
    assert reset.status_code == 204
    login(client, password=NEW_PASSWORD)

    change = {"current_password": NEW_PASSWORD, "new_password": BREACHED_ONE}
    refused = client.post("/api/auth/password", json=change)
    assert refused.status_code == 422 and refused.json() == {"detail": BREACHED}
    login(another_browser(client), password=NEW_PASSWORD)


def test_padding_and_other_hashes_of_the_range_are_no_breach(client, checked):
    http = client.app.state.http
    assert breached(http, PWNED_ADDRESS, BREACHED_ONE)
    assert not breached(http, PWNED_ADDRESS, PASSWORD)
    # The padding the service adds names suffixes with no count, which are no breach either.
    checked.breach(PASSWORD, count=0)
    assert not breached(http, PWNED_ADDRESS, PASSWORD)


def test_a_service_that_does_not_answer_lets_the_password_through(
    client, checked, signed_in_only, caplog
):
    checked.status = 503
    with caplog.at_level(logging.INFO):
        signup(client, password=BREACHED_ONE)
    assert "The breached-password check at https://pwned.test failed (it answered 503)" in (
        caplog.text
    )
    # Neither that line nor httpx's own names the start of the hash.
    assert sha1(BREACHED_ONE)[:5] not in caplog.text
    assert "GET https://pwned.test/range/… " in caplog.text


def test_a_service_out_of_reach_lets_it_through_and_its_address_is_logged_bare(caplog):
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    http = httpx.Client(transport=httpx.MockTransport(unreachable))
    with caplog.at_level(logging.WARNING, logger="app.passwords"):
        assert not breached(http, "https://user:secret@pwned.test/", BREACHED_ONE)
    assert "check at https://pwned.test failed (ConnectTimeout)" in caplog.text
    assert "secret" not in caplog.text and sha1(BREACHED_ONE)[:5] not in caplog.text


def test_an_empty_address_checks_nothing(client, signed_in_only, pwned_passwords):
    assert get_settings().pwned_passwords_url == ""
    signup(client, password=BREACHED_ONE)
    assert pwned_passwords.requests == []


def test_no_address_in_the_log_names_a_hashs_range():
    assert bare_url("https://api.pwnedpasswords.com/range/5BAA6") == (
        "https://api.pwnedpasswords.com/range/…"
    )
    # Only the five characters the check asks by: any other path is kept as it is.
    assert bare_url("https://example.com/range/5BAA61") == "https://example.com/range/5BAA61"
