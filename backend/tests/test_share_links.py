"""Share links (app/share_links.py): a member shows a document to whoever holds the link, with no
account, for a few days, with a password or not, and every link works only while what it rests
on holds: its maker's place, the plan, the organisation's switch, the flag and the document."""

from datetime import UTC, datetime, timedelta
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.config import get_settings
from app.export import _share_links, account_export
from app.flags import SHARE_LINKS
from app.indexing.pipeline import process_document
from app.models import Document, Organization, ShareLink, ShareView, UsageEvent
from app.share_links import (
    LINK_GONE,
    LINKS_OFF,
    NOT_READY,
    NOT_YOURS,
    PASSWORD_NEEDED,
    TOO_MANY_LINKS,
    VIEW_COOKIE,
    VIEW_OVER,
    WRONG_PASSWORD,
    prune_share_links,
)
from tests.conftest import APP_HEADERS
from tests.test_api import create_project, upload
from tests.test_audit import actions, rows
from tests.test_organizations import set_role
from tests.test_templates import turn_on

PASSWORD = "harbour lights"


@pytest.fixture(autouse=True)
def links_on(monkeypatch):
    turn_on(monkeypatch, SHARE_LINKS)


@pytest.fixture
def shared(client, services, sample_pdf) -> dict:
    """The sample contract, indexed, in a project of the local organisation."""
    project = create_project(client)
    document = upload(client, project["id"], sample_pdf)["documents"][0]
    process_document(document["id"], services)
    return {"project_id": project["id"], **document}


@pytest.fixture
def visitor(client):
    """A browser of someone with no account, who was sent the link."""
    with TestClient(client.app, headers=APP_HEADERS) as browser:
        yield browser


def make(client, document_id: int, **body) -> dict:
    response = client.post(f"/api/documents/{document_id}/share-links", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def token_of(made: dict) -> str:
    """The token the link's address carries in its fragment."""
    address, _, fragment = made["url"].partition("#")
    assert address.endswith("/shared")
    assert fragment.startswith("token=")
    return unquote(fragment.removeprefix("token="))


def opened(visitor, made: dict, password: str | None = None):
    body = {"token": token_of(made)}
    if password is not None:
        body["password"] = password
    return visitor.post("/api/shared", json=body)


def test_whoever_holds_a_link_reads_the_document(client, session, account, shared, visitor):
    made = make(client, shared["id"])
    assert made["password"] is False and made["views"] == 0
    assert made["made_by"] == account.user.name
    # Only the token's hash is kept, and the address is shown once.
    row = session.get(ShareLink, made["id"])
    assert row.token_hash != token_of(made) and len(row.token_hash) == 64
    listed = client.get(f"/api/documents/{shared['id']}/share-links").json()
    assert [link["id"] for link in listed] == [made["id"]]
    assert "url" not in listed[0]

    response = opened(visitor, made)
    assert response.status_code == 200, response.text
    document = response.json()
    assert (document["filename"], document["shared_by"]) == ("msa.pdf", account.user.name)
    assert document["organization"] == account.organization.name
    assert document["page_count"] == 2
    assert any("ninety (90) days" in block["text"] for block in document["content"]["blocks"])
    # The view's cookie goes to this link's pages and file alone, and to no script.
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{VIEW_COOKIE}=")
    assert f"Path=/api/shared/{made['id']}" in cookie
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie

    page = visitor.get(f"/api/shared/{made['id']}/pages/2.png")
    assert (page.status_code, page.headers["content-type"]) == (200, "image/png")
    original = visitor.get(f"/api/shared/{made['id']}/file")
    assert original.status_code == 200
    assert original.headers["content-disposition"].startswith("attachment")
    assert original.content.startswith(b"%PDF")

    listed = client.get(f"/api/documents/{shared['id']}/share-links").json()
    assert listed[0]["views"] == 1 and listed[0]["last_viewed_at"] is not None
    logged = [(action, detail) for action, detail in actions(session) if "share" in action]
    assert logged == [
        (
            "share_link_created",
            {"filename": "msa.pdf", "link": made["id"], "days": 7, "password": False},
        ),
        ("share_link_viewed", {"filename": "msa.pdf", "link": made["id"]}),
        ("share_link_downloaded", {"filename": "msa.pdf", "link": made["id"]}),
    ]


def test_the_visitor_is_nobody_and_the_maker_pays_for_a_page_drawn(
    client, session, account, shared, visitor, services
):
    made = make(client, shared["id"])
    opened(visitor, made)
    # A page not kept is drawn, as the members' is, and charged to the link's maker.
    document = session.get(Document, shared["id"])
    services.storage.delete([document.page_image_key(2)])
    assert visitor.get(f"/api/shared/{made['id']}/pages/2.png").status_code == 200
    drawn = session.scalars(select(UsageEvent).where(UsageEvent.kind == "render")).all()
    assert [(row.user_id, row.quantity) for row in drawn] == [(account.user.id, 1)]
    viewed = [row for row in rows(session) if row.action == "share_link_viewed"]
    assert [(row.user_id, row.ip) for row in viewed] == [(None, "testclient")]


def test_a_link_runs_the_days_its_maker_chose(client, shared):
    made = make(client, shared["id"], days=30)
    expires = datetime.fromisoformat(made["expires_at"])
    assert timedelta(days=29, hours=23) < expires - datetime.now(UTC) <= timedelta(days=30)
    assert make(client, shared["id"])["expires_at"] < made["expires_at"]
    for days in (0, 2, 31, 365):
        response = client.post(f"/api/documents/{shared['id']}/share-links", json={"days": days})
        assert response.status_code == 422, days
    short = client.post(f"/api/documents/{shared['id']}/share-links", json={"password": "short"})
    assert short.status_code == 422


def test_a_link_with_a_password_opens_with_it_alone(client, shared, visitor, monkeypatch):
    made = make(client, shared["id"], password=PASSWORD)
    assert made["password"] is True
    asked = opened(visitor, made)
    assert (asked.status_code, asked.json()["detail"]) == (401, PASSWORD_NEEDED)
    assert "set-cookie" not in asked.headers
    wrong = opened(visitor, made, "harbour light")
    assert (wrong.status_code, wrong.json()["detail"]) == (401, WRONG_PASSWORD)
    assert opened(visitor, made, PASSWORD).status_code == 200

    # Past the wrong passwords an hour, the link opens with none, the right one included.
    monkeypatch.setattr(get_settings(), "rate_shared_passwords", 2)
    opened(visitor, made, "one")
    paused = opened(visitor, made, PASSWORD)
    assert paused.status_code == 429
    assert paused.json()["detail"].startswith("This link was given the wrong password too often")
    assert int(paused.headers["Retry-After"]) > 0


def test_opening_links_is_held_to_a_rate_per_address(client, shared, visitor, monkeypatch):
    made = make(client, shared["id"])
    monkeypatch.setattr(get_settings(), "rate_shared_links", 2)
    assert [opened(visitor, made).status_code for _ in range(3)] == [200, 200, 429]
    unknown = visitor.post("/api/shared", json={"token": "nothing-like-it"})
    assert unknown.status_code == 429


def test_a_view_reaches_its_own_link_while_it_lasts(client, session, shared, visitor):
    first = make(client, shared["id"])
    second = make(client, shared["id"])
    assert visitor.get(f"/api/shared/{first['id']}/file").json()["detail"] == VIEW_OVER
    response = opened(visitor, first)
    view = response.cookies[VIEW_COOKIE]
    # Another link's pages are not this view's, whatever the browser sends.
    other = visitor.get(
        f"/api/shared/{second['id']}/file", headers={"Cookie": f"{VIEW_COOKIE}={view}"}
    )
    assert (other.status_code, other.json()["detail"]) == (401, VIEW_OVER)
    assert visitor.get(f"/api/shared/{first['id']}/file").status_code == 200
    session.execute(update(ShareView).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    session.commit()
    ended = visitor.get(f"/api/shared/{first['id']}/file")
    assert (ended.status_code, ended.json()["detail"]) == (401, VIEW_OVER)


def test_an_unknown_or_expired_link_says_nothing_but_that_it_is_gone(
    client, session, shared, visitor
):
    unknown = visitor.post("/api/shared", json={"token": "x" * 43})
    assert (unknown.status_code, unknown.json()["detail"]) == (404, LINK_GONE)
    made = make(client, shared["id"])
    session.execute(update(ShareLink).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    session.commit()
    assert opened(visitor, made).json()["detail"] == LINK_GONE


def stops(visitor, made: dict) -> bool:
    """Whether the link is refused as gone, at its opening and at a view made before."""
    return (
        opened(visitor, made).status_code == 404
        and visitor.get(f"/api/shared/{made['id']}/file").status_code == 404
    )


def test_a_link_stops_with_the_document_the_plan_the_switch_and_the_flag(
    client, session, account, shared, visitor, monkeypatch
):
    made = make(client, shared["id"])
    assert opened(visitor, made).status_code == 200

    assert client.delete(f"/api/documents/{shared['id']}").status_code == 204
    assert stops(visitor, made)
    restored = client.post(f"/api/trash/documents/{shared['id']}/restore")
    assert restored.status_code == 200, restored.text
    assert opened(visitor, made).status_code == 200

    organization = session.get(Organization, account.organization.id)
    organization.share_links = False
    session.commit()
    assert stops(visitor, made)
    organization.share_links = True
    organization.plan = "free"
    session.commit()
    assert stops(visitor, made)
    organization.plan = "enterprise"
    session.commit()
    assert opened(visitor, made).status_code == 200

    monkeypatch.undo()
    assert stops(visitor, made)


def test_a_link_works_as_its_maker_while_they_may_make_it(
    client, session, account, shared, visitor, make_account, act_as
):
    maker = make_account("mo@example.com", account.organization, role="member")
    act_as(maker)
    made = make(client, shared["id"])
    assert opened(visitor, made).status_code == 200

    set_role(session, account.organization.id, maker.user.id, "viewer")
    assert stops(visitor, made)
    set_role(session, account.organization.id, maker.user.id, "member")
    assert opened(visitor, made).status_code == 200

    # A project made private without its maker stops it.
    client.app.dependency_overrides.clear()
    private = client.put(
        f"/api/projects/{shared['project_id']}/access",
        json={"visibility": "private", "user_ids": []},
    )
    assert private.status_code == 200, private.text
    assert stops(visitor, made)
    client.put(
        f"/api/projects/{shared['project_id']}/access",
        json={"visibility": "private", "user_ids": [maker.user.id]},
    )
    assert opened(visitor, made).status_code == 200

    # And a maker leaving Emudoc, then gone from the organisation, takes it.
    maker.user.deletion_due_at = datetime.now(UTC) + timedelta(days=14)
    session.add(maker.user)
    session.commit()
    assert stops(visitor, made)
    assert client.delete(f"/api/organization/members/{maker.user.id}").status_code == 204
    assert session.scalar(select(ShareLink).where(ShareLink.id == made["id"])) is None


def test_making_a_link_is_held_to_the_role_the_plan_and_the_switch(
    client, session, account, shared, make_account, act_as, monkeypatch
):
    path = f"/api/documents/{shared['id']}/share-links"
    organization = session.get(Organization, account.organization.id)
    organization.plan = "free"
    session.commit()
    refused = client.post(path, json={})
    assert (refused.status_code, refused.json()["limit"]) == (402, "share_links")
    assert refused.json()["detail"].startswith("The Free plan does not include share links.")
    organization.plan = "enterprise"
    organization.share_links = False
    session.commit()
    off = client.post(path, json={})
    assert (off.status_code, off.json()["detail"]) == (403, LINKS_OFF)
    organization.share_links = True
    session.commit()
    act_as(make_account("vic@example.com", account.organization, role="viewer"))
    assert client.post(path, json={}).status_code == 403
    client.app.dependency_overrides.clear()

    monkeypatch.setattr("app.api.share_links.MAX_LINKS", 2)
    make(client, shared["id"])
    make(client, shared["id"])
    full = client.post(path, json={})
    assert (full.status_code, full.json()["detail"]) == (409, TOO_MANY_LINKS)

    note = client.post(
        f"/api/projects/{shared['project_id']}/documents",
        files=[("files", ("note.txt", b"Not indexed yet."))],
    ).json()["documents"][0]
    waiting = client.post(f"/api/documents/{note['id']}/share-links", json={})
    assert (waiting.status_code, waiting.json()["detail"]) == (409, NOT_READY)

    monkeypatch.undo()
    assert client.post(path, json={}).status_code == 404
    assert client.get(path).status_code == 404


def test_its_maker_or_an_owner_or_admin_revokes_a_link(
    client, session, account, shared, visitor, make_account, act_as
):
    maker = make_account("mo@example.com", account.organization, role="member")
    act_as(maker)
    made = make(client, shared["id"])
    other = make(client, shared["id"])
    act_as(make_account("max@example.com", account.organization, role="member"))
    refused = client.delete(f"/api/share-links/{made['id']}")
    assert (refused.status_code, refused.json()["detail"]) == (403, NOT_YOURS)

    act_as(maker)
    assert client.delete(f"/api/share-links/{made['id']}").status_code == 204
    assert opened(visitor, made).json()["detail"] == LINK_GONE
    assert client.delete(f"/api/share-links/{made['id']}").status_code == 404
    act_as(make_account("al@example.com", account.organization, role="admin"))
    assert client.delete(f"/api/share-links/{other['id']}").status_code == 204
    revoked = [detail for action, detail in actions(session) if action == "share_link_revoked"]
    assert revoked == [
        {"filename": "msa.pdf", "link": made["id"]},
        {"filename": "msa.pdf", "link": other["id"]},
    ]


def test_the_owners_and_admins_list_every_link_of_the_organisation(
    client, account, shared, make_account, act_as
):
    made = make(client, shared["id"], password=PASSWORD)
    listed = client.get("/api/organization/share-links").json()
    assert [(link["id"], link["filename"], link["project_name"]) for link in listed] == [
        (made["id"], "msa.pdf", "Contracts")
    ]
    assert listed[0]["password"] is True
    act_as(make_account("mo@example.com", account.organization, role="member"))
    assert client.get("/api/organization/share-links").status_code == 403


def test_owners_and_admins_turn_links_off_for_the_organisation(
    client, session, account, make_account, act_as
):
    assert client.get("/api/auth/me").json()["organization"]["share_links"] is True
    changed = client.patch("/api/organization", json={"share_links": False})
    assert changed.json()["organization"]["share_links"] is False
    settings = [detail for action, detail in actions(session) if action == "settings_changed"]
    assert settings == [{"setting": "share_links", "from": True, "to": False}]
    act_as(make_account("mo@example.com", account.organization, role="member"))
    assert client.patch("/api/organization", json={"share_links": True}).status_code == 403


def test_the_housekeeper_forgets_links_that_expired(client, session, shared, visitor):
    old = make(client, shared["id"])
    kept = make(client, shared["id"])
    opened(visitor, old)
    opened(visitor, kept)
    past = datetime.now(UTC) - timedelta(seconds=1)
    session.execute(update(ShareLink).where(ShareLink.id == old["id"]).values(expires_at=past))
    session.commit()
    with session.bind.begin() as connection:
        assert prune_share_links(connection) == 1
    session.expire_all()
    assert session.scalars(select(ShareLink.id)).all() == [kept["id"]]
    assert {view.link_id for view in session.scalars(select(ShareView))} == {kept["id"]}


def test_the_exports_list_the_links_never_their_addresses(client, session, account, shared):
    made = make(client, shared["id"], password=PASSWORD)
    exported = account_export(session, account.user)
    assert [(link.document, link.password) for link in exported.share_links] == [("msa.pdf", True)]
    assert token_of(made) not in exported.model_dump_json()
    listed = _share_links(session, shared["project_id"])
    assert [(link.document_id, link.made_by) for link in listed] == [
        (shared["id"], account.user.name)
    ]
