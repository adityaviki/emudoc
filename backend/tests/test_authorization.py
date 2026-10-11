"""Who may see and change what: every route is scoped to the caller's organisation and role."""

from dataclasses import dataclass

import pytest
from pydantic_ai.messages import TextPart

from app.indexing.pipeline import process_document
from tests.conftest import FakeTurn
from tests.test_api import create_project, upload
from tests.test_chats import ask, new_chat
from tests.test_folders import make_folder

Route = tuple[str, str, dict]


@dataclass
class World:
    """A project with a folder, an indexed document and a chat, all made by the local owner."""

    project_id: int
    folder_id: int
    document_id: int
    chat_id: int
    passage_id: int


@pytest.fixture
def world(client, services, sample_pdf) -> World:
    project = create_project(client)
    folder = make_folder(client, project["id"], "Contracts")
    document = upload(client, project["id"], sample_pdf)["documents"][0]
    process_document(document["id"], services)
    chat = new_chat(client, project["id"])
    hits = client.get(f"/api/projects/{project['id']}/search", params={"q": "notice"}).json()
    return World(
        project["id"], folder["id"], document["id"], chat["id"], hits["hits"][0]["passage_id"]
    )


def reads(w: World) -> list[Route]:
    """Every route that shows a project or what is in it, the chat aside."""
    return [
        ("GET", f"/api/projects/{w.project_id}", {}),
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


def chat_routes(w: World) -> list[Route]:
    return [
        ("GET", f"/api/chats/{w.chat_id}", {}),
        ("PATCH", f"/api/chats/{w.chat_id}", {"json": {"title": "Renamed"}}),
        ("POST", f"/api/chats/{w.chat_id}/messages", {"json": {"content": "What is due?"}}),
        ("DELETE", f"/api/chats/{w.chat_id}", {}),
    ]


def statuses(client, routes: list[Route]) -> dict[str, int]:
    """Status per route, keyed by "METHOD /path", so a failure names the route at fault."""
    return {
        f"{method} {path}": client.request(method, path, **kwargs).status_code
        for method, path, kwargs in routes
    }


def test_another_organisation_sees_nothing(client, world, make_account, act_as):
    act_as(make_account("eve@globex.com"))
    assert client.get("/api/projects").json() == []
    found = statuses(client, reads(world) + writes(world) + chat_routes(world))
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
    assert client.get(f"/api/chats/{world.chat_id}").json()["messages"] == []


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

    # Asking questions is reading: a viewer may start a chat of their own.
    chat = new_chat(client, world.project_id)
    assert chat["visibility"] == "private"


def test_a_member_can_change_things(client, world, account, make_account, act_as):
    act_as(make_account("max@example.com", account.organization, role="member"))
    assert client.post("/api/projects", json={"name": "Mine"}).status_code == 201
    renamed = client.patch(f"/api/projects/{world.project_id}", json={"name": "Ours"})
    assert renamed.status_code == 200
    assert client.delete(f"/api/folders/{world.folder_id}").status_code == 204


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
    assert client.get(f"/api/chats/{owner_chat}").status_code == 200
    refused = statuses(client, chat_routes(world)[1:])
    assert set(refused.values()) == {403}, refused
    detail = client.delete(f"/api/chats/{owner_chat}").json()["detail"]
    assert detail == "Only the member who started this chat can change it or ask in it."

    # Max's own chat is fully his, questions included.
    services.llm.agent_turns.append(FakeTurn(TextPart("Nothing is due.")))
    assert ask(client, own["id"], "What is due?")[-1]["type"] == "done"
    assert client.delete(f"/api/chats/{own['id']}").status_code == 204
