"""A document's comments (app/comments.py): threads everyone who sees the project writes in,
viewers too, about the document or a passage of it, whose quote outlives a new reading; members
mentioned and a thread's writers told of a reply, never what was written; changed by their
writer, deleted by them or an owner or admin; kept in the audit log by names alone, through the
trash, in the project's export and the writer's own, without the writer's name once the account
is gone."""

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select

from app.comments import (
    BLANK,
    BLOCK_GONE,
    NOT_MENTIONABLE,
    NOT_YOURS,
    NOT_YOURS_TO_DELETE,
    PASSAGE_ELSEWHERE,
)
from app.models import Notification, User
from app.schemas import QUOTE_LENGTH
from tests.test_access import access
from tests.test_api import create_project
from tests.test_audit import actions
from tests.test_exports import archive_of, ask, run_export
from tests.test_notices import told
from tests.test_trash import restore
from tests.test_versions import given, text_document

TERMS = (
    b"Services agreement\n\n"
    b"The service fee is 100 EUR a month, paid on the first day of each month.\n\n"
    b"Deliveries arrive within five working days of an order.\n\n"
    b"Either party may end the agreement with three months' notice.\n"
)


@pytest.fixture
def room(client, session, services, account, make_account) -> SimpleNamespace:
    """The local owner's organisation with comments on and room for its members: an admin, two
    members and a viewer, and a member of another organisation; a project with the services
    agreement indexed, and another document beside it."""
    org = account.organization
    org.plan = "enterprise"
    session.commit()
    project = create_project(client, "Suppliers")["id"]
    return SimpleNamespace(
        org_id=org.id,
        owner=account,
        ann=make_account("ann@example.com", org, role="admin"),
        bob=make_account("bob@example.com", org, role="member"),
        cat=make_account("cat@example.com", org, role="member"),
        vic=make_account("vic@example.com", org, role="viewer"),
        eve=make_account("eve@globex.example"),
        project=project,
        terms=text_document(client, services, project, TERMS, "terms.txt")["id"],
        other=text_document(client, services, project, b"Prices for 2026.\n", "prices.txt")["id"],
    )


def start(client, document_id: int, text: str, **about):
    return client.post(f"/api/documents/{document_id}/threads", json={"text": text, **about})


def started(client, document_id: int, text: str, **about) -> dict:
    response = start(client, document_id, text, **about)
    assert response.status_code == 201, response.text
    return response.json()


def replied(client, thread_id: int, text: str) -> dict:
    response = client.post(f"/api/threads/{thread_id}/comments", json={"text": text})
    assert response.status_code == 201, response.text
    return response.json()


def threads(client, document_id: int) -> list[dict]:
    response = client.get(f"/api/documents/{document_id}/threads")
    assert response.status_code == 200, response.text
    return response.json()


def said(thread: dict) -> list[tuple[str | None, str]]:
    return [(comment["author"], comment["text"]) for comment in thread["comments"]]


def passages(client, project_id: int, words: str) -> list[dict]:
    response = client.get(f"/api/projects/{project_id}/search", params={"q": words})
    assert response.status_code == 200, response.text
    return response.json()["hits"]


def passage_of(client, project_id: int, document_id: int, words: str) -> dict:
    """The first passage of the document the project's search finds for the words."""
    return next(
        hit for hit in passages(client, project_id, words) if hit["document_id"] == document_id
    )


def blocks(client, document_id: int) -> list[dict]:
    return client.get(f"/api/documents/{document_id}/content").json()["blocks"]


def comment_rows(session, org_id: int) -> list[tuple[str, dict]]:
    return [
        (action, detail)
        for action, detail in actions(session, org_id)
        if action.startswith(("comment_", "thread_"))
    ]


def notices(session, user_id: int) -> list[tuple[str, str, str, str]]:
    return [
        (row.kind, row.title, row.body, row.link)
        for row in session.scalars(
            select(Notification).where(Notification.user_id == user_id).order_by(Notification.id)
        )
    ]


def test_everyone_who_sees_the_project_writes_in_a_thread_on_a_passage_and_resolves_it(
    client, session, room, act_as
):
    hit = passage_of(client, room.project, room.terms, "deliveries")
    thread = started(
        client, room.terms, "Is five days in the order form?", passage_id=hit["passage_id"]
    )
    assert thread["passage_id"] == hit["passage_id"]
    # The passage's own words are quoted when none were chosen, with the version they were in.
    assert thread["quote"].startswith("Services agreement")
    assert "Deliveries arrive within five working days" in thread["quote"]
    assert (thread["version"], thread["resolved_at"], thread["resolver"]) == (1, None, None)
    assert said(thread) == [("Owner", "Is five days in the order form?")]

    act_as(room.bob)
    thread = replied(client, thread["id"], "It says ten.")
    # A viewer writes too: a comment changes no document.
    act_as(room.vic)
    thread = replied(client, thread["id"], "Noted.")
    assert said(thread) == [
        ("Owner", "Is five days in the order form?"),
        ("Bob", "It says ten."),
        ("Vic", "Noted."),
    ]
    resolved = client.patch(f"/api/threads/{thread['id']}", json={"resolved": True}).json()
    assert resolved["resolver"] == "Vic" and resolved["resolved_at"] is not None
    # Resolving it again changes nothing, and is not recorded twice.
    assert client.patch(f"/api/threads/{thread['id']}", json={"resolved": True}).status_code == 200
    act_as(room.bob)
    # A resolved thread still takes a reply, and opens again.
    replied(client, thread["id"], "Ten, now in the form.")
    reopened = client.patch(f"/api/threads/{thread['id']}", json={"resolved": False}).json()
    assert (reopened["resolved_at"], reopened["resolver"]) == (None, None)

    [listed] = threads(client, room.terms)
    assert listed == reopened
    assert threads(client, room.other) == []
    # By the document's name and the thread, never a word of a comment.
    named = {"filename": "terms.txt", "thread": thread["id"]}
    assert comment_rows(session, room.org_id) == [
        ("comment_added", {**named, "reply": False}),
        ("comment_added", {**named, "reply": True}),
        ("comment_added", {**named, "reply": True}),
        ("thread_resolved", named),
        ("comment_added", {**named, "reply": True}),
        ("thread_reopened", named),
    ]


def test_a_thread_keeps_its_quote_once_the_document_is_read_again(client, services, room):
    hit = passage_of(client, room.project, room.terms, "fee")
    thread = started(client, room.terms, "Is this the 2026 fee?", passage_id=hit["passage_id"])
    quote = thread["quote"]

    given(client, services, room.terms, "terms.txt", b"The service fee is 250 EUR a month.\n")
    [kept] = threads(client, room.terms)
    # The passage went with the reading; the words it was about, and their version, stay.
    assert kept["passage_id"] is None
    assert (kept["quote"], kept["version"]) == (quote, 1)
    assert client.get(f"/api/documents/{room.terms}").json()["version"] == 2
    assert said(kept) == [("Owner", "Is this the 2026 fee?")]


def test_a_thread_is_about_the_words_chosen_a_block_s_passage_or_the_whole_document(client, room):
    texts = {block["text"]: block for block in blocks(client, room.terms)}
    notice = texts["Either party may end the agreement with three months' notice."]
    chosen = started(
        client, room.terms, "Three or six?", block=notice["ordinal"], quote="three  months'\nnotice"
    )
    # The words chosen, on one line; the passage holding the block.
    assert chosen["quote"] == "three months' notice"
    assert chosen["passage_id"] is not None and chosen["page"] == notice["page"]

    whole = started(client, room.terms, "Who signed this?")
    assert (whole["passage_id"], whole["quote"], whole["page"]) == (None, None, None)

    long = started(client, room.terms, "Long.", quote="word " * QUOTE_LENGTH)
    assert len(long["quote"]) <= QUOTE_LENGTH and long["quote"].endswith("word…")

    gone = start(client, room.terms, "Here?", block=999)
    assert (gone.status_code, gone.json()["detail"]) == (400, BLOCK_GONE)
    elsewhere = passage_of(client, room.project, room.other, "prices")
    wrong = start(client, room.terms, "Here?", passage_id=elsewhere["passage_id"])
    assert (wrong.status_code, wrong.json()["detail"]) == (400, PASSAGE_ELSEWHERE)
    blank = start(client, room.terms, "  \n ")
    assert (blank.status_code, blank.json()["detail"]) == (400, BLANK)
    assert start(client, room.terms, "x" * 5_001).status_code == 422
    # Postgres refuses a NUL, which the comment loses.
    assert said(started(client, room.terms, "A\x00B")) == [("Owner", "AB")]


def test_mentions_and_replies_tell_who_and_where_never_what(client, session, mailer, room, act_as):
    bob, ann, vic = room.bob.user, room.ann.user, room.vic.user
    thread = started(
        client, room.terms, f"<@{bob.id}> can you check the fee? <@{room.owner.user.id}>"
    )
    [comment] = thread["comments"]
    assert comment["mentions"] == [
        {"id": bob.id, "name": "Bob"},
        {"id": room.owner.user.id, "name": "Owner"},
    ]
    page = f"/projects/{room.project}/documents/{room.terms}?thread={thread['id']}"
    # Mentioning oneself tells nobody.
    assert notices(session, room.owner.user.id) == []
    assert notices(session, bob.id) == [
        (
            "mentioned",
            "You were mentioned in a comment",
            "Owner mentioned you in a comment on terms.txt in Suppliers in Local.",
            page,
        )
    ]
    [mail] = told(mailer, "bob@example.com")
    assert f"{page}\n" in mail.text
    assert "fee" not in mail.text and "fee" not in mail.html

    act_as(room.bob)
    replied(client, thread["id"], f"Asking <@{ann.id}>.")
    # Ann is mentioned; the owner, who wrote before, hears of the reply.
    assert [kind for kind, *_ in notices(session, ann.id)] == ["mentioned"]
    assert notices(session, room.owner.user.id) == [
        (
            "thread_replied",
            "A reply to a thread you commented in",
            "Bob replied in a thread you commented in, on terms.txt in Suppliers in Local.",
            page,
        )
    ]

    act_as(room.vic)
    replied(client, thread["id"], "It is 100 EUR.")
    # The thread's writers hear of it, each once; Ann was only mentioned in it.
    assert [kind for kind, *_ in notices(session, room.owner.user.id)] == ["thread_replied"] * 2
    assert [kind for kind, *_ in notices(session, bob.id)] == ["mentioned", "thread_replied"]
    assert [kind for kind, *_ in notices(session, ann.id)] == ["mentioned"]

    # An edit tells whom it newly mentions, and nobody already mentioned.
    act_as(room.bob)
    [_, asked, _] = threads(client, room.terms)[0]["comments"]
    changed = client.patch(
        f"/api/comments/{asked['id']}", json={"text": f"Asking <@{ann.id}> and <@{vic.id}>."}
    )
    assert changed.status_code == 200, changed.text
    edited = changed.json()["comments"][1]
    assert edited["edited_at"] is not None
    assert [kind for kind, *_ in notices(session, vic.id)] == ["mentioned"]
    assert [kind for kind, *_ in notices(session, ann.id)] == ["mentioned"]
    # Every notice of a comment names the document, never its words.
    bodies = [row.body for row in session.scalars(select(Notification))]
    assert bodies and not any("100 EUR" in body or "fee" in body for body in bodies)


def test_only_a_member_who_sees_the_project_is_mentioned_or_told(client, session, room, act_as):
    cat, eve = room.cat.user, room.eve.user
    listed = client.get(f"/api/projects/{room.project}/mentionable").json()
    assert [member["name"] for member in listed] == ["Ann", "Bob", "Cat", "Owner", "Vic"]

    assert access(client, room.project, "private", room.bob).status_code == 200
    listed = client.get(f"/api/projects/{room.project}/mentionable").json()
    # The owners and admins see every project.
    assert [member["name"] for member in listed] == ["Ann", "Bob", "Owner"]
    for stranger in (cat.id, eve.id, 999_999):
        refused = start(client, room.terms, f"<@{stranger}> look")
        assert (refused.status_code, refused.json()["detail"]) == (400, NOT_MENTIONABLE)

    # Bob is mentioned, then no longer sees the project: his reply's thread tells him nothing,
    # and an edit keeping his mention is not refused for it.
    thread = started(client, room.terms, f"<@{room.bob.user.id}> look")
    act_as(room.bob)
    replied(client, thread["id"], "Looking.")
    client.app.dependency_overrides.clear()
    assert access(client, room.project, "private", room.ann).status_code == 200
    replied(client, thread["id"], "Thanks.")
    assert [kind for kind, *_ in notices(session, room.bob.user.id)] == ["mentioned"]
    first = thread["comments"][0]["id"]
    kept = client.patch(f"/api/comments/{first}", json={"text": f"<@{room.bob.user.id}> look!"})
    assert kept.status_code == 200, kept.text


def test_a_comment_is_its_writer_s_to_change_and_theirs_or_an_admin_s_to_delete(
    client, session, room, act_as
):
    thread = started(client, room.terms, "First.")
    act_as(room.bob)
    thread = replied(client, thread["id"], "Second.")
    first, second = (comment["id"] for comment in thread["comments"])
    changed = client.patch(f"/api/comments/{second}", json={"text": "Second, changed."})
    assert said(changed.json())[1] == ("Bob", "Second, changed.")
    refused = client.patch(f"/api/comments/{first}", json={"text": "Mine now."})
    assert (refused.status_code, refused.json()["detail"]) == (403, NOT_YOURS)
    refused = client.delete(f"/api/comments/{first}")
    assert (refused.status_code, refused.json()["detail"]) == (403, NOT_YOURS_TO_DELETE)

    act_as(room.ann)
    assert client.delete(f"/api/comments/{second}").status_code == 204
    assert said(threads(client, room.terms)[0]) == [("Owner", "First.")]
    # The thread goes with its last comment.
    client.app.dependency_overrides.clear()
    assert client.delete(f"/api/comments/{first}").status_code == 204
    assert threads(client, room.terms) == []
    assert (
        client.post(f"/api/threads/{thread['id']}/comments", json={"text": "?"}).status_code == 404
    )

    deleted = [
        detail
        for action, detail in comment_rows(session, room.org_id)
        if action == "comment_deleted"
    ]
    assert deleted == [
        {"filename": "terms.txt", "thread": thread["id"], "author": room.bob.user.id},
        {"filename": "terms.txt", "thread": thread["id"], "author": room.owner.user.id},
    ]


def test_comments_go_with_their_document_into_the_trash_and_out_of_it(client, room):
    thread = started(client, room.terms, "Signed?")
    comment = thread["comments"][0]["id"]
    assert client.delete(f"/api/documents/{room.terms}").status_code == 204
    for method, path in (
        ("GET", f"/api/documents/{room.terms}/threads"),
        ("POST", f"/api/threads/{thread['id']}/comments"),
        ("PATCH", f"/api/comments/{comment}"),
    ):
        assert client.request(method, path, json={"text": "?"}).status_code == 404, path
    assert restore(client, "documents", room.terms).status_code == 200
    assert said(threads(client, room.terms)[0]) == [("Owner", "Signed?")]


def test_the_exports_hold_the_threads_and_the_writer_s_own_comments(
    client, session, services, room, act_as
):
    hit = passage_of(client, room.project, room.terms, "fee")
    thread = started(
        client, room.terms, f"<@{room.bob.user.id}> right?", passage_id=hit["passage_id"]
    )
    act_as(room.bob)
    replied(client, thread["id"], "Yes.")
    client.patch(f"/api/threads/{thread['id']}", json={"resolved": True})
    client.app.dependency_overrides.clear()

    made = ask(client, room.project).json()
    run_export(services)
    listed = {
        entry["filename"]: entry
        for entry in json.loads(archive_of(client, room.project, made["id"]).read("project.json"))[
            "documents"
        ]
    }
    [exported] = listed["terms.txt"]["threads"]
    assert exported["quote"] == thread["quote"] and exported["resolved_by"] == "Bob"
    assert [(c["author"], c["text"]) for c in exported["comments"]] == [
        ("Owner", "@Bob right?"),
        ("Bob", "Yes."),
    ]
    assert listed["prices.txt"]["threads"] == []

    [own] = client.get("/api/auth/account/export").json()["comments"]
    assert (own["project"], own["document"], own["text"]) == (
        "Suppliers",
        "terms.txt",
        "@Bob right?",
    )
    assert own["quote"] == thread["quote"]

    # An account gone leaves its comments without its name, and its mentions as someone's.
    session.execute(delete(User).where(User.id == room.bob.user.id))
    session.commit()
    [kept] = threads(client, room.terms)
    assert said(kept) == [("Owner", f"<@{room.bob.user.id}> right?"), (None, "Yes.")]
    assert kept["comments"][0]["mentions"] == [{"id": room.bob.user.id, "name": None}]
    assert kept["resolver"] is None
    [own] = client.get("/api/auth/account/export").json()["comments"]
    assert own["text"] == "@someone right?"
