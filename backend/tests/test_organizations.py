"""A user's organisations: starting one, switching between them, renaming, the members, handing
one over and leaving it, and the tab a switch elsewhere leaves behind."""

import pytest
from sqlalchemy import select

from app.api.auth import WRONG_PASSWORD
from app.api.deps import ORGANIZATION_HEADER, SWITCHED
from app.api.organizations import (
    ADMINS_ONLY,
    LAST_ORGANISATION,
    NAME_NEEDED,
    NO_SESSION,
    OWNERS_ONLY,
    YOURS_ALREADY,
    too_many_free,
)
from app.config import get_settings
from app.models import Chat, Membership, Organization, UserSession
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


@pytest.fixture
def team(client, session, signed_in_only):
    """Ada, signed in on `client`, and Bob on a browser of his own, who is a member of the
    organisation Ada signed up with."""
    ada = signup(client, "ada@example.com", name="Ada")
    bob_client = another_browser(client)
    bob = signup(bob_client, "bob@example.com", name="Bob")
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
