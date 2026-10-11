"""Sign-in: passwords, verification and reset links, authenticator codes, sessions, and sign-in
through Google and Microsoft."""

import base64
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app.accounts import LOCAL_OWNER_EMAIL
from app.api.auth import CODE_INVALID, LINK_INVALID, MFA_FIRST, SIGN_IN_FAILED
from app.api.deps import CROSS_SITE, SAME_APP_HEADER, SIGN_IN
from app.auth import totp_code, totp_step
from app.config import Settings, get_settings
from app.oidc import Handshake
from tests.conftest import APP_HEADERS

PASSWORD = "correct horse battery"
NEW_PASSWORD = "staple of a new life"


def signup(client, email="ada@example.com", password=PASSWORD, name="Ada") -> dict:
    response = client.post(
        "/api/auth/signup", json={"email": email, "name": name, "password": password}
    )
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


def test_signup_signs_in_and_sends_a_verification_link(client, mailer, signed_in_only):
    assert me(client).status_code == 401
    account = signup(client)
    assert account["email"] == "ada@example.com" and account["name"] == "Ada"
    assert account["email_verified"] is False and account["mfa_enabled"] is False
    assert account["identities"] == [] and account["role"] == "owner"
    assert account["organization"]["name"] == "Ada's workspace"
    assert account["organization"]["slug"] == "ada"
    assert me(client).json() == account
    # The new organisation is empty, and its owner may fill it.
    assert client.get("/api/projects").json() == []
    assert client.post("/api/projects", json={"name": "Audit"}).status_code == 201

    ((to, subject, body),) = mailer.sent
    assert to == "ada@example.com" and subject == "Confirm your email address"
    assert "Hi Ada," in body
    assert mailer.link().startswith("http://testserver/verify?token=")
    # The link is opened in a browser that is not signed in.
    token = mailer.token()
    other = another_browser(client)
    assert other.post("/api/auth/verify", json={"token": token}).status_code == 204
    assert me(client).json()["email_verified"] is True
    again = other.post("/api/auth/verify", json={"token": token})
    assert again.status_code == 400 and again.json()["detail"] == LINK_INVALID

    # Addresses are one account each, however they are typed.
    taken = client.post("/api/auth/signup", json={"email": "Ada@Example.com", "password": PASSWORD})
    assert taken.status_code == 409
    for body_ in (
        {"email": "not-an-email", "password": PASSWORD},
        {"email": "b@x.io", "password": "short"},
    ):
        assert client.post("/api/auth/signup", json=body_).status_code == 422
    # Names make the organisation; a second Ada gets the next slug.
    second = signup(another_browser(client), "ada@elsewhere.org", name="Ada")
    assert second["organization"]["slug"] == "ada-2"


def test_resending_the_verification_link_voids_the_earlier_one(client, mailer, signed_in_only):
    signup(client)
    first = mailer.token()
    assert client.post("/api/auth/verify/resend").status_code == 202
    second = mailer.token()
    assert first != second
    assert client.post("/api/auth/verify", json={"token": first}).status_code == 400
    assert client.post("/api/auth/verify", json={"token": second}).status_code == 204
    # Once verified there is nothing to resend.
    assert client.post("/api/auth/verify/resend").status_code == 202
    assert len(mailer.sent) == 2


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
    assert (
        client.post("/api/auth/password/forgot", json={"email": "Ada@example.com"}).status_code
        == 202
    )
    to, subject, _ = mailer.sent[-1]
    assert to == "ada@example.com" and subject == "Reset your password"
    assert mailer.link().startswith("http://testserver/reset-password?token=")
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
    assert client.get(f"/api/projects/{project['id']}/documents").json() == []
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


def test_two_factor_sign_in_with_an_authenticator_and_recovery_codes(client, signed_in_only):
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
    client, mailer, identity_provider, oauth, signed_in_only
):
    query, handshake = start(client, next="/projects/3")
    assert query["_host"] == "accounts.google.com"
    assert query["client_id"] == "google-client"
    assert query["redirect_uri"] == "http://testserver/api/auth/oauth/google/callback"
    assert query["scope"] == "openid email profile"
    assert query["state"] == handshake.state and query["nonce"] == handshake.nonce
    assert query["code_challenge_method"] == "S256" and query["code_challenge"]
    assert handshake.next == "/projects/3"

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
    _, handshake = start(client, next="/projects/3")
    identity_provider.claims = google_claims(handshake.nonce)
    arrived = callback(client, code="the-code", state=handshake.state)
    assert arrived.status_code == 302 and arrived.headers["location"] == "/projects/3"
    assert identity_provider.requests[-1]["code"] == "the-code"
    assert identity_provider.requests[-1]["code_verifier"] == handshake.verifier
    assert identity_provider.requests[-1]["grant_type"] == "authorization_code"
    account = me(client).json()
    assert account["email"] == "ada@example.com" and account["name"] == "Ada Lovelace"
    assert account["email_verified"] is True and account["identities"] == ["google"]
    assert account["organization"]["slug"] == "ada-lovelace"
    assert mailer.sent == []

    # The subject names the person: the account stays the same if the address changes.
    client.post("/api/auth/logout")
    _, handshake = start(client)
    identity_provider.claims = google_claims(handshake.nonce, email="ada@newjob.com")
    assert callback(client, code="the-code", state=handshake.state).headers["location"] == "/"
    assert me(client).json()["id"] == account["id"]
    assert me(client).json()["email"] == "ada@example.com"

    # A password account whose address is verified gets Google added to it.
    bob = another_browser(client)
    bob_account = signup(bob, "bob@example.com", name="Bob")
    bob.post("/api/auth/verify", json={"token": mailer.token()})
    bob.post("/api/auth/logout")
    _, handshake = start(bob)
    identity_provider.claims = google_claims(
        handshake.nonce, sub="google-456", email="bob@example.com"
    )
    callback(bob, code="the-code", state=handshake.state)
    linked = me(bob).json()
    assert linked["id"] == bob_account["id"] and linked["identities"] == ["google"]
    login(bob, "bob@example.com")

    # An account that never verified its address is not linked: it may not be theirs.
    eve = another_browser(client)
    signup(eve, "eve@example.com", name="Eve")
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
    client, mailer, identity_provider, oauth, signed_in_only
):
    signup(client)
    client.post("/api/auth/verify", json={"token": mailer.token()})
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
