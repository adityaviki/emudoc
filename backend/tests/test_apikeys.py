"""API keys: made, rolled and revoked by an organisation's owners and admins, and acting as the
member who made them, within their role, the key's scope and the organisation's plan."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.api.deps import get_account
from app.apikeys import ELSEWHERE, KEY_UNKNOWN, READ_ONLY_KEY, ROLL_YOUR_OWN
from app.auth import prune_credentials
from app.billing import change_plan
from app.config import get_settings
from app.db import engine
from app.legal import TERMS_VERSION
from app.models import ApiKey, Membership, Organization, Project, User
from tests.test_api import create_project
from tests.test_audit import actions

SYNC = "Invoices sync"


def make_key(client, name: str = SYNC, scope: str = "read", days: int | None = None) -> dict:
    response = client.post(
        "/api/organization/keys", json={"name": name, "scope": scope, "days": days}
    )
    assert response.status_code == 201, response.text
    return response.json()


def program(client, secret: str) -> TestClient:
    """A program calling the API with the key: no cookie and none of the app's headers."""
    return TestClient(client.app, headers={"Authorization": f"Bearer {secret}"})


def keys(client) -> list[dict]:
    response = client.get("/api/organization/keys")
    assert response.status_code == 200, response.text
    return response.json()


def stored(session, key_id: int) -> ApiKey | None:
    session.expire_all()
    return session.get(ApiKey, key_id)


def test_a_key_acts_as_the_member_who_made_it(client, session, account, monkeypatch):
    project = create_project(client)
    made = make_key(client, scope="write")
    assert made["key"].startswith("emk_") and len(made["key"]) == 47
    assert made["hint"] == made["key"][:8]
    assert (made["scope"], made["made_by"], made["user_id"]) == ("write", "Owner", account.user.id)
    assert made["last_used_at"] is None and made["expires_at"] is None
    # The key itself is shown once, as it is made.
    listed = keys(client)
    assert [key["name"] for key in listed] == [SYNC] and "key" not in listed[0]

    # The program gets in by its key alone, without the local account to fall back on.
    monkeypatch.setattr(get_settings(), "local_account", False)
    api = program(client, made["key"])
    assert [p["id"] for p in api.get("/api/projects").json()] == [project["id"]]
    # It needs none of the app's headers to change something, as a page elsewhere would.
    uploaded = api.post(
        f"/api/projects/{project['id']}/documents",
        files=[("files", ("note.txt", b"A delivery note."))],
    )
    assert uploaded.status_code == 201, uploaded.text
    assert stored(session, made["id"]).last_used_at is not None

    # What it did is its member's, with the key named as it was.
    detail = dict(actions(session, account.organization.id))["document_uploaded"]
    assert detail["api_key"] == {"id": made["id"], "name": SYNC}
    assert ("api_key_created", {"name": SYNC, "scope": "write", "days": None}) in actions(
        session, account.organization.id
    )


def test_a_read_key_only_reads(client):
    project = create_project(client)
    api = program(client, make_key(client)["key"])
    assert api.get(f"/api/projects/{project['id']}").status_code == 200
    refused = api.post("/api/projects", json={"name": "Mine"})
    assert (refused.status_code, refused.json()["detail"]) == (403, READ_ONLY_KEY)
    assert api.delete(f"/api/projects/{project['id']}").status_code == 403


def test_a_key_reaches_what_the_projects_hold_and_nothing_else(client, session, account):
    api = program(client, make_key(client, scope="write")["key"])
    for method, path in (
        ("GET", "/api/auth/me"),
        ("GET", "/api/organization/keys"),
        ("POST", "/api/organization/keys"),
        ("GET", "/api/organization/members"),
        ("GET", "/api/audit"),
        ("PATCH", "/api/organization"),
        ("POST", "/api/organization/invitations"),
    ):
        refused = api.request(method, path, json={"name": "Another", "email": "ivy@example.com"})
        assert (refused.status_code, refused.json()["detail"]) == (403, ELSEWHERE), path
    # No key made another.
    assert len(keys(client)) == 1
    assert session.scalars(select(ApiKey.name)).all() == [SYNC]
    # The status is read, for what one upload may carry, which a program sends its files within.
    status = api.get("/api/status")
    assert status.status_code == 200 and status.json()["uploads"]["files"] == 100


def test_an_unknown_expired_rolled_or_revoked_key_is_refused(client, session):
    project = create_project(client)
    path = f"/api/projects/{project['id']}"
    # Without a key it knows, a request is refused, never taken for the local account's.
    unknown = program(client, "emk_" + "x" * 43).get(path)
    assert (unknown.status_code, unknown.json()["detail"]) == (401, KEY_UNKNOWN)
    assert unknown.headers["www-authenticate"] == "Bearer"
    assert program(client, "").get(path).status_code == 401
    # Another scheme is no key: a browser sends Basic credentials by itself.
    basic = client.get(path, headers={"Authorization": "Basic dXNlcjpwYXNz"})
    assert basic.status_code == 200

    week = make_key(client, "A week", days=7)
    assert stored(session, week["id"]).expires_at > datetime.now(UTC) + timedelta(days=6)
    assert program(client, week["key"]).get(path).status_code == 200
    with engine.begin() as connection:
        connection.execute(text("UPDATE api_keys SET expires_at = '2026-09-01T12:00:00Z'"))
    ran_out = program(client, week["key"]).get(path)
    assert ran_out.status_code == 401
    assert ran_out.json()["detail"].startswith("This API key ran out on 1 September 2026.")
    assert keys(client)[0]["expired"] is True
    # A month after it ran out, the housekeeper forgets it.
    with engine.begin() as connection:
        assert prune_credentials(connection) == 1
    assert keys(client) == []

    made = make_key(client)
    rolled = client.post(f"/api/organization/keys/{made['id']}/roll")
    assert rolled.status_code == 200, rolled.text
    assert rolled.json()["key"] != made["key"] and rolled.json()["rolled_at"] is not None
    assert program(client, made["key"]).get(path).status_code == 401
    assert program(client, rolled.json()["key"]).get(path).status_code == 200

    assert client.delete(f"/api/organization/keys/{made['id']}").status_code == 204
    assert program(client, rolled.json()["key"]).get(path).status_code == 401
    assert client.delete(f"/api/organization/keys/{made['id']}").status_code == 404
    org_id = session.scalar(select(Organization.id))
    assert [name for name, _ in actions(session, org_id) if name.startswith("api_key")] == [
        "api_key_created",
        "api_key_created",
        "api_key_rolled",
        "api_key_revoked",
    ]


def test_owners_and_admins_manage_keys_and_only_its_member_rolls_one(
    client, account, make_account, act_as
):
    made = make_key(client)
    act_as(make_account("max@example.com", account.organization, role="member"))
    assert client.get("/api/organization/keys").status_code == 403
    assert client.post("/api/organization/keys", json={"name": "Mine"}).status_code == 403
    assert client.delete(f"/api/organization/keys/{made['id']}").status_code == 403

    act_as(make_account("al@example.com", account.organization, role="admin"))
    assert [key["made_by"] for key in keys(client)] == ["Owner"]
    # Whoever holds a rolled secret acts as the key's member, so only they roll it.
    refused = client.post(f"/api/organization/keys/{made['id']}/roll")
    assert (refused.status_code, refused.json()["detail"]) == (403, ROLL_YOUR_OWN)
    assert client.delete(f"/api/organization/keys/{made['id']}").status_code == 204
    assert client.post("/api/organization/keys", json={"name": " "}).status_code == 400


def test_a_key_does_no_more_than_its_member(client, session, account, make_account, act_as):
    shared = create_project(client, "Shared")
    private = create_project(client, "Board")
    al = make_account("al@example.com", account.organization, role="admin")
    act_as(al)
    made = make_key(client, scope="write")
    client.app.dependency_overrides.pop(get_account)
    elsewhere = make_account("eve@globex.com")
    theirs = Project(org_id=elsewhere.organization.id, name="Globex files")
    session.add(theirs)
    session.commit()
    api = program(client, made["key"])
    assert {p["name"] for p in api.get("/api/projects").json()} == {"Shared", "Board"}
    assert api.get(f"/api/projects/{theirs.id}").status_code == 404

    # As a member, Al no longer sees the project made private without him.
    client.put(
        f"/api/projects/{private['id']}/access",
        json={"visibility": "private", "user_ids": [account.user.id]},
    )
    role = session.scalar(select(Membership).where(Membership.user_id == al.user.id))
    role.role = "member"
    session.commit()
    assert api.get(f"/api/projects/{private['id']}").status_code == 404
    # As a viewer, the write key changes nothing.
    role.role = "viewer"
    session.commit()
    viewer = api.patch(f"/api/projects/{shared['id']}", json={"name": "Ours"})
    assert viewer.status_code == 403
    assert viewer.json()["detail"].startswith("Your role in this organisation is viewer")

    # Removed from the organisation, his key goes with his place in it.
    session.delete(role)
    session.commit()
    assert stored(session, made["id"]) is None
    assert api.get("/api/projects").status_code == 401


def test_a_key_is_held_with_its_member(client, session, account, make_account, act_as):
    """A key whose member has yet to accept the current terms, or is held by the
    organisation's two-factor requirement, is refused what they would be."""
    create_project(client)
    al = make_account("al@example.com", account.organization, role="admin")
    act_as(al)
    api = program(client, make_key(client)["key"])
    client.app.dependency_overrides.pop(get_account)
    assert api.get("/api/projects").status_code == 200
    user = session.get(User, al.user.id)
    user.terms_version = "2020-01-01"
    session.commit()
    held = api.get("/api/projects")
    assert held.status_code == 403
    assert held.json()["detail"].startswith("Accept the terms of service")

    user.terms_version = TERMS_VERSION
    session.get(Organization, account.organization.id).mfa_required = True
    session.commit()
    held = api.get("/api/projects")
    assert held.status_code == 403
    assert "sign in with two-factor" in held.json()["detail"]


def test_the_plan_must_include_the_api(client, session, make_account, act_as):
    ada = make_account("ada@acme.com")
    act_as(ada)
    refused = client.post("/api/organization/keys", json={"name": SYNC})
    assert refused.status_code == 402
    assert refused.json() == {
        "detail": "The Free plan does not include the API. Upgrade your plan to make and use "
        "API keys.",
        "limit": "api_access",
    }
    assert client.get("/api/auth/me").json()["organization"]["allowance"]["api_access"] is False

    organization = session.get(Organization, ada.organization.id)
    change_plan(session, organization, "pro")
    session.commit()
    made = make_key(client)
    client.app.dependency_overrides.pop(get_account)
    api = program(client, made["key"])
    assert api.get("/api/projects").status_code == 200

    # Back on Free, the key is kept but refused until the plan includes the API again.
    change_plan(session, organization, "free")
    session.commit()
    lapsed = api.get("/api/projects")
    assert (lapsed.status_code, lapsed.json()["limit"]) == (402, "api_access")
    act_as(ada)
    assert [key["id"] for key in keys(client)] == [made["id"]]
    assert client.post(f"/api/organization/keys/{made['id']}/roll").status_code == 402
    assert client.delete(f"/api/organization/keys/{made['id']}").status_code == 204


@pytest.mark.usefixtures("one_minute")
def test_a_key_is_counted_by_itself(client, session, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_api_requests", 2)
    api = program(client, make_key(client)["key"])
    assert [api.get("/api/projects").status_code for _ in range(2)] == [200, 200]
    refused = api.get("/api/projects")
    assert refused.status_code == 429
    assert "requests with this API key in the last minute" in refused.json()["detail"]
    assert int(refused.headers["retry-after"]) >= 1
    # The page's own requests, by the member's session, are not the key's.
    assert client.get("/api/projects").status_code == 200


@pytest.mark.parametrize("days", [0, 367])
def test_a_key_runs_a_year_at_most(client, days):
    response = client.post("/api/organization/keys", json={"name": SYNC, "days": days})
    assert response.status_code == 422
