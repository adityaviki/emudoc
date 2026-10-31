"""A project's activity (app/activity.py): what was done in it lately, newest first, from its
rows of the audit log, by names alone, read by everyone who sees the project, leading to what is
still there to open."""

from sqlalchemy import delete

from app.audit import audit_event
from app.models import AuditEvent, User
from tests.test_comments import room, started  # noqa: F401 (a fixture)
from tests.test_trash import restore
from tests.test_versions import given, text_document


def activity(client, project_id: int, **params) -> dict:
    response = client.get(f"/api/projects/{project_id}/activity", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def shown(page: dict) -> list[tuple]:
    return [(event["action"], event["actor"], event["filename"]) for event in page["events"]]


def test_the_activity_lists_what_was_done_in_the_project_newest_first(
    client,
    session,
    services,
    room,  # noqa: F811
    act_as,
):
    thread = started(client, room.terms, "Signed?")
    given(client, services, room.terms, "terms.txt", b"The service fee is 250 EUR a month.\n")
    client.patch(f"/api/threads/{thread['id']}", json={"resolved": True})
    client.get(f"/api/documents/{room.other}/file")
    client.patch(f"/api/projects/{room.project}", json={"name": "Vendors"})
    assert client.delete(f"/api/documents/{room.other}").status_code == 204

    # A viewer reads it, as everyone who sees the project does.
    act_as(room.vic)
    page = activity(client, room.project)
    # A download and a rename are the audit log's alone.
    assert shown(page) == [
        ("document_deleted", "Owner", "prices.txt"),
        ("thread_resolved", "Owner", "terms.txt"),
        ("document_version_uploaded", "Owner", "terms.txt"),
        ("comment_added", "Owner", "terms.txt"),
        ("document_uploaded", "Owner", "prices.txt"),
        ("document_uploaded", "Owner", "terms.txt"),
    ]
    deleted, resolved, version, comment, *_ = page["events"]
    # What is still there leads to it: the thread and its document; the deleted one does not.
    assert (deleted["document_id"], resolved["thread_id"]) == (None, thread["id"])
    assert (comment["document_id"], comment["thread_id"], comment["reply"]) == (
        room.terms,
        thread["id"],
        False,
    )
    assert version["document_id"] == room.terms
    assert page["next_before"] is None

    client.app.dependency_overrides.clear()
    assert restore(client, "documents", room.other).status_code == 200
    assert activity(client, room.project)["events"][0]["document_id"] == room.other
    assert client.delete(f"/api/comments/{thread['comments'][0]['id']}").status_code == 204
    events = activity(client, room.project)["events"]
    # The thread went with its last comment; the items stay, leading to the document alone.
    commented = next(event for event in events if event["action"] == "comment_added")
    assert (commented["thread_id"], commented["document_id"]) == (None, room.terms)
    assert "comment_deleted" not in {event["action"] for event in events}


def test_the_activity_names_reviews_automations_and_what_brought_a_document(
    client,
    session,
    room,  # noqa: F811
):
    def row(action, detail, user_id=None, target=("document", room.terms)):
        audit_event(
            session,
            action,
            org_id=room.org_id,
            user_id=user_id,
            project_id=room.project,
            target=target,
            detail=detail,
        )

    named = {"filename": "terms.txt"}
    row("document_review_changed", {**named, "from": None, "to": "disputed"}, room.ann.user.id)
    row("document_assigned", {**named, "from": None, "to": room.bob.user.id}, room.ann.user.id)
    row("document_assigned", {**named, "from": room.bob.user.id, "to": None}, room.ann.user.id)
    row("document_review_changed", {**named, "from": None, "to": "to_review", "automation": "New"})
    row("document_uploaded", {**named, "size_bytes": 9, "by_email": True})
    row("document_uploaded", {**named, "size_bytes": 9, "by_connector": "google"}, 1)
    row("folder_deleted", {"name": "Old", "documents": 3}, room.bob.user.id, ("folder", 1))
    row("question_set_run", {"name": "Daily", "questions": 2}, None, ("question_report", 999))
    row("cross_checks_run", {"names": ["Totals"], "report": False}, None, ("workflow_run", 998))
    row("draft_created", {"kind": "report", "documents": 1}, 1, ("draft", 997))
    # What the activity leaves out: the owners' and admins' affairs, and another project's.
    row("settings_changed", {"chat_days": 30}, 1, None)
    audit_event(session, "comment_added", org_id=room.org_id, project_id=None, detail=named)
    session.commit()

    events = {event["id"]: event for event in activity(client, room.project, limit=200)["events"]}
    by_action = [(e["action"], e["actor"]) for e in sorted(events.values(), key=lambda e: e["id"])]
    assert by_action[-10:] == [
        ("document_review_changed", "Ann"),
        ("document_assigned", "Ann"),
        ("document_assigned", "Ann"),
        ("document_review_changed", None),
        ("document_uploaded", None),
        ("document_uploaded", "Owner"),
        ("folder_deleted", "Bob"),
        ("question_set_run", None),
        ("cross_checks_run", None),
        ("draft_created", "Owner"),
    ]
    picked = sorted(events.values(), key=lambda e: e["id"])[-10:]
    disputed, assigned, unassigned, automated, mailed, synced, folder, asked, run, draft = picked
    assert disputed["review"] == "disputed"
    assert (assigned["assignee"], unassigned["assignee"]) == ("Bob", None)
    assert (automated["automation"], automated["review"]) == ("New", "to_review")
    assert (mailed["via"], synced["via"]) == ("email", "google")
    assert (folder["folder"], folder["documents"], folder["filename"]) == ("Old", 3, None)
    assert (asked["name"], asked["questions"], run["names"], draft["kind"]) == (
        "Daily",
        2,
        ["Totals"],
        "report",
    )
    # A run, a report or a draft no longer there leads nowhere.
    assert (asked["target_id"], run["target_id"], draft["target_id"]) == (None, None, None)

    # An account gone is nobody's name, and the one assigned is no one's.
    session.execute(delete(User).where(User.id == room.bob.user.id))
    session.commit()
    events = {event["id"]: event for event in activity(client, room.project, limit=200)["events"]}
    assert (events[folder["id"]]["actor"], events[assigned["id"]]["assignee"]) == (None, None)


def test_the_activity_comes_a_page_at_a_time(client, session, services, room):  # noqa: F811
    for number in range(3):
        text_document(client, services, room.project, f"Note {number}.\n".encode(), f"{number}.txt")
    first = activity(client, room.project, limit=2)
    assert [event["filename"] for event in first["events"]] == ["2.txt", "1.txt"]
    second = activity(client, room.project, limit=2, before=first["next_before"])
    assert [event["filename"] for event in second["events"]] == ["0.txt", "prices.txt"]
    rest = activity(client, room.project, limit=2, before=second["next_before"])
    assert [event["filename"] for event in rest["events"]] == ["terms.txt"]
    assert rest["next_before"] is None
    # A row pruned since the page before has nothing older to show.
    session.execute(delete(AuditEvent).where(AuditEvent.id == second["next_before"]))
    session.commit()
    gone = activity(client, room.project, before=second["next_before"])
    assert gone == {"events": [], "next_before": None}
