"""Deleting an organisation: an owner asks, confirmed by the password, once no subscription
renews; every member is told; until the day the organisation is held as it stands, which any
owner undoes by keeping it; then the housekeeper deletes it with its rows and files, gives a
member it was the only organisation of one of their own, and leaves a record by which a restore
that brings it back deletes it again."""

import logging
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select, update

from app.api.auth import WRONG_PASSWORD
from app.api.organizations import OWNERS_DELETE
from app.billing import renewal_refusal, renews
from app.config import get_settings
from app.maintenance import housekeeping
from app.models import (
    AuditEvent,
    Document,
    Invitation,
    Membership,
    Organization,
    Project,
    User,
    UserSession,
)
from app.retention import (
    DELETION_GRACE,
    LOCAL_ORGANISATION,
    ORGANIZATION_ERASURES,
    day,
    held_for_deletion,
    organization_erasure_key,
    purge_due_organizations,
)
from app.storage import organization_prefix
from tests.test_api import create_project, upload
from tests.test_audit import actions
from tests.test_auth import PASSWORD, another_browser, me, signup
from tests.test_billing import STRIPE_KEY
from tests.test_organizations import join
from tests.test_retention import backup, restore

DELETED = "An organisation you belong to is set to be deleted"
KEPT = "An organisation you belong to is kept"


@pytest.fixture
def crew(client, session, signed_in_only):
    """Ada, signed in on `client`, the owner of the organisation she signed up with, and Bob on
    a browser of his own, a member of it besides his own; on the Team plan, with seats for
    both."""
    ada = signup(client, "ada@example.com", name="Ada")
    bob_client = another_browser(client)
    bob = signup(bob_client, "bob@example.com", name="Bob")
    org_id = ada["organization"]["id"]
    session.execute(
        update(Organization).where(Organization.id == org_id).values(plan="team", seats=5)
    )
    join(session, org_id, bob["id"])
    bob_client.post(f"/api/organizations/{org_id}/switch")
    return org_id, bob_client


def ask_deletion(client, **body):
    return client.post("/api/organization/deletion", json=body)


def due_of(session, org_id: int) -> datetime | None:
    session.expire_all()
    return session.get(Organization, org_id).deletion_due_at


def make_due(session, org_id: int) -> None:
    """Move the organisation's deletion into the past, as the grace period running out would."""
    session.execute(
        update(Organization)
        .where(Organization.id == org_id)
        .values(deletion_due_at=datetime.now(UTC) - timedelta(minutes=1))
    )
    session.commit()


def test_an_owner_asks_confirmed_by_the_password_and_every_member_is_told(
    client, session, mailer, crew
):
    org_id, bob_client = crew
    mailer.sent.clear()

    refused = ask_deletion(bob_client, password=PASSWORD)
    assert refused.status_code == 403 and refused.json()["detail"] == OWNERS_DELETE
    for body in ({}, {"password": "not it"}, {"email": "ada@example.com"}):
        refused = ask_deletion(client, **body)
        assert refused.status_code == 403 and refused.json()["detail"] == WRONG_PASSWORD
    assert due_of(session, org_id) is None

    asked = ask_deletion(client, password=PASSWORD)
    assert asked.status_code == 200, asked.text
    due = datetime.fromisoformat(asked.json()["organization"]["deletion_due_at"])
    assert abs(due - (datetime.now(UTC) + DELETION_GRACE)) < timedelta(minutes=1)
    assert due_of(session, org_id) == due
    shown = me(bob_client).json()["organization"]["deletion_due_at"]
    assert datetime.fromisoformat(shown) == due

    told = {to: body for to, subject, body in mailer.sent if subject == DELETED}
    assert set(told) == {"ada@example.com", "bob@example.com"}
    for body in told.values():
        assert f"Ada asked for Ada's workspace to be deleted. On {day(due)} it goes" in body
        assert f"http://testserver/organization?organization={org_id}" in body
    assert "choose to keep it" in told["ada@example.com"]
    assert "ask one of its owners to keep it" in told["bob@example.com"]
    assert ("organization_deletion_requested", {"due_at": due.isoformat()}) in actions(
        session, org_id
    )

    # Asked again while it is due, nothing changes and nobody is told again.
    mailer.sent.clear()
    again = ask_deletion(client, password=PASSWORD)
    assert again.status_code == 200 and due_of(session, org_id) == due
    assert not mailer.sent


def test_until_the_day_it_is_held_as_it_stands(client, session, crew, sample_pdf, make_account):
    org_id, bob_client = crew
    project = create_project(client, "Contracts")
    upload(client, project["id"], sample_pdf)
    assert ask_deletion(client, password=PASSWORD).status_code == 200
    held = held_for_deletion(session.get(Organization, org_id))
    assert held.startswith("Ada's workspace is set to be deleted on ")

    for response in (
        client.post("/api/projects", json={"name": "More"}),
        client.patch(f"/api/projects/{project['id']}", json={"name": "Renamed"}),
        bob_client.post(
            f"/api/projects/{project['id']}/documents",
            files=[("files", ("note.txt", b"A note.", "text/plain"))],
        ),
        client.post(
            "/api/organization/invitations", json={"email": "cat@example.com", "role": "member"}
        ),
    ):
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == held

    # What it holds can still be read, downloaded and deleted, and its members may leave.
    assert [p["name"] for p in client.get("/api/projects").json()] == ["Contracts"]
    (document,) = client.get(f"/api/projects/{project['id']}/documents").json()["documents"]
    assert client.get(f"/api/documents/{document['id']}").status_code == 200
    assert client.delete(f"/api/documents/{document['id']}").status_code == 204
    assert bob_client.post("/api/organization/leave").status_code == 200

    # Nobody new joins it, by the domain or by an invitation sent before.
    session.execute(
        update(Organization).where(Organization.id == org_id).values(join_domains=["example.com"])
    )
    session.commit()
    assert me(bob_client).json()["joinable"] == []
    assert bob_client.post(f"/api/organizations/{org_id}/join").status_code == 404


def test_an_owner_keeps_it_and_every_member_is_told(client, session, mailer, crew):
    org_id, bob_client = crew
    assert ask_deletion(client, password=PASSWORD).status_code == 200
    mailer.sent.clear()

    refused = bob_client.delete("/api/organization/deletion")
    assert refused.status_code == 403 and refused.json()["detail"] == OWNERS_DELETE
    kept = client.delete("/api/organization/deletion")
    assert kept.status_code == 200 and kept.json()["organization"]["deletion_due_at"] is None
    assert due_of(session, org_id) is None
    told = {to: body for to, subject, body in mailer.sent if subject == KEPT}
    assert set(told) == {"ada@example.com", "bob@example.com"}
    assert "Ada chose to keep Ada's workspace" in told["bob@example.com"]
    assert actions(session, org_id)[-1] == ("organization_deletion_cancelled", {})

    # It can be changed again, and keeping it again changes nothing.
    assert client.post("/api/projects", json={"name": "More"}).status_code == 201
    mailer.sent.clear()
    assert client.delete("/api/organization/deletion").status_code == 200
    assert not mailer.sent


def test_a_subscription_that_renews_is_cancelled_first(client, session, crew, monkeypatch):
    org_id, _ = crew
    session.execute(
        update(Organization)
        .where(Organization.id == org_id)
        .values(subscription_status="active", stripe_subscription_id="sub_1")
    )
    session.commit()
    organization = session.get(Organization, org_id)
    refused = ask_deletion(client, password=PASSWORD)
    assert refused.status_code == 409
    assert (
        refused.json()["detail"]
        == renewal_refusal(organization)
        == (
            "The subscription of Ada's workspace to the Team plan still renews. Cancel it under "
            "Manage billing first, then delete the organisation: it ends with the period paid for, "
            "and nothing more is charged."
        )
    )

    # Cancelled, it ends with its period, and the organisation may go.
    session.execute(
        update(Organization)
        .where(Organization.id == org_id)
        .values(ends_at=datetime.now(UTC) + timedelta(days=10))
    )
    session.commit()
    assert ask_deletion(client, password=PASSWORD).status_code == 200

    # Meanwhile it buys nothing and changes no subscription.
    for name, value in (
        ("stripe_secret_key", STRIPE_KEY),
        ("stripe_webhook_secret", "whsec_test"),
        ("stripe_prices", "pro=price_pro"),
    ):
        monkeypatch.setattr(get_settings(), name, value)
    session.expire_all()
    held = held_for_deletion(session.get(Organization, org_id))
    for response in (
        client.post("/api/organization/billing/checkout", json={"plan": "pro"}),
        client.post("/api/organization/billing/portal"),
    ):
        assert response.status_code == 409 and response.json()["detail"] == held


def test_the_local_organisation_is_never_deleted(client):
    refused = ask_deletion(client, email="owner@emudoc.invalid")
    assert refused.status_code == 409 and refused.json()["detail"] == LOCAL_ORGANISATION


def test_the_purge_takes_it_with_its_files_and_houses_the_members_it_leaves_in_none(
    client, session, services, crew, sample_pdf, make_account
):
    org_id, bob_client = crew
    upload(client, create_project(client)["id"], sample_pdf)
    # Cat is a member of Ada's organisation alone; Bob has his own besides.
    cat = User(email="cat@example.com", name="Cat")
    session.add(cat)
    session.flush()
    join(session, org_id, cat.id)
    session.add(
        Invitation(
            org_id=org_id,
            email="dan@example.com",
            token_hash="x" * 64,
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
    )
    # Another organisation, which the purge must not touch.
    other = make_account("una@globex.example")
    session.add(Project(org_id=other.organization.id, name="Globex files"))
    session.commit()
    storage = services.storage
    assert list(storage.list(organization_prefix(org_id)))

    assert ask_deletion(client, password=PASSWORD).status_code == 200
    # Not due yet: nothing goes.
    assert purge_due_organizations(storage, renews=renews) == 0
    make_due(session, org_id)
    housekeeping(storage)

    session.expire_all()
    assert session.get(Organization, org_id) is None
    assert session.scalars(select(Project.name)).all() == ["Globex files"]
    assert not session.scalar(select(func.count()).select_from(Document))
    assert not session.scalar(select(func.count()).select_from(Invitation))
    assert not session.scalar(
        select(func.count()).select_from(AuditEvent).where(AuditEvent.org_id == org_id)
    )
    assert not list(storage.list(organization_prefix(org_id)))

    # Ada and Cat had no other organisation, so each has one of their own now; Bob works in his.
    owned = dict(
        session.execute(
            select(User.email, Organization.name)
            .join(Membership, Membership.user_id == User.id)
            .join(Organization, Organization.id == Membership.org_id)
        ).all()
    )
    assert owned == {
        "ada@example.com": "Ada's workspace",
        "bob@example.com": "Bob's workspace",
        "cat@example.com": "Cat's workspace",
        "una@globex.example": "Globex",
    }
    cat_org = session.scalar(select(Membership.org_id).where(Membership.user_id == cat.id))
    assert actions(session, cat_org) == [("organization_created", {"name": "Cat's workspace"})]
    assert me(client).json()["organization"]["name"] == "Ada's workspace"
    assert me(client).json()["organization"]["id"] != org_id
    assert me(bob_client).json()["organization"]["name"] == "Bob's workspace"
    assert not session.scalar(
        select(func.count()).select_from(UserSession).where(UserSession.org_id == org_id)
    )

    # The record names the organisation by its id and creation time, and holds nothing else.
    (entry,) = storage.list(ORGANIZATION_ERASURES)
    assert entry.key.startswith(f"erasures/organizations/{org_id}-")
    record = storage.get(entry.key).decode()
    assert "ada" not in record.lower() and "workspace" not in record.lower()


def test_one_whose_subscription_renews_again_is_kept_until_it_is_cancelled(
    client, session, services, crew, caplog
):
    org_id, _ = crew
    assert ask_deletion(client, password=PASSWORD).status_code == 200
    make_due(session, org_id)
    # Renewed at Stripe after the request, which the organisation page no longer offers.
    session.execute(
        update(Organization)
        .where(Organization.id == org_id)
        .values(subscription_status="active", stripe_subscription_id="sub_1")
    )
    session.commit()
    with caplog.at_level(logging.ERROR, logger="app.retention"):
        assert purge_due_organizations(services.storage, renews=renews) == 0
    assert f"Organisation {org_id} is due to be deleted, but its subscription renews" in caplog.text
    assert session.get(Organization, org_id) is not None

    session.execute(
        update(Organization)
        .where(Organization.id == org_id)
        .values(ends_at=datetime.now(UTC) + timedelta(days=3))
    )
    session.commit()
    assert purge_due_organizations(services.storage, renews=renews) == 1


def test_an_organisation_a_restore_brings_back_is_deleted_again(
    client, session, services, signed_in_only, sample_pdf, caplog
):
    account = signup(client)
    org_id = account["organization"]["id"]
    upload(client, create_project(client)["id"], sample_pdf)
    organization = session.get(Organization, org_id)
    key = organization_erasure_key(org_id, organization.created_at)
    # The backup holds Ada too; she is put back only where she is gone.
    taken = backup(session, org_id)
    taken = {table: rows for table, rows in taken.items() if table.name != "users"}
    storage = services.storage

    assert ask_deletion(client, password=PASSWORD).status_code == 200
    make_due(session, org_id)
    housekeeping(storage)
    session.expire_all()
    assert session.get(Organization, org_id) is None
    assert [entry.key for entry in storage.list(ORGANIZATION_ERASURES)] == [key]

    # The database is restored from the backup made before the request, which lacks the
    # organisation Ada was given since; the bucket is not.
    session.execute(delete(Organization).where(Organization.id != org_id))
    restore(session, taken)
    assert session.get(Organization, org_id).deletion_due_at is None
    with caplog.at_level(logging.WARNING, logger="app.retention"):
        housekeeping(storage)
    assert f"Organisations [{org_id}] were deleted before and are back" in caplog.text
    session.expire_all()
    assert session.get(Organization, org_id) is None
    assert not session.scalar(select(func.count()).select_from(Document))


def test_the_only_owner_of_an_organisation_set_to_be_deleted_may_delete_their_account(
    client, session, crew
):
    org_id, _ = crew
    refused = client.post("/api/auth/account/deletion", json={"password": PASSWORD})
    assert refused.status_code == 409
    assert ask_deletion(client, password=PASSWORD).status_code == 200
    deleted = client.post("/api/auth/account/deletion", json={"password": PASSWORD})
    assert deleted.status_code == 200, deleted.text
