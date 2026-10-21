"""An organisation's security settings: requiring two-factor sign-in holds a member without it
in that organisation alone, until they set it up, switch, leave or sign out, an account without
a password too, and only someone who signs in with it turns it on; and a session that signed in
longer ago than the organisation it works in allows ends at its next request there. Owners and
admins change both, each change a row of the audit log, and they see who signs in with
two-factor."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.api.organizations import ADMINS_ONLY
from app.auth import totp_code, totp_step
from app.models import Organization, User, UserSession
from app.notices import tell
from app.security import (
    MFA_YOURS_FIRST,
    longer_than_sessions,
    mfa_pending,
    sign_in_again,
)
from tests.test_api import create_project
from tests.test_auth import PASSWORD, login, me, signup
from tests.test_notifications import APP, centre, news, titles
from tests.test_organization_settings import change, settings_changed
from tests.test_organizations import start, switch, team  # noqa: F401


def two_factor(client) -> str:
    """Turn two-factor sign-in on for the account signed in on `client`; returns the secret."""
    secret = client.post("/api/auth/mfa/setup").json()["secret"]
    enabled = client.post("/api/auth/mfa/enable", json={"code": totp_code(secret, totp_step())})
    assert enabled.status_code == 200, enabled.text
    return secret


def require(client, on: bool = True):
    return client.patch("/api/organization", json={"mfa_required": on})


def organization(session, org_id: int) -> Organization:
    session.expire_all()
    return session.get(Organization, org_id)


# Two-factor sign-in


def test_requiring_two_factor_holds_the_members_without_it_in_that_organisation_alone(
    client, session, request
):
    ada, bob, bob_client = request.getfixturevalue("team")
    acme = ada["organization"]["id"]
    workspace = bob["organization"]["id"]
    # Nobody requires what they do not do themselves: they would be the first held.
    refused = require(client)
    assert refused.status_code == 409 and refused.json()["detail"] == MFA_YOURS_FIRST
    assert organization(session, acme).mfa_required is False

    two_factor(client)
    assert change(client, mfa_required=True)["mfa_required"] is True
    assert settings_changed(session, acme) == [
        {"setting": "mfa_required", "from": False, "to": True}
    ]
    # The owners and admins see whom it holds; the other members are not told.
    members = client.get("/api/organization/members").json()
    assert {member["name"]: member["mfa_enabled"] for member in members} == {
        "Ada": True,
        "Bob": False,
    }

    assert switch(bob_client, acme)["mfa_due"] is True
    held = bob_client.get("/api/projects")
    assert held.status_code == 403
    assert held.json()["detail"] == mfa_pending(organization(session, acme))
    assert bob_client.get("/api/audit").status_code == 403
    # Acme's notices name what its projects hold, so they wait with it; his own come.
    for org_id, subject in ((acme, "From Acme"), (workspace, "From his workspace")):
        tell(
            session, session.get(User, bob["id"]), org_id, "upload_indexed", news(subject), "/", APP
        )
    session.commit()
    assert titles(centre(bob_client)) == ["From his workspace"]
    assert centre(bob_client)["unread"] == 1

    # His own organisation asks nothing of the kind, and Acme holds him again on the way back.
    assert switch(bob_client, workspace)["mfa_due"] is False
    assert bob_client.get("/api/projects").status_code == 200
    assert switch(bob_client, acme)["mfa_due"] is True
    assert bob_client.get("/api/projects").status_code == 403

    # Setting it up is open to him there, and lets him in.
    two_factor(bob_client)
    assert me(bob_client).json()["mfa_due"] is False
    assert bob_client.get("/api/projects").status_code == 200
    assert titles(centre(bob_client)) == ["From his workspace", "From Acme"]
    assert {
        member["mfa_enabled"] for member in bob_client.get("/api/organization/members").json()
    } == {None}
    # A member may not change it, held or not.
    refused = require(bob_client, False)
    assert refused.status_code == 403 and refused.json()["detail"] == ADMINS_ONLY

    # Turning one's own off is allowed, and holds the account again until it is set up anew.
    disabled = bob_client.post("/api/auth/mfa/disable", json={"password": PASSWORD})
    assert disabled.status_code == 204
    assert bob_client.get("/api/projects").status_code == 403

    assert change(client, mfa_required=False)["mfa_required"] is False
    assert bob_client.get("/api/projects").status_code == 200
    assert settings_changed(session, acme)[-1] == {
        "setting": "mfa_required",
        "from": True,
        "to": False,
    }


def test_a_member_held_may_leave_or_sign_out_instead(client, session, request):
    ada, bob, bob_client = request.getfixturevalue("team")
    two_factor(client)
    change(client, mfa_required=True)

    switch(bob_client, ada["organization"]["id"])
    assert bob_client.get("/api/projects").status_code == 403
    left = bob_client.post("/api/organization/leave")
    assert left.status_code == 200, left.text
    assert left.json()["organization"]["id"] == bob["organization"]["id"]
    assert left.json()["mfa_due"] is False
    assert bob_client.get("/api/projects").status_code == 200
    assert bob_client.post("/api/auth/logout").status_code == 204


def test_an_account_without_a_password_sets_up_two_factor_where_it_is_required(
    client, session, request
):
    ada, bob, bob_client = request.getfixturevalue("team")
    two_factor(client)
    change(client, mfa_required=True)
    # As an account that signs in through Google alone: its provider's second factor, if it
    # asks one, is not seen, so it sets up an authenticator too, with no password to give.
    session.execute(update(User).where(User.id == bob["id"]).values(password_hash=None))
    session.commit()

    assert switch(bob_client, ada["organization"]["id"])["mfa_due"] is True
    secret = two_factor(bob_client)
    assert bob_client.get("/api/projects").status_code == 200
    # Turning it off takes a code, then, and holds him again.
    code = totp_code(secret, totp_step() + 1)
    assert bob_client.post("/api/auth/mfa/disable", json={"code": code}).status_code == 204
    assert me(bob_client).json()["mfa_due"] is True


def test_the_local_account_is_never_held(client, session, account):
    session.execute(
        update(Organization)
        .where(Organization.id == account.organization.id)
        .values(mfa_required=True)
    )
    session.commit()
    assert me(client).json()["mfa_due"] is False
    assert client.get("/api/projects").status_code == 200


# Sessions


def backdate_sessions(session, user_id: int, days: float) -> None:
    """As if the user's sessions had signed in `days` ago."""
    session.execute(
        update(UserSession)
        .where(UserSession.user_id == user_id)
        .values(created_at=datetime.now(UTC) - timedelta(days=days))
    )
    session.commit()


def test_a_session_older_than_its_organisation_allows_ends_there(client, session, signed_in_only):
    ada = signup(client)
    acme = ada["organization"]["id"]
    create_project(client, "Contracts")
    assert change(client, session_days=1)["session_days"] == 1
    assert settings_changed(session, acme) == [{"setting": "session_days", "from": None, "to": 1}]

    backdate_sessions(session, ada["id"], 0.9)
    assert client.get("/api/projects").status_code == 200
    backdate_sessions(session, ada["id"], 1.1)
    ended = client.get("/api/projects")
    assert ended.status_code == 401
    assert ended.json()["detail"] == sign_in_again(organization(session, acme))
    assert sign_in_again(organization(session, acme)).startswith(
        "Ada's workspace asks its members to sign in again every day."
    )
    # It ended for good, not just there.
    assert me(client).status_code == 401
    session.expire_all()
    (revoked,) = session.scalars(
        select(UserSession.revoked_at).where(UserSession.user_id == ada["id"])
    )
    assert revoked is not None

    login(client)
    assert client.get("/api/projects").status_code == 200

    # A session that works in another organisation goes on, whatever this one allows, and
    # ends once it comes back.
    start(client, "Elsewhere")
    backdate_sessions(session, ada["id"], 3)
    assert client.get("/api/projects").status_code == 200
    switch(client, acme)
    assert client.get("/api/projects").status_code == 401


def test_a_session_lasts_no_longer_than_the_deployment_keeps_one(client, session, signed_in_only):
    ada = signup(client)
    refused = client.patch("/api/organization", json={"session_days": 31})
    assert refused.status_code == 400
    assert refused.json()["detail"] == longer_than_sessions(30)
    assert "every 30 days at the most" in longer_than_sessions(30)

    organization_out = change(client, session_days=7)
    assert (organization_out["session_days"], organization_out["max_session_days"]) == (7, 30)
    # Back to the deployment's.
    assert change(client, session_days=None)["session_days"] is None
    assert settings_changed(session, ada["organization"]["id"])[-1] == {
        "setting": "session_days",
        "from": 7,
        "to": None,
    }
    assert client.patch("/api/organization", json={"session_days": 0}).status_code == 422
