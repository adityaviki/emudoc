"""How people join an organisation: the invitations its owners and admins send, resend and
revoke, the page an invitation's link opens, signed in, signing up or through a provider, and
the domains whose confirmed addresses may join by themselves."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, update

from app.api.auth import (
    INVITATION_ELSEWHERE,
    INVITATION_EXPIRED,
    INVITATION_GONE,
    INVITATION_HAS_ACCOUNT,
    LOCAL_INVITATION,
)
from app.api.invitations import ADMINS_ONLY, INVITED_ALREADY, OWNERS_INVITE_OWNERS
from app.auth import CREDENTIAL_HISTORY, prune_credentials
from app.config import get_settings
from app.db import engine
from app.entitlements import PLANS
from app.legal import TERMS_VERSION
from app.models import Invitation, User
from tests.test_audit import actions
from tests.test_auth import (
    PASSWORD,
    another_browser,
    callback,
    google_claims,
    me,
    signup,
)
from tests.test_auth import start as begin_oauth
from tests.test_organizations import join
from tests.test_ratelimit import counted_afresh


@pytest.fixture(autouse=True)
def room(monkeypatch):
    """Room for ten members on the free plan, which allows one; `room(n)` for another cap."""

    def members(cap: int | None) -> None:
        monkeypatch.setitem(PLANS, "free", replace(PLANS["free"], members=cap))

    members(10)
    return members


@pytest.fixture
def ada(client, signed_in_only) -> dict:
    """Ada, signed in on `client`, the owner of the organisation she signed up with."""
    return signup(client, "ada@acme.com", name="Ada")


def invite(client, email: str, role: str = "member") -> dict:
    response = client.post("/api/organization/invitations", json={"email": email, "role": role})
    assert response.status_code == 201, response.text
    return response.json()


def invitations(client) -> list[dict]:
    response = client.get("/api/organization/invitations")
    assert response.status_code == 200, response.text
    return response.json()


def shown(client, token: str):
    """What the invitation's page shows, from its link."""
    return client.post("/api/auth/invitation", json={"token": token})


def accept(client, token: str):
    return client.post("/api/auth/invitation/accept", json={"token": token})


def sign_up_invited(client, token: str, name="Bob", password=PASSWORD, terms=TERMS_VERSION):
    return client.post(
        "/api/auth/invitation/signup",
        json={
            "token": token,
            "name": name,
            "password": password,
            "terms_version": terms,
            "time_zone": "Europe/Berlin",
        },
    )


def expire(session, email: str, ago: timedelta = timedelta(minutes=1)) -> None:
    session.execute(
        update(Invitation)
        .where(Invitation.email == email)
        .values(expires_at=datetime.now(UTC) - ago)
    )
    session.commit()


def joining(client, *domains: str, role: str = "member"):
    return client.put("/api/organization/joining", json={"domains": list(domains), "role": role})


# Invitations


def test_an_invitation_makes_a_new_account_a_member_and_nothing_more(client, session, mailer, ada):
    org_id = ada["organization"]["id"]
    sent = invite(client, "Bob@Example.com", "viewer")
    assert (sent["email"], sent["role"], sent["invited_by"], sent["expired"]) == (
        "bob@example.com",
        "viewer",
        "Ada",
        False,
    )
    to, subject, body = mailer.sent[-1]
    assert (to, subject) == ("bob@example.com", "You are invited to join an organisation on Emudoc")
    assert body.startswith("Hi,\n\nAda invited you to join Ada's workspace on Emudoc as a viewer")
    assert "works for 7 days" in body
    # The token rides in the fragment, which no request carries to a server or its log.
    assert mailer.link().startswith("http://testserver/invitation#token=")
    token = mailer.token()

    bob = another_browser(client)
    page = shown(bob, token).json()
    assert {key: page[key] for key in ("organization", "role", "email", "invited_by")} == {
        "organization": "Ada's workspace",
        "role": "viewer",
        "email": "bob@example.com",
        "invited_by": "Ada",
    }
    assert (page["expired"], page["has_account"]) == (False, False)

    # The terms are accepted as at any sign-up, and a refusal leaves the invitation open.
    refused = sign_up_invited(bob, token, terms=None)
    assert refused.status_code == 400
    made = sign_up_invited(bob, token)
    assert made.status_code == 201, made.text
    account = made.json()
    assert (account["organization"]["id"], account["role"]) == (org_id, "viewer")
    # No organisation of his own: the one that invited him is his only one.
    assert [o["name"] for o in account["organizations"]] == ["Ada's workspace"]
    assert account["email_verified"] is True and account["terms_due"] is False
    assert account["time_zone"] == "Europe/Berlin"
    assert me(bob).json()["organization"]["id"] == org_id

    # The invitation is gone, its link with it, and the members are two.
    assert invitations(client) == []
    assert shown(bob, token).json()["detail"] == INVITATION_GONE
    members = client.get("/api/organization/members").json()
    assert [(m["email"], m["role"]) for m in members] == [
        ("ada@acme.com", "owner"),
        ("bob@example.com", "viewer"),
    ]
    assert [(action, detail) for action, detail in actions(session, org_id)][-4:] == [
        ("invitation_sent", {"role": "viewer"}),
        ("sign_up", {"method": "password"}),
        ("terms_accepted", {"version": TERMS_VERSION}),
        ("invitation_accepted", {"role": "viewer"}),
    ]


def test_the_account_with_the_address_accepts_signed_in_and_works_there(
    client, session, mailer, ada
):
    org_id = ada["organization"]["id"]
    bob = another_browser(client)
    own = signup(bob, "bob@example.com", name="Bob")["organization"]["id"]
    # An account that signed up through a provider which did not vouch for its address.
    session.execute(
        update(User).where(User.email == "bob@example.com").values(email_verified_at=None)
    )
    session.commit()
    invite(client, "bob@example.com", "admin")
    token = mailer.token()
    assert shown(bob, token).json()["has_account"] is True
    assert sign_up_invited(another_browser(client), token).json()["detail"] == (
        INVITATION_HAS_ACCOUNT
    )

    joined = accept(bob, token)
    assert joined.status_code == 200, joined.text
    account = joined.json()
    assert (account["organization"]["id"], account["role"]) == (org_id, "admin")
    assert {o["id"] for o in account["organizations"]} == {org_id, own}
    # Opening the link proved the address.
    assert account["email_verified"] is True
    # The session works there from now on, in every tab.
    assert me(bob).json()["organization"]["id"] == org_id
    assert accept(bob, token).json()["detail"] == INVITATION_GONE
    assert actions(session, org_id)[-1] == ("invitation_accepted", {"role": "admin"})


def test_only_the_invited_address_accepts(client, session, mailer, ada, monkeypatch):
    invite(client, "carol@example.com")
    token = mailer.token()
    bob = another_browser(client)
    signup(bob, "bob@example.com")
    refused = accept(bob, token)
    assert refused.status_code == 403
    assert refused.json()["detail"] == INVITATION_ELSEWHERE.format(
        invited="carol@example.com", email="bob@example.com"
    )
    # Nobody signed in may accept for anyone, nor the local account, which has no session.
    assert accept(another_browser(client), token).status_code == 401
    assert len(invitations(client)) == 1
    monkeypatch.setattr(get_settings(), "local_account", True)
    local = another_browser(client)
    assert accept(local, token).json()["detail"] == LOCAL_INVITATION
    assert sign_up_invited(local, "not-a-token").json()["detail"] == INVITATION_GONE


def test_owners_and_admins_invite_to_their_own_role_or_below(client, session, mailer, ada):
    org_id = ada["organization"]["id"]
    bob, cat = another_browser(client), another_browser(client)
    join(session, org_id, signup(bob, "bob@example.com", name="Bob")["id"], "admin")
    join(session, org_id, signup(cat, "cat@example.com", name="Cat")["id"], "member")
    for browser in (bob, cat):
        browser.post(f"/api/organizations/{org_id}/switch")

    refused = bob.post(
        "/api/organization/invitations", json={"email": "x@example.com", "role": "owner"}
    )
    assert (refused.status_code, refused.json()["detail"]) == (403, OWNERS_INVITE_OWNERS)
    assert invite(bob, "dan@example.com", "admin")["invited_by"] == "Bob"
    owner = invite(client, "eve@example.com", "owner")
    # An admin may not send an owner's invitation again, but may revoke it.
    resent = bob.post(f"/api/organization/invitations/{owner['id']}/resend")
    assert resent.json()["detail"] == OWNERS_INVITE_OWNERS
    assert [i["email"] for i in invitations(bob)] == ["eve@example.com", "dan@example.com"]

    # A member does none of it.
    for response in (
        cat.get("/api/organization/invitations"),
        cat.post("/api/organization/invitations", json={"email": "f@example.com"}),
        cat.delete(f"/api/organization/invitations/{owner['id']}"),
    ):
        assert (response.status_code, response.json()["detail"]) == (403, ADMINS_ONLY)
    assert bob.delete(f"/api/organization/invitations/{owner['id']}").status_code == 204


def test_an_open_invitation_holds_a_place_among_the_plans_members(client, mailer, ada, room):
    room(1)
    refused = client.post("/api/organization/invitations", json={"email": "bob@example.com"})
    assert refused.status_code == 402
    assert refused.json()["detail"] == (
        "The Free plan allows 1 member, and this organisation has 1, open invitations counted. "
        "Upgrade your plan to invite more."
    )
    room(2)
    first = invite(client, "bob@example.com")
    assert (
        client.post("/api/organization/invitations", json={"email": "cat@example.com"}).status_code
        == 402
    )
    assert client.delete(f"/api/organization/invitations/{first['id']}").status_code == 204
    invite(client, "cat@example.com")


def test_an_address_is_invited_once_and_a_member_not_at_all(client, session, mailer, ada):
    org_id = ada["organization"]["id"]
    bob = another_browser(client)
    join(session, org_id, signup(bob, "bob@example.com", name="Bob")["id"])
    member = client.post("/api/organization/invitations", json={"email": "BOB@example.com"})
    assert (member.status_code, member.json()["detail"]) == (
        409,
        "Bob is a member of Ada's workspace already.",
    )
    first = invite(client, "cat@example.com")
    twice = client.post(
        "/api/organization/invitations", json={"email": "cat@example.com", "role": "viewer"}
    )
    assert (twice.status_code, twice.json()["detail"]) == (409, INVITED_ALREADY)

    # Once it expired, inviting the address again is a new invitation, with a new link.
    old = mailer.token()
    expire(session, "cat@example.com")
    again = invite(client, "cat@example.com", "viewer")
    assert (again["id"], again["role"], again["expired"]) == (first["id"], "viewer", False)
    assert shown(client, old).json()["detail"] == INVITATION_GONE
    assert shown(client, mailer.token()).json()["role"] == "viewer"


def test_sending_again_mails_a_new_link_and_stops_the_one_before(
    client, session, mailer, ada, room
):
    org_id = ada["organization"]["id"]
    sent = invite(client, "bob@example.com")
    first = mailer.token()
    resent = client.post(f"/api/organization/invitations/{sent['id']}/resend")
    assert resent.status_code == 200, resent.text
    second = mailer.token()
    assert second != first and len(mailer.sent) == 3
    assert shown(client, first).json()["detail"] == INVITATION_GONE
    assert shown(client, second).json()["email"] == "bob@example.com"
    assert actions(session, org_id)[-1] == ("invitation_sent", {"role": "member", "again": True})

    # An expired one comes back for a week, if the plan has room for it again.
    expire(session, "bob@example.com")
    assert invitations(client)[0]["expired"] is True
    room(1)
    full = client.post(f"/api/organization/invitations/{sent['id']}/resend")
    assert full.status_code == 402
    room(2)
    back = client.post(f"/api/organization/invitations/{sent['id']}/resend").json()
    assert back["expired"] is False
    assert datetime.fromisoformat(back["expires_at"]) > datetime.now(UTC) + timedelta(days=6)
    assert client.post("/api/organization/invitations/999999/resend").status_code == 404


def test_a_revoked_invitation_is_gone_with_its_link(client, session, mailer, ada):
    org_id = ada["organization"]["id"]
    sent = invite(client, "bob@example.com", "viewer")
    token = mailer.token()
    assert client.delete(f"/api/organization/invitations/{sent['id']}").status_code == 204
    assert invitations(client) == []
    bob = another_browser(client)
    assert shown(bob, token).json()["detail"] == INVITATION_GONE
    assert sign_up_invited(bob, token).json()["detail"] == INVITATION_GONE
    assert actions(session, org_id)[-1] == ("invitation_revoked", {"role": "viewer"})
    assert client.delete(f"/api/organization/invitations/{sent['id']}").status_code == 404


def test_an_expired_invitation_says_whom_to_ask_and_goes_in_time(client, session, mailer, ada):
    invite(client, "bob@example.com")
    token = mailer.token()
    expire(session, "bob@example.com")
    bob = another_browser(client)
    page = shown(bob, token).json()
    assert (page["expired"], page["invited_by"]) == (True, "Ada")
    refused = sign_up_invited(bob, token)
    assert (refused.status_code, refused.json()["detail"]) == (
        400,
        INVITATION_EXPIRED.format(inviter="Ada"),
    )
    # Kept for its inviter to send again, as long as a one-time link, then pruned.
    with engine.begin() as connection:
        prune_credentials(connection)
    assert len(invitations(client)) == 1
    expire(session, "bob@example.com", CREDENTIAL_HISTORY + timedelta(minutes=1))
    with engine.begin() as connection:
        prune_credentials(connection)
    assert invitations(client) == []


def test_invitations_are_held_to_a_rate_per_inviter_and_per_address(
    client, session, mailer, ada, monkeypatch
):
    bob = another_browser(client)
    signup(bob, "bob@example.com")
    counted_afresh(session)
    monkeypatch.setattr(get_settings(), "rate_sign_ins", 1)
    invite(client, "carol@example.com")
    # Another organisation's admin cannot flood the same inbox.
    flooded = bob.post("/api/organization/invitations", json={"email": "carol@example.com"})
    assert flooded.status_code == 429
    assert "for this email address" in flooded.json()["detail"]
    hurried = client.post("/api/organization/invitations", json={"email": "dan@example.com"})
    assert hurried.status_code == 429
    assert "from your account" in hurried.json()["detail"]
    assert len(mailer.sent) == 3


def test_a_new_account_through_a_provider_joins_the_organisation_that_invited_it(
    client, session, mailer, identity_provider, ada, monkeypatch
):
    monkeypatch.setattr(get_settings(), "google_oauth_client_id", "google-client")
    monkeypatch.setattr(get_settings(), "google_oauth_client_secret", "google-secret")
    org_id = ada["organization"]["id"]
    invite(client, "grace@example.com", "viewer")
    token = mailer.token()

    grace = another_browser(client)
    query, handshake = begin_oauth(grace, next="/invitation", invitation=token)
    # The token stays in the cookie, never in what the provider is sent.
    assert handshake.invitation == token and token not in str(query)
    identity_provider.claims = google_claims(
        handshake.nonce, sub="google-grace", email="grace@example.com", name="Grace"
    )
    arrived = callback(grace, code="the-code", state=handshake.state)
    assert arrived.headers["location"] == "/"
    account = me(grace).json()
    assert (account["organization"]["id"], account["role"]) == (org_id, "viewer")
    assert [o["id"] for o in account["organizations"]] == [org_id]
    assert account["email_verified"] is True and account["terms_due"] is True
    assert actions(session, org_id)[-3:] == [
        ("sign_up", {"method": "google"}),
        ("invitation_accepted", {"role": "viewer"}),
        ("sign_in", {"method": "google"}),
    ]

    # A provider's account with another address starts one of its own, and the invitation
    # waits on its page.
    invite(client, "hopper@example.com")
    token = mailer.token()
    other = another_browser(client)
    _, handshake = begin_oauth(other, next="/invitation", invitation=token)
    identity_provider.claims = google_claims(
        handshake.nonce, sub="google-other", email="other@example.com"
    )
    assert callback(other, code="the-code", state=handshake.state).headers["location"] == (
        "/invitation"
    )
    assert me(other).json()["organization"]["id"] != org_id
    assert shown(other, token).status_code == 200


# Joining by domain


def test_everyone_at_an_admins_own_domain_is_offered_the_organisation(client, session, ada):
    org_id = ada["organization"]["id"]
    chosen = joining(client, "Acme.com", role="viewer")
    assert chosen.status_code == 200, chosen.text
    assert chosen.json()["organization"]["join_domains"] == ["acme.com"]
    assert chosen.json()["organization"]["join_role"] == "viewer"
    assert actions(session, org_id)[-1] == (
        "joining_changed",
        {"domains": "acme.com", "role": "viewer"},
    )

    bob = another_browser(client)
    offered = signup(bob, "bob@acme.com", name="Bob")
    assert offered["joinable"] == [{"id": org_id, "name": "Ada's workspace", "role": "viewer"}]
    joined = bob.post(f"/api/organizations/{org_id}/join")
    assert joined.status_code == 200, joined.text
    assert (joined.json()["organization"]["id"], joined.json()["role"]) == (org_id, "viewer")
    assert joined.json()["joinable"] == []
    assert me(bob).json()["organization"]["id"] == org_id
    assert actions(session, org_id)[-1] == (
        "member_joined",
        {"role": "viewer", "domain": "acme.com"},
    )
    again = bob.post(f"/api/organizations/{org_id}/join")
    assert again.status_code == 409

    # Someone at another domain is offered nothing, and finds nothing to join.
    eve = another_browser(client)
    assert signup(eve, "eve@globex.com")["joinable"] == []
    assert eve.post(f"/api/organizations/{org_id}/join").status_code == 404


def test_a_domain_is_added_only_by_someone_with_a_confirmed_address_at_it(client, session, ada):
    for domain, sentence in (
        ("gmail.com", "Anyone can have an address at gmail.com"),
        ("globex.com", "Only someone whose own confirmed address is at globex.com"),
        ("not a domain", "not a domain is not a domain."),
    ):
        refused = joining(client, domain)
        assert refused.status_code == 400
        assert refused.json()["detail"].startswith(sentence)
    session.execute(update(User).where(User.email == "ada@acme.com").values(email_verified_at=None))
    session.commit()
    assert joining(client, "acme.com").status_code == 400

    # A domain someone else added stays, and any may be taken off.
    session.execute(
        update(User).where(User.email == "ada@acme.com").values(email_verified_at=func.now())
    )
    session.commit()
    assert joining(client, "acme.com").status_code == 200
    bob = another_browser(client)
    join(session, ada["organization"]["id"], signup(bob, "bob@acme.co.uk")["id"], "admin")
    bob.post(f"/api/organizations/{ada['organization']['id']}/switch")
    both = joining(bob, "acme.com", "acme.co.uk").json()["organization"]["join_domains"]
    assert both == ["acme.co.uk", "acme.com"]
    assert joining(client, "acme.co.uk").json()["organization"]["join_domains"] == ["acme.co.uk"]
    assert joining(client).json()["organization"]["join_domains"] == []

    # A member chooses nothing.
    cat = another_browser(client)
    join(session, ada["organization"]["id"], signup(cat, "cat@acme.com")["id"])
    cat.post(f"/api/organizations/{ada['organization']['id']}/switch")
    assert joining(cat, "acme.com").json()["detail"] == ADMINS_ONLY


def test_an_unconfirmed_address_or_a_full_plan_joins_nothing(client, session, ada, room):
    org_id = ada["organization"]["id"]
    joining(client, "acme.com")
    bob = another_browser(client)
    signup(bob, "bob@acme.com")
    session.execute(update(User).where(User.email == "bob@acme.com").values(email_verified_at=None))
    session.commit()
    assert me(bob).json()["joinable"] == []
    assert bob.post(f"/api/organizations/{org_id}/join").status_code == 404

    session.execute(
        update(User).where(User.email == "bob@acme.com").values(email_verified_at=func.now())
    )
    session.commit()
    room(1)
    full = bob.post(f"/api/organizations/{org_id}/join")
    assert full.status_code == 402
    assert full.json()["detail"].startswith("Ada's workspace has as many members as its plan")


def test_an_organisation_past_its_plan_invites_and_takes_nobody(client, session, ada, room):
    org_id = ada["organization"]["id"]
    joining(client, "acme.com")
    sent = invite(client, "bob@example.com")
    # Its members lowered under the member and the invitation it has.
    room(1)
    refused = client.post("/api/organization/invitations", json={"email": "cat@example.com"})
    assert refused.status_code == 402
    assert refused.json() == {
        "detail": (
            "This organisation holds more than the Free plan allows (2 members of 1), so until "
            "it is within the plan nothing can be added or changed: what it holds can still be "
            "read, asked about and deleted. Remove members or revoke invitations, or upgrade "
            "your plan."
        ),
        "limit": "members",
    }
    # The invitation still open is sent again, since it holds its place already.
    assert client.post(f"/api/organization/invitations/{sent['id']}/resend").status_code == 200

    dan = another_browser(client)
    signup(dan, "dan@acme.com")
    refused = dan.post(f"/api/organizations/{org_id}/join")
    assert (refused.status_code, refused.json()["detail"]) == (
        402,
        "Ada's workspace holds more than its plan allows, so nobody can join it until it is "
        "within the plan. Ask one of its owners to make room, or to move it to a larger plan.",
    )

    # Revoking it is the way back, and the next invitation goes out.
    assert client.delete(f"/api/organization/invitations/{sent['id']}").status_code == 204
    room(2)
    invite(client, "cat@example.com")
