"""A user's organisations: starting one, switching between them, renaming, the members and
their roles, handing one over, removing a member and leaving, and the tab a switch elsewhere
leaves behind."""

from concurrent.futures import ThreadPoolExecutor
from typing import get_args

import pytest
from sqlalchemy import delete, select, update

from app.api.auth import WRONG_PASSWORD
from app.api.deps import ORGANIZATION_HEADER, READ_ONLY, SWITCHED
from app.api.organizations import (
    ADMINS_ONLY,
    LAST_ORGANISATION,
    MANAGERS_ONLY,
    NAME_NEEDED,
    NO_SESSION,
    OWNERS_FOR_OWNERS,
    OWNERS_ONLY,
    REMOVE_YOURSELF,
    YOUR_OWN_ROLE,
    YOURS_ALREADY,
    too_many_free,
)
from app.config import get_settings
from app.db import engine
from app.models import ROLES, Chat, Membership, Organization, UserSession
from app.schemas import Role
from tests.test_api import create_project
from tests.test_audit import actions
from tests.test_auth import PASSWORD, another_browser, login, me, signup
from tests.test_chats import new_chat


def start(client, name: str) -> dict:
    response = client.post("/api/organizations", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def switch(client, org_id: int, **headers) -> dict:
    response = client.post(f"/api/organizations/{org_id}/switch", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


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
    """Ada, signed in on `client`, and Bob on a browser of his own, who is a member of the
    organisation Ada signed up with. It has seats for them on the Team plan, since the Free
    plan's one member would leave it past its plan, and so read-only."""
    ada = signup(client, "ada@example.com", name="Ada")
    bob_client = another_browser(client)
    bob = signup(bob_client, "bob@example.com", name="Bob")
    session.execute(
        update(Organization)
        .where(Organization.id == ada["organization"]["id"])
        .values(plan="team", seats=5)
    )
    join(session, ada["organization"]["id"], bob["id"])
    return ada, bob, bob_client


def test_a_member_starts_an_organisation_and_works_in_it(
    client, session, signed_in_only, monkeypatch
):
    monkeypatch.setattr(get_settings(), "data_region", "eu")
    first = signup(client, "ada@example.com", name="Ada")
    create_project(client, "Ada's files")

    started = start(client, "  Acme   Legal ")
    organization = started["organization"]
    assert (organization["name"], organization["plan"], organization["region"]) == (
        "Acme Legal",
        "free",
        "eu",
    )
    assert organization["slug"] == "acme-legal"
    assert started["role"] == "owner"
    assert [(o["name"], o["role"]) for o in started["organizations"]] == [
        ("Acme Legal", "owner"),
        ("Ada's workspace", "owner"),
    ]
    # The new organisation starts empty, and what is made now is its own.
    assert names(client) == []
    create_project(client, "Acme matters")
    assert me(client).json()["organization"]["id"] == organization["id"]

    back = switch(client, first["organization"]["id"])
    assert back["organization"]["name"] == "Ada's workspace"
    assert names(client) == ["Ada's files"]
    assert actions(session, organization["id"]) == [
        ("organization_created", {"name": "Acme Legal"})
    ]
    assert client.post("/api/organizations", json={"name": "   "}).json()["detail"] == NAME_NEEDED


def test_the_organisation_worked_in_is_the_sessions(client, session, team):
    ada, bob, bob_client = team
    bobs_own = bob["organization"]["id"]
    assert switch(bob_client, ada["organization"]["id"])["role"] == "member"

    # Another browser of Bob's starts where he joined first, and keeps its own choice.
    elsewhere = another_browser(client)
    login(elsewhere, "bob@example.com")
    assert me(elsewhere).json()["organization"]["id"] == bobs_own
    assert me(bob_client).json()["organization"]["id"] == ada["organization"]["id"]

    # A switch outlives the request, and a new sign-in starts again from the first joined.
    bob_client.post("/api/auth/logout")
    login(bob_client, "bob@example.com")
    assert me(bob_client).json()["organization"]["id"] == bobs_own


def test_only_a_member_may_switch_to_an_organisation(client, session, team, account):
    ada, bob, bob_client = team
    for org_id in (account.organization.id, 10**9):
        response = bob_client.post(f"/api/organizations/{org_id}/switch")
        assert response.status_code == 404
    assert me(bob_client).json()["organization"]["id"] == bob["organization"]["id"]


def test_the_local_account_may_rename_its_organisation_but_not_start_switch_or_leave(
    client, account
):
    assert client.post("/api/organizations", json={"name": "More"}).json()["detail"] == NO_SESSION
    response = client.post(f"/api/organizations/{account.organization.id}/switch")
    assert (response.status_code, response.json()["detail"]) == (409, NO_SESSION)
    assert client.post("/api/organization/leave").status_code == 409
    renamed = client.patch("/api/organization", json={"name": "My machine"}).json()
    assert (renamed["organization"]["name"], renamed["organization"]["slug"]) == (
        "My machine",
        "local",
    )


def test_a_tab_left_behind_by_a_switch_elsewhere_is_refused_until_it_catches_up(
    client, session, signed_in_only
):
    first = signup(client, "ada@example.com")["organization"]["id"]
    second = start(client, "Acme")["organization"]["id"]
    stale = {ORGANIZATION_HEADER: str(first)}

    refused = client.post("/api/projects", json={"name": "Lost"}, headers=stale)
    assert (refused.status_code, refused.json()["detail"]) == (409, SWITCHED)
    assert refused.headers[ORGANIZATION_HEADER] == str(second)
    assert client.get("/api/projects", headers=stale).status_code == 409
    assert names(client) == []

    # Asking who is signed in is how the tab catches up, and a switch may come from it too.
    assert me(client).json()["organization"]["id"] == second
    assert client.get("/api/auth/me", headers=stale).status_code == 200
    current = {ORGANIZATION_HEADER: str(second)}
    assert client.get("/api/projects", headers=current).status_code == 200
    assert switch(client, first, **current)["organization"]["id"] == first
    assert client.get("/api/projects", headers=stale).status_code == 200


def test_one_user_owns_only_so_many_free_organisations(
    client, session, team, monkeypatch, make_account
):
    ada, bob, bob_client = team
    # Bob's own, from his sign-up, is the first; a member's place in Ada's counts nothing.
    start(bob_client, "Bob's second")
    refused = bob_client.post("/api/organizations", json={"name": "Third"})
    assert (refused.status_code, refused.json()["detail"]) == (409, too_many_free(2))
    assert "You own 2 organisations on the Free plan" in too_many_free(2)

    # A paid one counts nothing either, and without a ceiling there is none.
    organization = session.get(Organization, bob["organization"]["id"])
    organization.plan = "pro"
    session.commit()
    start(bob_client, "Third")
    monkeypatch.setattr(get_settings(), "max_free_organizations", 1)
    assert bob_client.post("/api/organizations", json={"name": "Fourth"}).status_code == 409
    monkeypatch.setattr(get_settings(), "max_free_organizations", 0)
    start(bob_client, "Fourth")


def test_owners_and_admins_rename_it_and_its_slug_stays(client, session, team):
    ada, bob, bob_client = team
    org_id = ada["organization"]["id"]
    switch(bob_client, org_id)

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
    switch(bob_client, org_id)

    listed = bob_client.get("/api/organization/members").json()
    assert [(m["name"], m["email"], m["role"]) for m in listed] == [
        ("Ada", "ada@example.com", "owner"),
        ("Bob", "bob@example.com", "member"),
        ("Vic", "vic@example.com", "viewer"),
    ]
    assert listed[2]["user_id"] == viewer.user.id
    # Bob's own organisation is his alone.
    switch(bob_client, bob["organization"]["id"])
    assert [m["name"] for m in bob_client.get("/api/organization/members").json()] == ["Bob"]


def test_an_owner_hands_the_organisation_over_with_their_password(client, session, team):
    ada, bob, bob_client = team
    org_id = ada["organization"]["id"]

    def hand_over(who, user_id, password=PASSWORD):
        return who.post("/api/organization/owner", json={"user_id": user_id, "password": password})

    switch(bob_client, org_id)
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


def test_a_member_leaves_with_their_private_chats(client, session, team):
    ada, bob, bob_client = team
    org_id = ada["organization"]["id"]
    project = create_project(client, "Contracts")
    switch(bob_client, org_id)
    private = new_chat(bob_client, project["id"])["id"]
    shared = new_chat(bob_client, project["id"])
    bob_client.patch(f"/api/chats/{shared['id']}", json={"visibility": "project"})
    adas = new_chat(client, project["id"])
    # Another browser of Bob's works in Ada's organisation too, until he leaves it.
    elsewhere = another_browser(client)
    login(elsewhere, "bob@example.com")
    switch(elsewhere, org_id)

    left = bob_client.post("/api/organization/leave")
    assert left.status_code == 200, left.text
    assert left.json()["organization"]["id"] == bob["organization"]["id"]
    assert [o["id"] for o in left.json()["organizations"]] == [bob["organization"]["id"]]
    assert me(elsewhere).json()["organization"]["id"] == bob["organization"]["id"]
    session.expire_all()
    assert private not in set(session.scalars(select(Chat.id)))
    assert set(session.scalars(select(Chat.id))) == {shared["id"], adas["id"]}
    assert (
        session.scalar(
            select(Membership).where(Membership.org_id == org_id, Membership.user_id == bob["id"])
        )
        is None
    )
    working = select(UserSession.id).where(UserSession.org_id == org_id)
    assert session.scalars(working).all() == []
    assert actions(session, org_id)[-1] == ("member_left", {"role": "member", "private_chats": 1})
    assert bob_client.post(f"/api/organizations/{org_id}/switch").status_code == 404


def test_nobody_leaves_an_organisation_without_an_owner_or_with_nobody(client, session, team):
    ada, bob, bob_client = team
    # Bob's own is his only one but for Ada's, where he is a member.
    switch(bob_client, bob["organization"]["id"])
    alone = bob_client.post("/api/organization/leave")
    assert (alone.status_code, alone.json()["detail"]) == (
        409,
        "You are the only member of Bob's workspace, so leaving it would leave it with "
        "nobody. Make another member its owner first, or stay.",
    )
    owner = client.post("/api/organization/leave")
    assert owner.json()["detail"] == LAST_ORGANISATION
    start(client, "Acme")
    switch(client, ada["organization"]["id"])
    owner = client.post("/api/organization/leave")
    assert (owner.status_code, owner.json()["detail"]) == (
        409,
        "You are the only owner of Ada's workspace, which has other members. Make one of them "
        "its owner first.",
    )


def test_the_roles_a_request_names_are_the_databases():
    assert get_args(Role) == ROLES


def test_owners_and_admins_change_the_other_members_roles(client, session, team, make_account):
    ada, bob, bob_client = team
    org_id = ada["organization"]["id"]
    vic = make_account("vic@example.com", session.get(Organization, org_id), role="viewer")
    vic = vic.user.id
    switch(bob_client, org_id)
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
    switch(bob_client, org_id)
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


def test_a_member_removed_loses_their_private_chats_and_their_place(client, session, team):
    ada, bob, bob_client = team
    org_id = ada["organization"]["id"]
    project = create_project(client, "Contracts")
    switch(bob_client, org_id)
    new_chat(bob_client, project["id"])
    shared = new_chat(bob_client, project["id"])
    bob_client.patch(f"/api/chats/{shared['id']}", json={"visibility": "project"})
    # Another browser of Bob's works in Ada's organisation too, until he is removed.
    elsewhere = another_browser(client)
    login(elsewhere, "bob@example.com")
    switch(elsewhere, org_id)

    refused = remove(bob_client, ada["id"])
    assert (refused.status_code, refused.json()["detail"]) == (403, MANAGERS_ONLY)
    yourself = remove(client, ada["id"])
    assert (yourself.status_code, yourself.json()["detail"]) == (400, REMOVE_YOURSELF)
    assert remove(client, 10**9).status_code == 404

    removed = remove(client, bob["id"])
    assert removed.status_code == 204, removed.text
    assert roles(client) == {"Ada": "owner"}
    assert me(elsewhere).json()["organization"]["id"] == bob["organization"]["id"]
    # A tab of his that still shows Ada's organisation starts again in his own.
    stale = bob_client.get("/api/projects", headers={ORGANIZATION_HEADER: str(org_id)})
    assert (stale.status_code, stale.json()["detail"]) == (409, SWITCHED)
    session.expire_all()
    assert set(session.scalars(select(Chat.id))) == {shared["id"]}
    assert actions(session, org_id)[-1] == (
        "member_removed",
        {"role": "member", "private_chats": 1},
    )
    assert bob_client.post(f"/api/organizations/{org_id}/switch").status_code == 404


def test_only_an_owner_removes_an_owner(client, session, team, make_account):
    ada, bob, bob_client = team
    org_id = ada["organization"]["id"]
    organization = session.get(Organization, org_id)
    cy = make_account("cy@example.com", organization, role="owner").user.id
    dee = make_account("dee@example.com", organization, role="admin").user.id
    set_role(session, org_id, bob["id"], "admin")
    switch(bob_client, org_id)

    refused = remove(bob_client, cy)
    assert (refused.status_code, refused.json()["detail"]) == (403, OWNERS_FOR_OWNERS)
    assert remove(bob_client, dee).status_code == 204
    assert remove(client, cy).status_code == 204
    assert roles(client) == {"Ada": "owner", "Bob": "admin"}


def test_a_member_removed_from_their_last_organisation_is_given_one_of_their_own(
    client, session, team
):
    ada, bob, bob_client = team
    org_id = ada["organization"]["id"]
    # Bob's own organisation is gone, so Ada's is the only one he is a member of.
    session.execute(delete(Organization).where(Organization.id == bob["organization"]["id"]))
    session.commit()
    assert me(bob_client).json()["organization"]["id"] == org_id

    assert remove(client, bob["id"]).status_code == 204
    now = me(bob_client).json()
    own = now["organization"]
    assert (own["name"], own["plan"], now["role"]) == ("Bob's workspace", "free", "owner")
    assert [o["id"] for o in now["organizations"]] == [own["id"]]
    assert names(bob_client) == []
    assert actions(session, own["id"]) == [("organization_created", {"name": "Bob's workspace"})]


def test_each_organisation_logs_the_sign_ins_and_acts_of_its_own(client, session, team):
    ada, bob, bob_client = team
    org_id = ada["organization"]["id"]
    bobs_own = bob["organization"]["id"]
    switch(bob_client, org_id)
    bob_client.post("/api/auth/logout")
    login(bob_client, "bob@example.com")

    # The sign-out was where his session worked by then, the sign-in where it starts.
    assert ("sign_out", {}) in actions(session, org_id)
    assert [a for a, _ in actions(session, bobs_own)] == [
        "sign_up",
        "terms_accepted",
        "sign_in",
    ]
