"""Who sees a project: everyone in its organisation, or the members chosen for it and the owners
and admins (app/access.py). That every route about a project holds to it is
`test_a_private_project_is_found_by_nobody_else` in test_authorization.py."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.access import KEEP_YOURSELF, NOT_MEMBERS
from tests.test_api import create_project
from tests.test_audit import actions
from tests.test_chats import new_chat
from tests.test_organizations import join


@pytest.fixture
def crew(account, make_account) -> SimpleNamespace:
    """The local owner's organisation with an admin, two members and a viewer."""
    org = account.organization
    return SimpleNamespace(
        owner=account,
        ann=make_account("ann@example.com", org, role="admin"),
        bob=make_account("bob@example.com", org, role="member"),
        cat=make_account("cat@example.com", org, role="member"),
        vic=make_account("vic@example.com", org, role="viewer"),
    )


def access(client, project_id: int, visibility: str, *members):
    return client.put(
        f"/api/projects/{project_id}/access",
        json={"visibility": visibility, "user_ids": [member.user.id for member in members]},
    )


def chosen(client, project_id: int) -> dict:
    return client.get(f"/api/projects/{project_id}/access").json()


def seen(client) -> list[str]:
    """The projects the account is shown, by name."""
    return [project["name"] for project in client.get("/api/projects").json()]


def ids(*members) -> list[int]:
    return sorted(member.user.id for member in members)


def test_a_project_is_seen_by_everyone_in_its_organisation_until_made_private(client, crew, act_as):
    project = create_project(client)
    assert project["visibility"] == "organization"
    assert chosen(client, project["id"]) == {"visibility": "organization", "user_ids": []}
    for member in (crew.bob, crew.vic):
        act_as(member)
        assert seen(client) == ["Contracts"]


def test_a_private_project_is_seen_by_its_members_and_the_owners_and_admins(
    client, session, crew, act_as
):
    project = create_project(client)
    made = access(client, project["id"], "private", crew.bob, crew.vic)
    assert made.status_code == 200, made.text
    assert made.json() == {"visibility": "private", "user_ids": ids(crew.bob, crew.vic)}
    assert actions(session, crew.owner.organization.id)[-1] == (
        "project_access_changed",
        {
            "name": "Contracts",
            "from": "organization",
            "to": "private",
            "added": ids(crew.bob, crew.vic),
            "removed": [],
        },
    )

    for member, sees in ((crew.ann, True), (crew.bob, True), (crew.vic, True), (crew.cat, False)):
        act_as(member)
        assert seen(client) == (["Contracts"] if sees else []), member.user.email
        found = client.get(f"/api/projects/{project['id']}")
        assert found.status_code == (200 if sees else 404), member.user.email
    # Nothing tells a member left out that it exists, even who may see it.
    assert client.get(f"/api/projects/{project['id']}/access").status_code == 404

    act_as(crew.bob)
    assert client.get("/api/projects").json()[0]["visibility"] == "private"


def test_being_chosen_for_one_private_project_shows_no_other(client, crew, act_as):
    salaries = create_project(client, "Salaries")
    bonuses = create_project(client, "Bonuses")
    access(client, salaries["id"], "private", crew.bob)
    access(client, bonuses["id"], "private", crew.cat)
    act_as(crew.bob)
    assert seen(client) == ["Salaries"]
    assert client.get(f"/api/projects/{bonuses['id']}").status_code == 404


def test_a_member_who_makes_a_project_private_stays_among_its_members(client, crew, act_as):
    project = create_project(client)
    act_as(crew.bob)
    refused = access(client, project["id"], "private")
    assert (refused.status_code, refused.json()["detail"]) == (400, KEEP_YOURSELF)
    assert access(client, project["id"], "private", crew.bob, crew.vic).status_code == 200

    # A viewer chosen reads it but chooses nobody, a member left out does not find it, and an
    # admin who was not chosen manages it all the same.
    act_as(crew.vic)
    assert seen(client) == ["Contracts"]
    assert access(client, project["id"], "organization").status_code == 403
    act_as(crew.cat)
    assert access(client, project["id"], "organization").status_code == 404
    act_as(crew.ann)
    assert access(client, project["id"], "private", crew.cat).status_code == 200
    act_as(crew.cat)
    assert seen(client) == ["Contracts"]
    act_as(crew.bob)
    assert seen(client) == []


def test_only_the_organisations_members_are_chosen(client, crew, make_account):
    project = create_project(client)
    eve = make_account("eve@globex.com")
    refused = access(client, project["id"], "private", crew.bob, eve)
    assert (refused.status_code, refused.json()["detail"]) == (400, NOT_MEMBERS)
    assert chosen(client, project["id"]) == {"visibility": "organization", "user_ids": []}


def test_opening_a_project_again_forgets_its_members(client, session, crew, act_as):
    project = create_project(client)
    access(client, project["id"], "private", crew.bob)
    opened = access(client, project["id"], "organization", crew.bob)
    assert opened.json() == {"visibility": "organization", "user_ids": []}
    assert actions(session, crew.owner.organization.id)[-1][1] == {
        "name": "Contracts",
        "from": "private",
        "to": "organization",
        "added": [],
        "removed": ids(crew.bob),
    }
    # Nothing changed, nothing recorded.
    recorded = len(actions(session))
    assert access(client, project["id"], "organization").status_code == 200
    assert len(actions(session)) == recorded

    access(client, project["id"], "private")
    act_as(crew.bob)
    assert seen(client) == []


def test_a_project_made_private_is_its_makers(client, crew, act_as):
    act_as(crew.bob)
    made = client.post("/api/projects", json={"name": "Salaries", "visibility": "private"})
    assert made.status_code == 201, made.text
    assert made.json()["visibility"] == "private"
    assert chosen(client, made.json()["id"]) == {"visibility": "private", "user_ids": ids(crew.bob)}
    act_as(crew.cat)
    assert seen(client) == []
    act_as(crew.ann)
    assert seen(client) == ["Salaries"]


def test_a_member_removed_is_chosen_for_nothing_on_joining_again(client, session, crew, act_as):
    project = create_project(client)
    access(client, project["id"], "private", crew.bob, crew.cat)
    assert client.delete(f"/api/organization/members/{crew.bob.user.id}").status_code == 204
    assert chosen(client, project["id"])["user_ids"] == ids(crew.cat)

    join(session, crew.owner.organization.id, crew.bob.user.id)
    act_as(crew.bob)
    assert seen(client) == []


def test_a_role_changed_keeps_the_projects_chosen_for_it(client, crew, act_as):
    project = create_project(client)
    access(client, project["id"], "private", crew.bob)
    for member, role in ((crew.ann, "member"), (crew.bob, "viewer")):
        changed = client.patch(f"/api/organization/members/{member.user.id}", json={"role": role})
        assert changed.status_code == 200, changed.text

    # The admin made a member sees what was chosen for her alone: nothing; the member made a
    # viewer still reads the project he was chosen for.
    act_as(replace(crew.ann, role="member"))
    assert seen(client) == []
    act_as(replace(crew.bob, role="viewer"))
    assert seen(client) == ["Contracts"]


def test_chats_in_a_project_no_longer_seen_come_back_with_it(client, crew, act_as):
    project = create_project(client)
    act_as(crew.bob)
    chat = new_chat(client, project["id"])
    act_as(crew.owner)
    access(client, project["id"], "private")

    act_as(crew.bob)
    assert client.get(f"/api/chats/{chat['id']}").status_code == 404
    assert client.get(f"/api/projects/{project['id']}/chats").status_code == 404
    act_as(crew.owner)
    access(client, project["id"], "private", crew.bob)
    act_as(crew.bob)
    assert client.get(f"/api/chats/{chat['id']}").status_code == 200
    listed = client.get(f"/api/projects/{project['id']}/chats").json()
    assert [c["id"] for c in listed] == [chat["id"]]


def test_the_account_export_names_the_private_projects_chosen_for_it(client, crew, act_as):
    project = create_project(client)
    create_project(client, "Open files")
    access(client, project["id"], "private", crew.bob)
    act_as(crew.bob)
    export = client.get("/api/auth/account/export").json()
    assert [row["private_projects"] for row in export["organizations"]] == [["Contracts"]]
