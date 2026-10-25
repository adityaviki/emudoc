"""Pinned chats: each reader pins the chats they read to the top of their own chat list, newest
pin first, the rest by their latest question; a pin waits with its chat in the trash and while it
is private again, and goes with it."""

from sqlalchemy import func, select

from app.export import account_export
from app.models import ChatPin, User
from tests.test_api import create_project
from tests.test_chats import new_chat


def listed(client, project_id) -> list[tuple[int, bool]]:
    """The chat list as the page reads it: each chat's id and whether the reader pinned it."""
    chats = client.get(f"/api/projects/{project_id}/chats").json()
    return [(chat["id"], chat["pinned"]) for chat in chats]


def pin(client, chat_id) -> dict:
    response = client.put(f"/api/chats/{chat_id}/pin")
    assert response.status_code == 200, response.text
    return response.json()


def unpin(client, chat_id) -> dict:
    response = client.delete(f"/api/chats/{chat_id}/pin")
    assert response.status_code == 200, response.text
    return response.json()


def test_pinned_chats_lead_the_list_newest_pin_first(client):
    project = create_project(client, "Pins")
    first, second, third = (new_chat(client, project["id"])["id"] for _ in range(3))
    assert listed(client, project["id"]) == [(third, False), (second, False), (first, False)]

    assert pin(client, first)["pinned"] is True
    assert listed(client, project["id"]) == [(first, True), (third, False), (second, False)]
    pin(client, second)
    assert listed(client, project["id"]) == [(second, True), (first, True), (third, False)]
    # Pinned again, a chat keeps the place its first pin gave it.
    pin(client, first)
    assert [chat for chat, _ in listed(client, project["id"])] == [second, first, third]

    # Unpinned, a chat goes back among the others by its latest question.
    assert unpin(client, second)["pinned"] is False
    assert listed(client, project["id"]) == [(first, True), (third, False), (second, False)]
    unpin(client, second)
    assert listed(client, project["id"])[2] == (second, False)

    # Every answer that shows the chat says whether its reader pinned it, so the page's list
    # keeps the pin through a rename, a reload and the trash.
    assert client.get(f"/api/chats/{first}").json()["pinned"] is True
    renamed = client.patch(f"/api/chats/{first}", json={"title": "Payment terms"}).json()
    assert renamed["pinned"] is True
    assert client.delete(f"/api/chats/{first}").status_code == 204
    assert [chat for chat, _ in listed(client, project["id"])] == [third, second]
    restored = client.post(f"/api/trash/chats/{first}/restore").json()
    assert restored["pinned"] is True
    assert listed(client, project["id"])[0] == (first, True)
    assert new_chat(client, project["id"])["pinned"] is False


def test_each_reader_pins_a_shared_chat_for_themselves(
    client, session, account, make_account, act_as
):
    project = create_project(client, "Shared pins")
    chat = new_chat(client, project["id"])["id"]
    client.patch(f"/api/chats/{chat}", json={"visibility": "project"})
    viewer = make_account("vic@example.com", account.organization, role="viewer")

    # Pinning is reading for oneself: a viewer pins the owner's shared chat, which stays
    # unpinned for the owner.
    act_as(viewer)
    assert pin(client, chat)["pinned"] is True
    assert listed(client, project["id"]) == [(chat, True)]
    client.app.dependency_overrides.clear()
    assert listed(client, project["id"]) == [(chat, False)]

    # Taken private, the chat leaves the viewer's list and their pin with it, until it is shared
    # again.
    client.patch(f"/api/chats/{chat}", json={"visibility": "private"})
    act_as(viewer)
    assert listed(client, project["id"]) == []
    assert client.put(f"/api/chats/{chat}/pin").status_code == 404
    client.app.dependency_overrides.clear()
    client.patch(f"/api/chats/{chat}", json={"visibility": "project"})
    act_as(viewer)
    assert listed(client, project["id"]) == [(chat, True)]

    # The member's export holds their pins, by the chat's title.
    exported = account_export(session, session.get(User, viewer.user.id)).pinned_chats
    assert [(pinned.project, pinned.chat) for pinned in exported] == [("Shared pins", "")]

    # A chat deleted for good takes its pins with it.
    client.app.dependency_overrides.clear()
    client.delete(f"/api/chats/{chat}")
    assert client.delete(f"/api/trash/chats/{chat}").status_code == 204
    assert session.scalar(select(func.count()).select_from(ChatPin)) == 0
