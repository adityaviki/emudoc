"""The workspace: renaming it, the members and their roles, handing it over, removing a member,
who keeps their account in no workspace until invited again, and the routes of a second
organisation, which are gone."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import get_args
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select, update

from app.api.auth import ADDRESS_HELD, WRONG_PASSWORD
from app.api.deps import NO_WORKSPACE, READ_ONLY
from app.api.organizations import (
    ADMINS_MOVE,
    ADMINS_ONLY,
    ADMINS_RESET,
    MANAGERS_ONLY,
    OWNERS_FOR_OWNERS,
    OWNERS_MOVE_OWNERS,
    OWNERS_ONLY,
    OWNERS_RESET_OWNERS,
    REMOVE_YOURSELF,
    THEIR_ADDRESS,
    YOUR_OWN_ADDRESS,
    YOUR_OWN_PASSWORD,
    YOUR_OWN_ROLE,
    YOURS_ALREADY,
)
from app.auth import HANDED_LINK_TTL
from app.db import engine
from app.models import ROLES, AuditEvent, Chat, Membership, Organization, UserSession
from app.schemas import Role
from tests.test_api import create_project
from tests.test_audit import actions, rows
from tests.test_auth import NEW_PASSWORD, PASSWORD, another_browser, login, me, signup
from tests.test_chats import new_chat


def join(session, org_id: int, user_id: int, role: str = "member") -> None:
    """Make the user a member, as an accepted invitation will."""
    session.add(Membership(org_id=org_id, user_id=user_id, role=role))
    session.commit()


def names(client) -> list[str]:
    return [project["name"] for project in client.get("/api/projects").json()]


def set_role(session, org_id: int, user_id: int, role: str) -> None:
    session.execute(
        update(Membership)
        .where(Membership.org_id == org_id, Membership.user_id == user_id)
        .values(role=role)
    )
    session.commit()


def change_role(client, user_id: int, role: str):
    return client.patch(f"/api/organization/members/{user_id}", json={"role": role})


def remove(client, user_id: int):
    return client.delete(f"/api/organization/members/{user_id}")


def roles(client) -> dict[str, str]:
    """The members' roles by name, as the organisation's page lists them."""
    return {m["name"]: m["role"] for m in client.get("/api/organization/members").json()}


@pytest.fixture
def team(client, session, signed_in_only):
    """Ada, signed in on `client`, the owner of her workspace, and Bob on a browser of his own,
    a member of it alone, as an invitation makes one."""
    ada = signup(client, "ada@example.com", name="Ada")
    bob_client = another_browser(client)
    bob = signup(bob_client, "bob@example.com", name="Bob", joins=ada["organization"]["id"])
    return ada, bob, bob_client


def test_no_route_starts_switches_leaves_joins_or_deletes_an_organisation(client, team):
    ada, bob, bob_client = team
    org_id = ada["organization"]["id"]
    gone = [
        ("post", "/api/organizations", {"name": "Another"}),
        ("post", f"/api/organizations/{org_id}/switch", None),
        ("post", "/api/organization/leave", None),
        ("post", "/api/organization/deletion", {"password": PASSWORD}),
        ("delete", "/api/organization/deletion", None),
        ("put", "/api/organization/joining", {"domains": ["example.com"]}),
        ("post", f"/api/organizations/{org_id}/join", None),
    ]
    for method, path, body in gone:
        response = client.request(method, path, json=body)
        assert response.status_code == 404, (method, path, response.status_code)
    assert "organizations" not in me(client).json()
    assert "joinable" not in me(client).json()


def test_the_local_account_may_rename_its_organisation(client, account):
    renamed = client.patch("/api/organization", json={"name": "My machine"}).json()
    assert (renamed["organization"]["name"], renamed["organization"]["slug"]) == (
        "My machine",
        "local",
    )


def test_owners_and_admins_rename_it_and_its_slug_stays(client, session, team):
    ada, bob, bob_client = team
    org_id = ada["organization"]["id"]

    refused = bob_client.patch("/api/organization", json={"name": "Bob's now"})
    assert (refused.status_code, refused.json()["detail"]) == (403, ADMINS_ONLY)
    renamed = client.patch("/api/organization", json={"name": "Lovelace & Co"})
    assert renamed.status_code == 200
    assert renamed.json()["organization"]["name"] == "Lovelace & Co"
    assert renamed.json()["organization"]["slug"] == ada["organization"]["slug"]
    assert me(bob_client).json()["organization"]["name"] == "Lovelace & Co"

    session.execute(
        Membership.__table__.update()
        .where(Membership.org_id == org_id, Membership.user_id == bob["id"])
        .values(role="admin")
    )
    session.commit()
    assert bob_client.patch("/api/organization", json={"name": "Ours"}).status_code == 200
    # The same name again records nothing.
    assert bob_client.patch("/api/organization", json={"name": "Ours"}).status_code == 200
    assert [(a, d) for a, d in actions(session, org_id) if a == "organization_renamed"] == [
        ("organization_renamed", {"from": "Ada's workspace", "to": "Lovelace & Co"}),
        ("organization_renamed", {"from": "Lovelace & Co", "to": "Ours"}),
    ]


def test_the_members_are_listed_owners_first(client, session, team, make_account):
    ada, bob, bob_client = team
    org_id = ada["organization"]["id"]
    viewer = make_account("vic@example.com", session.get(Organization, org_id), role="viewer")

    listed = bob_client.get("/api/organization/members").json()
    assert [(m["name"], m["email"], m["role"]) for m in listed] == [
        ("Ada", "ada@example.com", "owner"),
        ("Bob", "bob@example.com", "member"),
        ("Vic", "vic@example.com", "viewer"),
    ]
    assert listed[2]["user_id"] == viewer.user.id


def test_an_owner_hands_the_organisation_over_with_their_password(client, session, team):
    ada, bob, bob_client = team
    org_id = ada["organization"]["id"]

    def hand_over(who, user_id, password=PASSWORD):
        return who.post("/api/organization/owner", json={"user_id": user_id, "password": password})

    assert hand_over(bob_client, bob["id"]).json()["detail"] == OWNERS_ONLY
    wrong = hand_over(client, bob["id"], "not it")
    assert (wrong.status_code, wrong.json()["detail"]) == (403, WRONG_PASSWORD)
    assert hand_over(client, ada["id"]).json()["detail"] == YOURS_ALREADY
    assert hand_over(client, 10**9).status_code == 404

    handed = hand_over(client, bob["id"])
    assert handed.status_code == 200, handed.text
    assert handed.json()["role"] == "admin"
    assert me(bob_client).json()["role"] == "owner"
    assert hand_over(client, bob["id"]).json()["detail"] == OWNERS_ONLY
    again = hand_over(bob_client, ada["id"])
    assert again.status_code == 200 and me(client).json()["role"] == "owner"
    assert [(a, d) for a, d in actions(session, org_id) if a == "ownership_transferred"] == [
        ("ownership_transferred", {"from": "member"}),
        ("ownership_transferred", {"from": "admin"}),
    ]


def test_an_owner_cannot_hand_it_to_an_owner_already(client, session, team):
    ada, bob, bob_client = team
    org_id = ada["organization"]["id"]
    session.execute(
        Membership.__table__.update()
        .where(Membership.org_id == org_id, Membership.user_id == bob["id"])
        .values(role="owner")
    )
    session.commit()
    response = client.post(
        "/api/organization/owner", json={"user_id": bob["id"], "password": PASSWORD}
    )
    assert (response.status_code, response.json()["detail"]) == (
        409,
        "Bob is an owner of Ada's workspace already.",
    )


def test_the_roles_a_request_names_are_the_databases():
    assert get_args(Role) == ROLES


def test_owners_and_admins_change_the_other_members_roles(client, session, team, make_account):
    ada, bob, bob_client = team
    org_id = ada["organization"]["id"]
    vic = make_account("vic@example.com", session.get(Organization, org_id), role="viewer")
    vic = vic.user.id
    bobs = create_project(bob_client, "Bob's notes")

    refused = change_role(bob_client, vic, "member")
    assert (refused.status_code, refused.json()["detail"]) == (403, MANAGERS_ONLY)
    changed = change_role(client, bob["id"], "admin")
    assert changed.status_code == 200, changed.text
    assert (changed.json()["name"], changed.json()["role"]) == ("Bob", "admin")
    # The role is read at each request, so Bob's next one is an admin's.
    assert me(bob_client).json()["role"] == "admin"

    # An admin changes anyone's role but an owner's, to anything but owner, and never their own.
    assert change_role(bob_client, vic, "member").status_code == 200
    for user_id, role in ((vic, "owner"), (ada["id"], "admin")):
        response = change_role(bob_client, user_id, role)
        assert (response.status_code, response.json()["detail"]) == (403, OWNERS_FOR_OWNERS)
    own = change_role(bob_client, bob["id"], "member")
    assert (own.status_code, own.json()["detail"]) == (400, YOUR_OWN_ROLE)
    assert change_role(client, ada["id"], "admin").json()["detail"] == YOUR_OWN_ROLE
    assert change_role(client, 10**9, "member").status_code == 404
    assert change_role(client, vic, "boss").status_code == 422

    # The same role again records nothing, and an owner who makes another stays one.
    assert change_role(client, vic, "member").status_code == 200
    assert change_role(client, vic, "owner").status_code == 200
    assert change_role(client, bob["id"], "viewer").status_code == 200
    assert roles(client) == {"Ada": "owner", "Vic": "owner", "Bob": "viewer"}
    refused = bob_client.delete(f"/api/projects/{bobs['id']}")
    assert (refused.status_code, refused.json()["detail"]) == (403, READ_ONLY)
    assert [(a, d) for a, d in actions(session, org_id) if a == "role_changed"] == [
        ("role_changed", {"from": "member", "to": "admin"}),
        ("role_changed", {"from": "viewer", "to": "member"}),
        ("role_changed", {"from": "member", "to": "owner"}),
        ("role_changed", {"from": "admin", "to": "viewer"}),
    ]


def test_two_owners_cannot_take_each_others_role_at_once(client, session, team):
    ada, bob, bob_client = team
    org_id = ada["organization"]["id"]
    set_role(session, org_id, bob["id"], "owner")
    with engine.connect() as holder, ThreadPoolExecutor(1) as pool:
        # Ada's request holds the organisation and makes Bob an admin...
        holder.execute(select(Organization.id).where(Organization.id == org_id).with_for_update())
        holder.execute(
            update(Membership)
            .where(Membership.org_id == org_id, Membership.user_id == bob["id"])
            .values(role="admin")
        )
        # ...while Bob's, which began as an owner's, waits for it.
        waiting = pool.submit(change_role, bob_client, ada["id"], "admin")
        with pytest.raises(TimeoutError):
            waiting.result(timeout=0.5)
        holder.commit()
        refused = waiting.result(timeout=10)
    # Bob's is refused as the admin he is by then, and the organisation keeps its owner.
    assert (refused.status_code, refused.json()["detail"]) == (403, OWNERS_FOR_OWNERS)
    assert roles(client) == {"Ada": "owner", "Bob": "admin"}


def test_owners_and_admins_make_a_member_a_link_that_resets_their_password(client, session, team):
    ada, bob, bob_client = team
    org_id = ada["organization"]["id"]
    made = client.post(f"/api/organization/members/{bob['id']}/reset-link")
    assert made.status_code == 200, made.text
    link = made.json()["link"]
    assert link.startswith("http://testserver/reset-password#token=")
    lasts = datetime.fromisoformat(made.json()["expires_at"]) - datetime.now(UTC)
    assert HANDED_LINK_TTL - timedelta(minutes=1) < lasts <= HANDED_LINK_TTL
    # Whoever Ada hands it to sets Bob's password, which signs him out everywhere, once.
    token = parse_qs(urlsplit(link).fragment)["token"][0]
    reset = {"token": token, "password": NEW_PASSWORD}
    assert another_browser(client).post("/api/auth/password/reset", json=reset).status_code == 204
    assert me(bob_client).status_code == 401
    assert another_browser(client).post("/api/auth/password/reset", json=reset).status_code == 400
    login(bob_client, "bob@example.com", NEW_PASSWORD)
    made_row = session.scalars(
        select(AuditEvent).where(AuditEvent.action == "reset_link_made")
    ).one()
    assert (made_row.org_id, made_row.user_id) == (org_id, ada["id"])
    assert (made_row.target_type, made_row.target_id) == ("user", bob["id"])

    # Nobody makes one for themselves.
    yours = client.post(f"/api/organization/members/{ada['id']}/reset-link")
    assert (yours.status_code, yours.json()["detail"]) == (400, YOUR_OWN_PASSWORD)
    # An admin makes none for an owner, and a member none at all.
    set_role(session, org_id, bob["id"], "admin")
    for_owner = bob_client.post(f"/api/organization/members/{ada['id']}/reset-link")
    assert (for_owner.status_code, for_owner.json()["detail"]) == (403, OWNERS_RESET_OWNERS)
    set_role(session, org_id, bob["id"], "member")
    member = bob_client.post(f"/api/organization/members/{ada['id']}/reset-link")
    assert (member.status_code, member.json()["detail"]) == (403, ADMINS_RESET)


def test_owners_and_admins_make_a_member_a_link_that_moves_their_address(client, session, team):
    ada, bob, bob_client = team
    org_id = ada["organization"]["id"]
    link_to = f"/api/organization/members/{bob['id']}/email-link"
    first = client.post(link_to, json={"email": "bob@first.example"}).json()["link"]
    made = client.post(link_to, json={"email": "Bob@Analytical.org"})
    assert made.status_code == 200, made.text
    link = made.json()["link"]
    assert link.startswith("http://testserver/confirm-email#token=")
    lasts = datetime.fromisoformat(made.json()["expires_at"]) - datetime.now(UTC)
    assert HANDED_LINK_TTL - timedelta(minutes=1) < lasts <= HANDED_LINK_TTL
    # Nothing changes until it is opened, and the later link voids the earlier.
    assert me(bob_client).json()["email"] == "bob@example.com"
    stale = parse_qs(urlsplit(first).fragment)["token"][0]
    confirm = another_browser(client).post("/api/auth/email/confirm", json={"token": stale})
    assert confirm.status_code == 400
    # Whoever Ada hands it to moves Bob's account, signed in or not, once; the address is
    # confirmed, Ada vouching for it, and Bob stays signed in.
    token = parse_qs(urlsplit(link).fragment)["token"][0]
    opened = another_browser(client).post("/api/auth/email/confirm", json={"token": token})
    assert opened.status_code == 204
    account = me(bob_client).json()
    assert account["email"] == "bob@analytical.org" and account["email_verified"] is True
    again = another_browser(client).post("/api/auth/email/confirm", json={"token": token})
    assert again.status_code == 400
    made_row = session.scalars(
        select(AuditEvent).where(AuditEvent.action == "email_link_made")
    ).all()[-1]
    assert (made_row.org_id, made_row.user_id) == (org_id, ada["id"])
    assert (made_row.target_type, made_row.target_id) == ("user", bob["id"])
    assert made_row.detail == {}

    # Nobody makes one for themselves, nor to the address the member has or another account's.
    elsewhere = {"email": "someone@new.example"}
    yours = client.post(f"/api/organization/members/{ada['id']}/email-link", json=elsewhere)
    assert (yours.status_code, yours.json()["detail"]) == (400, YOUR_OWN_ADDRESS)
    same = client.post(link_to, json={"email": "BOB@analytical.org"})
    assert (same.status_code, same.json()["detail"]) == (400, THEIR_ADDRESS)
    taken = client.post(link_to, json={"email": "ada@example.com"})
    assert (taken.status_code, taken.json()["detail"]) == (409, ADDRESS_HELD)
    assert client.post(link_to, json={"email": "not an address"}).status_code == 422
    # An admin makes none for an owner, and a member none at all.
    set_role(session, org_id, bob["id"], "admin")
    to_ada = f"/api/organization/members/{ada['id']}/email-link"
    for_owner = bob_client.post(to_ada, json=elsewhere)
    assert (for_owner.status_code, for_owner.json()["detail"]) == (403, OWNERS_MOVE_OWNERS)
    set_role(session, org_id, bob["id"], "member")
    member = bob_client.post(to_ada, json=elsewhere)
    assert (member.status_code, member.json()["detail"]) == (403, ADMINS_MOVE)
    assert me(client).json()["email"] == "ada@example.com"


def test_a_member_removed_loses_their_private_chats_and_their_place(client, session, team):
    ada, bob, bob_client = team
    org_id = ada["organization"]["id"]
    project = create_project(client, "Contracts")
    new_chat(bob_client, project["id"])
    shared = new_chat(bob_client, project["id"])
    bob_client.patch(f"/api/chats/{shared['id']}", json={"visibility": "project"})
    # Another browser of Bob's works in Ada's workspace too, until he is removed.
    elsewhere = another_browser(client)
    login(elsewhere, "bob@example.com")

    refused = remove(bob_client, ada["id"])
    assert (refused.status_code, refused.json()["detail"]) == (403, MANAGERS_ONLY)
    yourself = remove(client, ada["id"])
    assert (yourself.status_code, yourself.json()["detail"]) == (400, REMOVE_YOURSELF)
    assert remove(client, 10**9).status_code == 404

    removed = remove(client, bob["id"])
    assert removed.status_code == 204, removed.text
    assert roles(client) == {"Ada": "owner"}
    session.expire_all()
    assert set(session.scalars(select(Chat.id))) == {shared["id"]}
    # His sessions work in no organisation now; Ada's still works in hers.
    working = select(UserSession.user_id).where(UserSession.org_id == org_id)
    assert set(session.scalars(working)) <= {ada["id"]}
    assert actions(session, org_id)[-1] == (
        "member_removed",
        {"role": "member", "private_chats": 1},
    )
    # No organisation is made for him, and both his browsers find him in none.
    made = select(Organization.id).where(Organization.name == "Bob's workspace")
    assert session.scalar(made) is None
    for browser in (bob_client, elsewhere):
        outside = me(browser).json()
        assert (outside["organization"], outside["role"], outside["email"]) == (
            None,
            None,
            "bob@example.com",
        )
        refused = browser.get("/api/projects")
        assert (refused.status_code, refused.json()["detail"]) == (403, NO_WORKSPACE)


def test_a_member_removed_keeps_their_account_and_comes_back_by_an_invitation(
    client, session, team
):
    ada, bob, bob_client = team
    assert remove(client, bob["id"]).status_code == 204
    bob_client.post("/api/auth/logout")
    # Signing in still works, and says he belongs to no workspace.
    login(bob_client, "bob@example.com")
    assert me(bob_client).json()["organization"] is None

    invited = client.post(
        "/api/organization/invitations", json={"email": "bob@example.com", "role": "viewer"}
    )
    assert invited.status_code == 201, invited.text
    token = parse_qs(urlsplit(invited.json()["link"]).fragment)["token"][0]
    back = bob_client.post("/api/auth/invitation/accept", json={"token": token})
    assert back.status_code == 200, back.text
    assert (back.json()["organization"]["id"], back.json()["role"]) == (
        ada["organization"]["id"],
        "viewer",
    )
    assert bob_client.get("/api/projects").status_code == 200
    assert roles(client) == {"Ada": "owner", "Bob": "viewer"}


def test_only_an_owner_removes_an_owner(client, session, team, make_account):
    ada, bob, bob_client = team
    org_id = ada["organization"]["id"]
    organization = session.get(Organization, org_id)
    cy = make_account("cy@example.com", organization, role="owner").user.id
    dee = make_account("dee@example.com", organization, role="admin").user.id
    set_role(session, org_id, bob["id"], "admin")

    refused = remove(bob_client, cy)
    assert (refused.status_code, refused.json()["detail"]) == (403, OWNERS_FOR_OWNERS)
    assert remove(bob_client, dee).status_code == 204
    assert remove(client, cy).status_code == 204
    assert roles(client) == {"Ada": "owner", "Bob": "admin"}


def test_the_workspace_logs_its_members_sign_ins(client, session, team):
    ada, bob, bob_client = team
    org_id = ada["organization"]["id"]
    bob_client.post("/api/auth/logout")
    login(bob_client, "bob@example.com")
    bobs = [row.action for row in rows(session, org_id) if row.user_id == bob["id"]]
    assert bobs == ["sign_up", "sign_out", "sign_in"]
