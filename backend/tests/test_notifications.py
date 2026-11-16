"""The notification centre: each notice told in the app beside its mail, each way as its member
chose for its kind; listed newest first, the workspace's alone, a page at a time, with how many
are unread; marked read, one or all, of the member's own alone; and gone with the membership and
after NOTIFICATION_DAYS."""

import pytest
from sqlalchemy import select, text

from app.accounts import Account
from app.config import get_settings
from app.db import engine
from app.mail import Content, Link
from app.models import NOTICE_KINDS, PAST_NOTICE_KINDS, Notification, User
from app.notices import NOTIFICATION_DAYS, prune_notifications, tell
from app.schemas import NoticeKind, PastNoticeKind
from tests.test_notices import told
from tests.test_organizations import join, remove, team  # noqa: F401

APP = "https://emudoc.example.com"


@pytest.fixture
def ann(make_account, act_as) -> Account:
    """The API acts as Ann, the owner of Globex, a fresh organisation."""
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


def test_a_notice_is_told_in_the_app_beside_its_mail(client, session, mailer, ann):
    told_in_app = tell(
        session, ann.user, ann.organization.id, "checks_run", news("Checked"), "/projects/1", APP
    )
    assert told_in_app
    session.commit()

    page = centre(client)
    (mail,) = told(mailer)
    assert page["unread"] == 1 and page["next_before"] is None
    (notice,) = page["notifications"]
    assert notice.pop("created_at") and notice.pop("id")
    # Its words are the mail's but the greeting and the link, which leads to the same page.
    assert notice == {
        "kind": "checks_run",
        "title": mail.subject,
        "body": "Said.",
        "link": "/projects/1",
        "read_at": None,
    }
    assert f"{APP}/projects/1" in mail.text


def test_each_way_of_each_kind_is_chosen_apart(client, session, mailer, ann):
    user, org_id = ann.user, ann.organization.id
    user.choose("checks_run", "app", False)
    assert tell(session, user, org_id, "checks_run", news("By email"), "/projects/1", APP)
    user.choose("upload_indexed", "email", False)
    assert tell(session, user, org_id, "upload_indexed", news("In the app"), "/projects/1", APP)
    user.choose("upload_indexed", "app", False)
    assert not tell(session, user, org_id, "upload_indexed", news("Untold"), "/projects/1", APP)
    session.commit()

    assert titles(centre(client)) == ["In the app"]
    assert [message.subject for message in told(mailer)] == ["By email"]


def test_the_centre_holds_the_workspaces_notices_newest_first(client, session, ann, make_account):
    # A database that holds another organisation, as development's does, keeps its notices apart.
    other = make_account("ivy@initech.example").organization
    join(session, other.id, ann.user.id, "admin")
    for number, org_id in enumerate([ann.organization.id, other.id, ann.organization.id] * 2):
        tell(session, ann.user, org_id, "upload_indexed", news(f"Notice {number}"), "/", APP)
    session.commit()

    first = centre(client, limit=2)
    assert titles(first) == ["Notice 5", "Notice 3"]
    assert first["unread"] == 4
    rest = centre(client, limit=2, before=first["next_before"])
    assert titles(rest) == ["Notice 2", "Notice 0"] and rest["next_before"] is None
    client.post("/api/notifications/read", json={})
    assert centre(client)["unread"] == 0
    unread = select(Notification.id).where(Notification.read_at.is_(None))
    assert len(session.scalars(unread).all()) == 2


def test_notices_are_marked_read_one_or_all_and_only_the_members_own(
    client, session, ann, make_account
):
    bob = make_account("bob@globex.example", ann.organization, "member")
    for user in (ann.user, ann.user, bob.user):
        tell(session, user, ann.organization.id, "checks_run", news(user.name), "/", APP)
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


def test_a_member_removed_loses_the_workspaces_notices(client, session, request):
    # Ada and Bob, a member of her workspace, each signed in (`team`).
    ada, bob, bob_client = request.getfixturevalue("team")
    workspace = ada["organization"]["id"]
    tell(session, session.get(User, bob["id"]), workspace, "checks_run", news("About it"), "/", APP)
    session.commit()
    assert titles(centre(bob_client)) == ["About it"]

    assert remove(client, bob["id"]).status_code == 204
    assert session.scalars(select(Notification.id)).all() == []
    join(session, workspace, bob["id"])
    assert titles(centre(bob_client)) == []


def test_the_housekeeper_removes_notices_older_than_their_days(client, session, ann):
    for subject in ("Old", "New"):
        tell(session, ann.user, ann.organization.id, "checks_run", news(subject), "/", APP)
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
    assert PastNoticeKind.__args__ == PAST_NOTICE_KINDS


def test_a_notice_of_a_kind_no_longer_told_still_reads(client, session, ann):
    # A member mentioned in a comment was told until comments went (S.6): the notice stays.
    session.add(
        Notification(
            user_id=ann.user.id,
            org_id=ann.organization.id,
            kind="mentioned",
            title="You were mentioned in a comment",
            body="Bob mentioned you in a comment on terms.txt in Suppliers in Globex.",
            link="/projects/1/documents/2?thread=3",
        )
    )
    session.commit()
    (notice,) = centre(client)["notifications"]
    assert (notice["kind"], notice["title"]) == ("mentioned", "You were mentioned in a comment")
