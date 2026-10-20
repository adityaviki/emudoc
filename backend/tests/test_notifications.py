"""The notification centre: each notice told in the app beside its mail, each way as its member
chose for its kind; listed newest first across the organisations they belong to, a page at a
time, with how many are unread, even to a tab left in another organisation; marked read, one or
all, of the member's own alone; and gone with the membership and after NOTIFICATION_DAYS."""

from dataclasses import replace

import pytest
from sqlalchemy import select, text

from app.accounts import Account
from app.api.deps import ORGANIZATION_HEADER
from app.config import get_settings
from app.db import engine
from app.entitlements import MONTHS, PLANS, month_of
from app.mail import Content, Link
from app.models import NOTICE_KINDS, Notification, User
from app.notices import NOTIFICATION_DAYS, prune_notifications, tell
from app.schemas import NoticeKind
from tests.test_api import create_project
from tests.test_chats import new_chat
from tests.test_notices import told
from tests.test_organizations import join, remove, switch, team  # noqa: F401

APP = "https://emudoc.example.com"


@pytest.fixture
def ann(make_account, act_as) -> Account:
    """The API acts as Ann, the owner of Globex, a fresh organisation on the free plan."""
    account = make_account("ann@globex.example")
    act_as(account)
    return account


@pytest.fixture(autouse=True)
def at_app(monkeypatch):
    monkeypatch.setattr(get_settings(), "app_url", APP)


def news(subject: str = "Your documents are indexed") -> Content:
    return Content(subject, ("Hi Ann,", "Said.", Link(f"{APP}/projects/1", "Open")), "Said.")


def centre(client, **params) -> dict:
    response = client.get("/api/notifications", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def titles(page: dict) -> list[str]:
    return [notice["title"] for notice in page["notifications"]]


def test_a_notice_is_told_in_the_app_beside_its_mail(client, session, mailer, ann, monkeypatch):
    monkeypatch.setitem(PLANS, "free", replace(PLANS["free"], questions_per_month=2))
    project = create_project(client)
    for _ in range(2):
        chat = new_chat(client, project["id"])
        client.post(f"/api/chats/{chat['id']}/messages", json={"content": "Price?"})
    month, following = (MONTHS[moment.month - 1] for moment in month_of())

    page = centre(client)
    (mail,) = told(mailer)
    assert page["unread"] == 1 and page["next_before"] is None
    (notice,) = page["notifications"]
    assert notice.pop("created_at") and notice.pop("id")
    # Its words are the mail's but the greeting and the link, which leads to the same page.
    assert notice == {
        "kind": "quota",
        "title": mail.subject,
        "body": f"Globex has used all the 2 questions it may ask on Emudoc in {month}. No more "
        f"questions are answered until 1 {following}, when the count starts afresh.",
        "link": "/organization/usage",
        "org_id": ann.organization.id,
        "organization": "Globex",
        "read_at": None,
    }
    assert f"{APP}/organization/usage?organization={ann.organization.id}" in mail.text


def test_each_way_of_each_kind_is_chosen_apart(client, session, mailer, ann):
    user, org_id = ann.user, ann.organization.id
    user.choose("quota", "app", False)
    assert tell(session, user, org_id, "quota", news("By email"), "/organization/usage", APP)
    user.choose("upload_indexed", "email", False)
    assert tell(session, user, org_id, "upload_indexed", news("In the app"), "/projects/1", APP)
    user.choose("upload_indexed", "app", False)
    assert not tell(session, user, org_id, "upload_indexed", news("Untold"), "/projects/1", APP)
    session.commit()

    assert titles(centre(client)) == ["In the app"]
    assert [message.subject for message in told(mailer)] == ["By email"]


def test_the_centre_holds_every_organisation_newest_first_even_for_a_tab_left_in_another(
    client, session, ann, make_account
):
    other = make_account("ivy@initech.example").organization
    join(session, other.id, ann.user.id, "admin")
    for number, org_id in enumerate([ann.organization.id, other.id, ann.organization.id]):
        tell(session, ann.user, org_id, "upload_indexed", news(f"Notice {number}"), "/", APP)
    session.commit()

    first = centre(client, limit=2)
    assert titles(first) == ["Notice 2", "Notice 1"]
    assert [notice["organization"] for notice in first["notifications"]] == ["Globex", "Initech"]
    assert first["unread"] == 3
    rest = centre(client, limit=2, before=first["next_before"])
    assert titles(rest) == ["Notice 0"] and rest["next_before"] is None

    # A tab that still shows another organisation asks for its bell every minute, and is
    # answered, where a change it made would be refused.
    stale = {ORGANIZATION_HEADER: str(other.id)}
    assert client.get("/api/projects", headers=stale).status_code == 409
    assert client.get("/api/notifications", headers=stale).status_code == 200


def test_notices_are_marked_read_one_or_all_and_only_the_members_own(
    client, session, ann, make_account
):
    bob = make_account("bob@globex.example", ann.organization, "member")
    for user in (ann.user, ann.user, bob.user):
        tell(session, user, ann.organization.id, "quota", news(user.name), "/", APP)
    session.commit()
    ids = [notice["id"] for notice in centre(client)["notifications"]]
    bobs = session.scalar(select(Notification.id).where(Notification.user_id == bob.user.id))

    response = client.post("/api/notifications/read", json={"ids": [ids[0], bobs]})
    assert response.status_code == 204
    page = centre(client)
    assert page["unread"] == 1
    assert [notice["read_at"] is None for notice in page["notifications"]] == [False, True]
    assert session.get(Notification, bobs).read_at is None

    client.post("/api/notifications/read", json={})
    assert centre(client)["unread"] == 0
    session.expire_all()
    assert session.get(Notification, bobs).read_at is None


def test_a_member_who_leaves_or_is_removed_loses_the_organisations_notices(
    client, session, request
):
    # Ada and Bob, a member of her organisation, each signed in (`team`).
    ada, bob, bob_client = request.getfixturevalue("team")
    shared, own = ada["organization"]["id"], bob["organization"]["id"]

    def tell_bob() -> None:
        user = session.get(User, bob["id"])
        for org_id in (shared, own):
            tell(session, user, org_id, "quota", news(f"About {org_id}"), "/", APP)
        session.commit()

    tell_bob()
    switch(bob_client, shared)
    assert bob_client.post("/api/organization/leave").status_code == 200
    assert titles(centre(bob_client)) == [f"About {own}"]

    join(session, shared, bob["id"])
    tell_bob()
    assert remove(client, bob["id"]).status_code == 204
    assert titles(centre(bob_client)) == [f"About {own}", f"About {own}"]


def test_the_housekeeper_removes_notices_older_than_their_days(client, session, ann):
    for subject in ("Old", "New"):
        tell(session, ann.user, ann.organization.id, "quota", news(subject), "/", APP)
    session.commit()
    session.execute(
        text(
            "UPDATE notifications SET created_at = now() - make_interval(days => :days) "
            "WHERE title = 'Old'"
        ),
        {"days": NOTIFICATION_DAYS + 1},
    )
    session.commit()
    with engine.begin() as connection:
        assert prune_notifications(connection) == 1
    assert titles(centre(client)) == ["New"]


def test_the_centre_is_for_a_signed_in_account_that_may_use_the_app(client, signed_in_only):
    assert client.get("/api/notifications").status_code == 401
    assert client.post("/api/notifications/read", json={}).status_code == 401


def test_the_kinds_the_api_names_are_the_models():
    assert NoticeKind.__args__ == NOTICE_KINDS
