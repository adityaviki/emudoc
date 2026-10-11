"""The account tables: users, organisations, memberships, invitations, sessions and API keys,
and how projects and chats hang off them."""

import datetime as dt

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.accounts import LOCAL_ORG_SLUG, local_account
from app.models import (
    ApiKey,
    Chat,
    Invitation,
    Membership,
    Organization,
    Project,
    User,
    UserSession,
)

LATER = dt.datetime(2030, 1, 1, tzinfo=dt.UTC)


def organisation_with_owner(session) -> tuple[Organization, User]:
    user = User(email="ada@example.com", name="Ada")
    organization = Organization(name="Acme", slug="acme")
    session.add_all([user, organization])
    session.flush()
    session.add(Membership(org_id=organization.id, user_id=user.id, role="owner"))
    session.commit()
    return organization, user


def test_a_user_has_one_role_per_organisation(session):
    organization, user = organisation_with_owner(session)
    session.add(Membership(org_id=organization.id, user_id=user.id, role="viewer"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_unknown_roles_are_refused(session):
    organization, _ = organisation_with_owner(session)
    bob = User(email="bob@example.com")
    session.add(bob)
    session.flush()
    session.add(Membership(org_id=organization.id, user_id=bob.id, role="superuser"))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()
    session.add(
        Invitation(
            org_id=organization.id,
            email="eve@example.com",
            role="root",
            token_hash="x" * 64,
            expires_at=LATER,
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_email_addresses_are_unique(session):
    organisation_with_owner(session)
    session.add(User(email="ada@example.com", name="Another Ada"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_deleting_a_user_or_organisation_removes_what_hangs_off_it(session):
    organization, user = organisation_with_owner(session)
    session.add_all(
        [
            UserSession(user_id=user.id, token_hash="s" * 64, expires_at=LATER),
            Invitation(
                org_id=organization.id,
                email="bob@example.com",
                token_hash="i" * 64,
                invited_by_id=user.id,
                expires_at=LATER,
            ),
            ApiKey(
                org_id=organization.id,
                created_by_id=user.id,
                name="CI",
                key_prefix="emk_ab12",
                key_hash="k" * 64,
            ),
        ]
    )
    session.commit()

    session.delete(user)
    session.commit()
    assert session.scalars(select(UserSession)).all() == []
    assert session.scalars(select(Membership)).all() == []
    # What the user created for the organisation outlives their account.
    invitation = session.scalars(select(Invitation)).one()
    key = session.scalars(select(ApiKey)).one()
    assert invitation.invited_by_id is None
    assert key.created_by_id is None

    session.delete(organization)
    session.commit()
    assert session.scalars(select(Invitation)).all() == []
    assert session.scalars(select(ApiKey)).all() == []


def test_a_project_belongs_to_an_organisation(session):
    organization, _ = organisation_with_owner(session)
    session.add(Project(name="Homeless"))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

    session.add(Project(org_id=organization.id, name="Audit"))
    session.commit()
    session.delete(organization)
    session.commit()
    assert session.scalars(select(Project)).all() == []


def test_a_chat_records_who_started_it_and_is_private_by_default(session):
    organization, user = organisation_with_owner(session)
    project = Project(org_id=organization.id, name="Audit")
    session.add(project)
    session.commit()

    session.add(Chat(project_id=project.id))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

    session.add(Chat(project_id=project.id, user_id=user.id, visibility="public"))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

    chat = Chat(project_id=project.id, user_id=user.id)
    session.add(chat)
    session.commit()
    assert chat.visibility == "private"

    # A private chat is the user's own: it goes with their account, not with the project.
    session.delete(user)
    session.commit()
    assert session.scalars(select(Chat)).all() == []
    assert session.get(Project, project.id) is not None


def test_the_local_account_is_created_once_and_owns_what_the_api_makes(client, session):
    assert session.scalar(select(Organization)) is None
    first = local_account(session)
    second = local_account(session)
    assert (first.organization.id, first.user.id) == (second.organization.id, second.user.id)
    assert first.organization.slug == LOCAL_ORG_SLUG
    assert session.scalars(select(User)).all() == [first.user]

    project_id = client.post("/api/projects", json={"name": "Audit"}).json()["id"]
    chat_id = client.post(f"/api/projects/{project_id}/chats").json()["id"]
    assert session.get(Project, project_id).org_id == first.organization.id
    assert session.get(Chat, chat_id).user_id == first.user.id
