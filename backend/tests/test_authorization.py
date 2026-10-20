"""Who may see and change what: every route is scoped to the caller's organisation and role."""

import re
from dataclasses import dataclass

import pytest
from pydantic_ai.messages import TextPart

from app.answers import answer
from app.indexing.pipeline import process_document
from tests.conftest import FakeTurn
from tests.test_api import create_project, upload
from tests.test_chats import answered, ask, new_chat
from tests.test_folders import make_folder

Route = tuple[str, str, dict]


@dataclass
class World:
    """A project with a folder, an indexed document and a chat holding one answered question,
    and an invitation to the organisation, all made by the local owner in the local
    organisation, and another member of it."""

    org_id: int
    project_id: int
    folder_id: int
    document_id: int
    chat_id: int
    message_id: int
    passage_id: int
    invitation_id: int
    member_id: int


@pytest.fixture
def world(client, services, sample_pdf, account, make_account) -> World:
    project = create_project(client)
    folder = make_folder(client, project["id"], "Contracts")
    document = upload(client, project["id"], sample_pdf)["documents"][0]
    process_document(document["id"], services)
    chat = new_chat(client, project["id"])
    services.llm.agent_turns.append(FakeTurn(TextPart("Nothing is due.")))
    asked = ask(client, chat["id"], "What is due?")
    answer(asked["answer"]["id"], services)
    hits = client.get(f"/api/projects/{project['id']}/search", params={"q": "notice"}).json()
    invited = client.post("/api/organization/invitations", json={"email": "ivy@example.com"})
    member = make_account("mo@example.com", account.organization, role="member")
    return World(
        account.organization.id,
        project["id"],
        folder["id"],
        document["id"],
        chat["id"],
        asked["answer"]["id"],
        hits["hits"][0]["passage_id"],
        invited.json()["id"],
        member.user.id,
    )


def reads(w: World) -> list[Route]:
    """Every route that shows a project or what is in it, the chat aside."""
    return [
        ("GET", f"/api/projects/{w.project_id}", {}),
        ("GET", f"/api/projects/{w.project_id}/access", {}),
        ("GET", f"/api/projects/{w.project_id}/folders", {}),
        ("GET", f"/api/projects/{w.project_id}/documents", {}),
        ("GET", f"/api/projects/{w.project_id}/chats", {}),
        ("GET", f"/api/projects/{w.project_id}/search", {"params": {"q": "notice"}}),
        ("GET", f"/api/projects/{w.project_id}/facts", {}),
        ("GET", f"/api/projects/{w.project_id}/facts/names", {}),
        ("GET", f"/api/documents/{w.document_id}", {}),
        ("GET", f"/api/documents/{w.document_id}/file", {}),
        ("GET", f"/api/documents/{w.document_id}/pages/1.png", {}),
        ("GET", f"/api/documents/{w.document_id}/thumbnail.png", {}),
        ("GET", f"/api/documents/{w.document_id}/outline", {}),
        ("GET", f"/api/documents/{w.document_id}/content", {}),
        ("GET", f"/api/documents/{w.document_id}/facts", {}),
        ("GET", f"/api/passages/{w.passage_id}", {}),
    ]


def writes(w: World) -> list[Route]:
    """Every route that changes a project or what is in it, the destructive ones last."""
    return [
        ("PATCH", f"/api/projects/{w.project_id}", {"json": {"name": "Renamed"}}),
        (
            "PUT",
            f"/api/projects/{w.project_id}/access",
            {"json": {"visibility": "organization", "user_ids": []}},
        ),
        ("POST", f"/api/projects/{w.project_id}/reprocess", {}),
        ("POST", f"/api/projects/{w.project_id}/folders", {"json": {"name": "Invoices"}}),
        (
            "POST",
            f"/api/projects/{w.project_id}/documents",
            {"files": [("files", ("other.txt", b"Some other file."))]},
        ),
        ("PATCH", f"/api/folders/{w.folder_id}", {"json": {"name": "Renamed"}}),
        ("PATCH", f"/api/documents/{w.document_id}", {"json": {"filename": "renamed.pdf"}}),
        ("POST", f"/api/documents/{w.document_id}/reprocess", {}),
        ("DELETE", f"/api/folders/{w.folder_id}", {}),
        ("DELETE", f"/api/documents/{w.document_id}", {}),
        ("DELETE", f"/api/projects/{w.project_id}", {}),
    ]


def chat_reads(w: World) -> list[Route]:
    """The routes that show a chat, which a shared chat shows the project's members."""
    return [
        ("GET", f"/api/chats/{w.chat_id}", {}),
        ("GET", f"/api/chats/{w.chat_id}/messages/{w.message_id}/events", {}),
    ]


def chat_writes(w: World) -> list[Route]:
    """The routes that change a chat or ask in it, which stay its starter's when it is shared."""
    return [
        ("PATCH", f"/api/chats/{w.chat_id}", {"json": {"title": "Renamed"}}),
        ("POST", f"/api/chats/{w.chat_id}/messages", {"json": {"content": "What is due?"}}),
        ("POST", f"/api/chats/{w.chat_id}/messages/{w.message_id}/stop", {}),
        ("DELETE", f"/api/chats/{w.chat_id}", {}),
    ]


def chat_routes(w: World) -> list[Route]:
    return chat_reads(w) + chat_writes(w)


def starts(w: World) -> list[Route]:
    """Starting a chat, which is reading: a viewer may (test_a_viewer_can_read_but_not_change)."""
    return [("POST", f"/api/projects/{w.project_id}/chats", {})]


def switches(w: World) -> list[Route]:
    """Switching to the organisation, which only its members may, and joining it, which only
    someone whose confirmed address is at one of its domains may; the local account, which
    has no session, may do neither (test_organizations.py, test_invitations.py)."""
    return [
        ("POST", f"/api/organizations/{w.org_id}/switch", {}),
        ("POST", f"/api/organizations/{w.org_id}/join", {}),
    ]


def invitations(w: World) -> list[Route]:
    """The organisation's invitations, which only its owners and admins manage."""
    return [
        ("POST", f"/api/organization/invitations/{w.invitation_id}/resend", {}),
        ("DELETE", f"/api/organization/invitations/{w.invitation_id}", {}),
    ]


def members(w: World) -> list[Route]:
    """Changing a member's role and removing them, which only the owners and admins may."""
    return [
        ("PATCH", f"/api/organization/members/{w.member_id}", {"json": {"role": "viewer"}}),
        ("DELETE", f"/api/organization/members/{w.member_id}", {}),
    ]


def scoped(w: World) -> list[Route]:
    return (
        reads(w)
        + writes(w)
        + chat_routes(w)
        + starts(w)
        + switches(w)
        + invitations(w)
        + members(w)
    )


def statuses(client, routes: list[Route]) -> dict[str, int]:
    """Status per route, keyed by "METHOD /path", so a failure names the route at fault."""
    return {
        f"{method} {path}": client.request(method, path, **kwargs).status_code
        for method, path, kwargs in routes
    }


def test_every_route_that_names_what_it_acts_on_is_tried(client, world):
    """A route added with an id in its path joins the lists above, or this fails: the lists
    are what the tests below hold to the caller's organisation and role. The account's own
    routes (/api/auth) are held to the user in test_auth.py."""
    tried = [(method, path) for method, path, _ in scoped(world)]
    untried = [
        f"{method.upper()} {template}"
        for template, operations in client.app.openapi()["paths"].items()
        if "{" in template and not template.startswith("/api/auth/")
        for method in operations
        if not any(
            method.upper() == used and re.fullmatch(re.sub(r"\{\w+\}", r"[^/]+", template), path)
            for used, path in tried
        )
    ]
    assert untried == []


def test_another_organisation_sees_nothing(client, world, make_account, act_as):
    act_as(make_account("eve@globex.com"))
    assert client.get("/api/projects").json() == []
    found = statuses(client, scoped(world))
    assert set(found.values()) == {404}, found

    # Eve's own project is hers, and hers alone.
    own = create_project(client, "Globex files")
    assert [p["id"] for p in client.get("/api/projects").json()] == [own["id"]]
    act_as(make_account("ada@initech.com"))
    assert client.get("/api/projects").json() == []

    # The owner's project is untouched by what Eve tried.
    client.app.dependency_overrides.clear()
    assert client.get(f"/api/projects/{world.project_id}").json()["document_count"] == 1
    assert client.get(f"/api/documents/{world.document_id}").json()["filename"] == "msa.pdf"
    folders = client.get(f"/api/projects/{world.project_id}/folders").json()
    assert [f["name"] for f in folders] == ["Contracts"]
    messages = client.get(f"/api/chats/{world.chat_id}").json()["messages"]
    assert [(m["content"], m["status"]) for m in messages] == [
        ("What is due?", "done"),
        ("Nothing is due.", "done"),
    ]


def test_a_private_project_is_found_by_nobody_else(client, world, account, make_account, act_as):
    """A member not chosen for a private project finds nothing of it, as another organisation
    does not; one chosen, and an admin, find everything (test_access.py has the rest)."""
    chosen = make_account("cy@example.com", account.organization, role="member")
    admin = make_account("al@example.com", account.organization, role="admin")
    client.patch(f"/api/chats/{world.chat_id}", json={"visibility": "project"})
    made = client.put(
        f"/api/projects/{world.project_id}/access",
        json={"visibility": "private", "user_ids": [chosen.user.id]},
    )
    assert made.status_code == 200, made.text
    project_routes = reads(world) + writes(world) + chat_routes(world) + starts(world)

    act_as(make_account("mo.too@example.com", account.organization, role="member"))
    assert client.get("/api/projects").json() == []
    found = statuses(client, project_routes)
    assert set(found.values()) == {404}, found

    for member in (chosen, admin):
        act_as(member)
        assert [p["id"] for p in client.get("/api/projects").json()] == [world.project_id]
        shown = statuses(client, reads(world) + chat_reads(world))
        assert set(shown.values()) == {200}, shown


def test_a_viewer_can_read_but_not_change(client, world, account, make_account, act_as):
    act_as(make_account("vic@example.com", account.organization, role="viewer"))
    assert [p["id"] for p in client.get("/api/projects").json()] == [world.project_id]
    seen = statuses(client, reads(world))
    assert set(seen.values()) == {200}, seen
    refused = statuses(client, writes(world))
    assert set(refused.values()) == {403}, refused
    assert client.post("/api/projects", json={"name": "Mine"}).status_code == 403
    detail = client.delete(f"/api/documents/{world.document_id}").json()["detail"]
    assert detail.startswith("Your role in this organisation is viewer")
    managed = statuses(client, invitations(world) + members(world))
    assert set(managed.values()) == {403}, managed

    # Asking questions is reading: a viewer may start a chat of their own.
    chat = new_chat(client, world.project_id)
    assert chat["visibility"] == "private"


def test_a_member_can_change_things(client, world, account, make_account, act_as):
    act_as(make_account("max@example.com", account.organization, role="member"))
    assert client.post("/api/projects", json={"name": "Mine"}).status_code == 201
    renamed = client.patch(f"/api/projects/{world.project_id}", json={"name": "Ours"})
    assert renamed.status_code == 200
    assert client.delete(f"/api/folders/{world.folder_id}").status_code == 204
    # The invitations and the members' roles are the owners' and admins' to manage.
    managed = statuses(client, invitations(world) + members(world))
    assert set(managed.values()) == {403}, managed


def test_chats_are_private_until_shared(client, services, world, account, make_account, act_as):
    owner_chat = world.chat_id
    max_ = make_account("max@example.com", account.organization, role="member")
    act_as(max_)
    assert client.get(f"/api/projects/{world.project_id}/chats").json() == []
    hidden = statuses(client, chat_routes(world))
    assert set(hidden.values()) == {404}, hidden
    own = new_chat(client, world.project_id)

    # The owner shares the chat. Max can read it, but it stays the owner's to run.
    client.app.dependency_overrides.clear()
    assert [c["id"] for c in client.get(f"/api/projects/{world.project_id}/chats").json()] == [
        owner_chat
    ]
    assert client.get(f"/api/chats/{own['id']}").status_code == 404
    client.patch(f"/api/chats/{owner_chat}", json={"visibility": "project"})

    act_as(max_)
    listed = client.get(f"/api/projects/{world.project_id}/chats").json()
    assert {c["id"] for c in listed} == {owner_chat, own["id"]}
    shown = statuses(client, chat_reads(world))
    assert set(shown.values()) == {200}, shown
    refused = statuses(client, chat_writes(world))
    assert set(refused.values()) == {403}, refused
    detail = client.delete(f"/api/chats/{owner_chat}").json()["detail"]
    assert detail == "Only the member who started this chat can change it or ask in it."

    # Max's own chat is fully his, questions included.
    services.llm.agent_turns.append(FakeTurn(TextPart("Nothing is due.")))
    assert answered(client, services, own["id"], "What is due?")[-1]["status"] == "done"
    assert client.delete(f"/api/chats/{own['id']}").status_code == 204
