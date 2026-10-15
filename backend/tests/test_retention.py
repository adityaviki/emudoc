"""Retention and deletion: a chat is kept for its organisation's plan's retention after its last
question, an ended session or link for a month; an account is deleted after a grace period,
confirmed by its password or its address, during which it can only be kept, exported or signed
out of; the purge takes every organisation the account was the only member of, with its rows
and files, leaves the rest of a shared one with an owner, and leaves a record by which an
account that a restore brings back is purged again; and the export holds what is kept about its
user and nothing of anyone else's."""

import logging
from datetime import UTC, datetime, timedelta

import pytest
from pydantic_ai.messages import TextPart
from sqlalchemy import func, select, text, update

from app.accounts import Account
from app.api.auth import TYPE_EMAIL, WRONG_PASSWORD
from app.auth import CREDENTIAL_HISTORY, issue_link_token, new_token, token_hash
from app.db import SessionLocal
from app.entitlements import PLANS
from app.maintenance import housekeeping
from app.models import (
    AuthToken,
    Chat,
    ChatMessage,
    Document,
    Job,
    Membership,
    Organization,
    Project,
    UsageEvent,
    User,
    UserSession,
)
from app.retention import (
    DELETION_GRACE,
    ERASURES,
    LOCAL_ACCOUNT,
    day,
    deletion_pending,
    erasure_key,
    purge_due_accounts,
    reapply_erasures,
)
from app.storage import StorageError, organization_prefix
from app.usage import ANSWER, record
from tests.conftest import FakeTurn
from tests.test_api import create_project, upload
from tests.test_auth import PASSWORD, another_browser, login, me, signup
from tests.test_chats import answered, new_chat

NOW = datetime.now(UTC)


def make_chat(session, project: Project, user: User, title: str, days_ago: float) -> Chat:
    """A chat whose last question was asked `days_ago` days ago, with that question in it."""
    asked = datetime.now(UTC) - timedelta(days=days_ago)
    chat = Chat(project_id=project.id, user_id=user.id, title=title, updated_at=asked)
    session.add(chat)
    session.flush()
    session.add(ChatMessage(chat_id=chat.id, role="user", content=title, created_at=asked))
    session.commit()
    return chat


def chat_titles(session) -> set[str]:
    session.expire_all()
    return set(session.scalars(select(Chat.title)))


def ada(session) -> User:
    return session.scalar(select(User).where(User.email == "ada@example.com"))


def make_due(session, user: User) -> None:
    """Move the user's deletion into the past, as the grace period running out would."""
    session.execute(
        update(User)
        .where(User.id == user.id)
        .values(deletion_due_at=datetime.now(UTC) - timedelta(minutes=1))
    )
    session.commit()


def delete_account(client, **body):
    return client.post("/api/auth/account/deletion", json=body)


def org_of(account: dict) -> Organization:
    return account["organization"]


# Retention


def test_chats_past_their_plans_retention_go_and_the_rest_stay(
    session, services, account, make_account
):
    kept_for = PLANS["free"].retention_days
    free = make_account("ann@globex.example")
    pro = make_account("bo@initech.example")
    session.execute(update(Organization).where(Organization.slug == "initech").values(plan="pro"))
    session.commit()
    projects = {}
    for name, owner in (("free", free), ("pro", pro), ("enterprise", account)):
        projects[name] = Project(org_id=owner.organization.id, name=name)
        session.add(projects[name])
    session.commit()

    make_chat(session, projects["free"], free.user, "free, quiet too long", kept_for + 1)
    make_chat(session, projects["free"], free.user, "free, asked lately", kept_for - 1)
    # An old chat whose answer is still being written is left until the answer is settled.
    streaming = make_chat(session, projects["free"], free.user, "free, answering", kept_for + 5)
    session.add(ChatMessage(chat_id=streaming.id, role="assistant", content="", status="streaming"))
    # Created long ago, but asked in lately: the last question counts, not the first.
    old = make_chat(session, projects["free"], free.user, "free, revived", kept_for - 2)
    old.created_at = NOW - timedelta(days=kept_for * 3)
    make_chat(session, projects["pro"], pro.user, "pro, a quarter old", 100)
    make_chat(session, projects["pro"], pro.user, "pro, over a year old", 366)
    make_chat(session, projects["enterprise"], account.user, "enterprise, ancient", 4000)
    session.commit()
    messages = session.scalar(select(func.count()).select_from(ChatMessage))

    housekeeping(services.storage)

    assert chat_titles(session) == {
        "free, asked lately",
        "free, answering",
        "free, revived",
        "pro, a quarter old",
        "enterprise, ancient",
    }
    # Their messages went with them.
    assert session.scalar(select(func.count()).select_from(ChatMessage)) == messages - 2


def test_ended_sessions_and_links_are_removed_after_a_month(client, session, services):
    signup(client)
    user = ada(session)
    ended = NOW - CREDENTIAL_HISTORY - timedelta(days=1)
    lately = NOW - timedelta(days=3)
    for expires, revoked in ((ended, None), (NOW + timedelta(days=9), ended), (lately, lately)):
        session.add(
            UserSession(
                user_id=user.id,
                token_hash=token_hash(new_token()),
                expires_at=expires,
                revoked_at=revoked,
            )
        )
    issue_link_token(session, user, "reset_password", user.email, ended - NOW)
    session.commit()
    assert session.scalar(select(func.count()).select_from(UserSession)) == 4
    assert session.scalar(select(func.count()).select_from(AuthToken)) == 2

    housekeeping(services.storage)

    session.expire_all()
    # The session signed in now and the one that ended days ago stay; so does the verification
    # link that signing up sent, which still works.
    assert session.scalar(select(func.count()).select_from(UserSession)) == 2
    assert session.scalars(select(AuthToken.purpose)).all() == ["verify_email"]
    assert me(client).status_code == 200


def test_the_account_says_how_long_its_chats_are_kept(client, signed_in_only):
    account = signup(client)
    assert org_of(account)["chat_retention_days"] == PLANS["free"].retention_days
    assert account["has_password"] is True and account["deletion_due_at"] is None


# Deleting an account


def test_deleting_an_account_takes_the_password_and_waits_out_the_grace_period(
    client, session, mailer, signed_in_only
):
    signup(client)
    create_project(client)
    elsewhere = another_browser(client)
    login(elsewhere)
    mailer.sent.clear()

    for body in ({}, {"password": "not it"}, {"email": "ada@example.com"}):
        refused = delete_account(client, **body)
        assert refused.status_code == 403 and refused.json()["detail"] == WRONG_PASSWORD
    assert me(client).json()["deletion_due_at"] is None

    deleted = delete_account(client, password=PASSWORD)
    assert deleted.status_code == 200, deleted.text
    due = datetime.fromisoformat(deleted.json()["deletion_due_at"])
    assert abs(due - (datetime.now(UTC) + DELETION_GRACE)) < timedelta(minutes=1)
    assert me(client).json() == deleted.json()

    ((to, subject, body),) = mailer.sent
    assert to == "ada@example.com" and subject == "Your account is set to be deleted"
    assert f"set to be deleted on {day(due)}" in body and "http://testserver/login" in body
    # Every other browser was signed out; this one stays, to keep the account or leave.
    assert me(elsewhere).status_code == 401

    # Until it is kept, the account can do nothing else.
    pending = deletion_pending(due)
    for response in (
        client.get("/api/projects"),
        client.post("/api/projects", json={"name": "More"}),
        client.get("/api/auth/sessions"),
        delete_account(client, password=PASSWORD),
    ):
        assert response.status_code == 403 and response.json()["detail"] == pending
    assert client.get("/api/auth/account/export").status_code == 200

    # Signing in again during the grace period finds it pending, and keeping it undoes it all.
    assert client.post("/api/auth/logout").status_code == 204
    login(client)
    assert me(client).json()["deletion_due_at"] is not None
    kept = client.delete("/api/auth/account/deletion")
    assert kept.status_code == 200 and kept.json()["deletion_due_at"] is None
    assert [project["name"] for project in client.get("/api/projects").json()] == ["Contracts"]
    session.expire_all()
    assert ada(session).deletion_due_at is None


def test_an_account_without_a_password_confirms_with_its_address(client, session, signed_in_only):
    signup(client)
    session.execute(update(User).values(password_hash=None))
    session.commit()
    assert me(client).json()["has_password"] is False

    for body in ({}, {"password": PASSWORD}, {"email": "someone@example.com"}):
        refused = delete_account(client, **body)
        assert refused.status_code == 403 and refused.json()["detail"] == TYPE_EMAIL
    assert delete_account(client, email=" Ada@Example.com ").status_code == 200


def test_the_local_account_cannot_be_deleted(client):
    refused = delete_account(client)
    assert refused.status_code == 409 and refused.json()["detail"] == LOCAL_ACCOUNT


def test_the_last_owner_of_an_organisation_with_members_is_refused(client, session, signed_in_only):
    organization_id = org_of(signup(client))["id"]
    bob = User(email="bob@example.com")
    session.add(bob)
    session.flush()
    session.add(Membership(org_id=organization_id, user_id=bob.id, role="member"))
    session.commit()

    refused = delete_account(client, password=PASSWORD)
    assert refused.status_code == 409
    assert refused.json()["detail"] == (
        "You are the only owner of Ada's workspace, which has other members. Make one of them "
        "an owner, or remove them, before deleting your account."
    )
    session.execute(update(Membership).where(Membership.user_id == bob.id).values(role="owner"))
    session.commit()
    assert delete_account(client, password=PASSWORD).status_code == 200


def test_the_purge_takes_the_account_its_organisation_and_their_files(
    client, session, services, signed_in_only, sample_pdf, make_account
):
    account = signup(client)
    organization_id = org_of(account)["id"]
    project = create_project(client)
    upload(client, project["id"], sample_pdf)
    services.llm.agent_turns = [FakeTurn(TextPart("The contract runs for a year."))]
    answered(client, services, new_chat(client, project["id"])["id"], "How long does it run?")
    # Another organisation, which the purge must not touch.
    other = make_account("bob@globex.example")
    session.add(Project(org_id=other.organization.id, name="Globex files"))
    session.commit()
    storage = services.storage
    assert list(storage.list(organization_prefix(organization_id)))

    assert delete_account(client, password=PASSWORD).status_code == 200
    # Not due yet: nothing goes.
    assert purge_due_accounts(storage) == 0
    assert ada(session) is not None

    make_due(session, ada(session))
    # A replica that holds the user is purging it already: another skips it.
    with SessionLocal() as other_replica:
        other_replica.execute(
            text("SELECT id FROM users WHERE email = 'ada@example.com' FOR UPDATE")
        )
        assert purge_due_accounts(storage) == 0
    housekeeping(storage)

    session.expire_all()
    assert ada(session) is None
    assert session.get(Organization, organization_id) is None
    # Everything else the database held was Ada's or her organisation's.
    for model in (Document, Chat, ChatMessage, Job, UsageEvent, UserSession, AuthToken):
        assert not session.scalar(select(func.count()).select_from(model)), model.__name__
    assert session.scalars(select(Project.name)).all() == ["Globex files"]
    assert not list(storage.list(organization_prefix(organization_id)))

    # The session is gone with the account, the password signs nothing in, and the address
    # is free for a new account.
    assert me(client).status_code == 401
    refused = client.post(
        "/api/auth/login", json={"email": "ada@example.com", "password": PASSWORD}
    )
    assert refused.status_code == 401
    assert org_of(signup(client))["id"] != organization_id


def backup(session, organization_id: int) -> dict:
    """The rows of the organisation and its one member, as a backup made now holds them."""
    picks = {
        Organization: Organization.id == organization_id,
        User: User.id.in_(select(Membership.user_id).where(Membership.org_id == organization_id)),
        Membership: Membership.org_id == organization_id,
        Project: Project.org_id == organization_id,
        Document: Document.project_id.in_(
            select(Project.id).where(Project.org_id == organization_id)
        ),
    }
    return {
        model.__table__: [
            dict(row) for row in session.execute(select(model.__table__).where(pick)).mappings()
        ]
        for model, pick in picks.items()
    }


def restore(session, rows: dict) -> None:
    """Put the backed-up rows back, parents first, as restoring the backup would."""
    for table, values in rows.items():
        session.execute(table.insert(), values)
    session.commit()


def test_an_account_a_restore_brings_back_is_purged_again(
    client, session, services, signed_in_only, sample_pdf, caplog
):
    organization_id = org_of(signup(client))["id"]
    upload(client, create_project(client)["id"], sample_pdf)
    user = ada(session)
    key = erasure_key(user.id, user.created_at)
    taken = backup(session, organization_id)
    storage = services.storage
    assert reapply_erasures(storage) == []

    assert delete_account(client, password=PASSWORD).status_code == 200
    make_due(session, user)
    housekeeping(storage)
    session.expire_all()
    assert ada(session) is None
    # The record names the account by its id and creation time, and holds nothing else.
    (entry,) = storage.list(ERASURES)
    assert entry.key == key and key.startswith("erasures/users/1-")
    record = storage.get(entry.key).decode()
    assert "ada" not in record.lower() and "example" not in record

    # The database is restored from the backup made before the request; the bucket is not.
    restore(session, taken)
    assert ada(session).deletion_due_at is None
    with caplog.at_level(logging.WARNING, logger="app.retention"):
        housekeeping(storage)
    assert "Accounts [1] were purged before and are back in the database" in caplog.text
    session.expire_all()
    assert ada(session) is None
    assert session.get(Organization, organization_id) is None
    assert not session.scalar(select(func.count()).select_from(Document))
    assert [entry.key for entry in storage.list(ERASURES)] == [key]
    assert me(client).status_code == 401


def test_a_new_account_with_a_purged_ones_id_is_left_alone(session, services, make_account):
    # A restored database hands out ids from where its sequences were, so a new account can
    # get the id of one purged since; its creation time tells them apart.
    purged = make_account("una@acme.example").user
    purged_id = purged.id
    make_due(session, purged)
    assert purge_due_accounts(services.storage) == 1
    session.expunge_all()
    session.add(User(id=purged_id, email="new@initech.example"))
    session.commit()

    assert reapply_erasures(services.storage) == []
    housekeeping(services.storage)
    session.expire_all()
    assert session.get(User, purged_id).email == "new@initech.example"


def test_a_purge_whose_record_cannot_be_kept_waits_for_the_next_round(
    session, services, make_account, monkeypatch
):
    storage = services.storage
    account = make_account("una@acme.example")
    user_id, organization_id = account.user.id, account.organization.id
    make_due(session, account.user)
    put = storage.put

    def unavailable(key, source, content_type=None):
        if key.startswith(ERASURES):
            raise StorageError("The storage is out of reach.", transient=True)
        put(key, source, content_type)

    monkeypatch.setattr(storage, "put", unavailable)
    with pytest.raises(StorageError):
        purge_due_accounts(storage)
    session.expire_all()
    assert session.get(User, user_id) is not None
    assert session.get(Organization, organization_id) is not None

    monkeypatch.setattr(storage, "put", put)
    assert purge_due_accounts(storage) == 1
    session.expire_all()
    assert session.get(User, user_id) is None
    assert len(list(storage.list(ERASURES))) == 1


def test_a_purge_leaves_a_shared_organisation_with_its_most_senior_member_as_owner(
    session, services, make_account, caplog
):
    owner = make_account("una@acme.example")
    organization = owner.organization
    joined = {
        "mia@acme.example": ("member", 30),
        "ali@acme.example": ("admin", 20),
        "ari@acme.example": ("admin", 10),
        "vic@acme.example": ("viewer", 40),
    }
    others: dict[str, Account] = {}
    for email, (role, days_ago) in joined.items():
        others[email] = make_account(email, organization, role)
        session.execute(
            update(Membership)
            .where(Membership.user_id == others[email].user.id)
            .values(created_at=NOW - timedelta(days=days_ago))
        )
    project = Project(org_id=organization.id, name="Shared")
    session.add(project)
    session.commit()
    make_chat(session, project, owner.user, "Una's chat", 1)
    make_chat(session, project, others["mia@acme.example"].user, "Mia's chat", 1)
    record(
        session,
        org_id=organization.id,
        project_id=project.id,
        user_id=owner.user.id,
        kind=ANSWER,
        measures={"calls": 1},
    )
    session.commit()
    # Set to be deleted while another owner was there, who has since been made an admin.
    make_due(session, owner.user)

    with caplog.at_level(logging.INFO, logger="app.retention"):
        assert purge_due_accounts(services.storage) == 1

    session.expire_all()
    roles = dict(
        session.execute(
            select(User.email, Membership.role).join(Membership, Membership.user_id == User.id)
        ).all()
    )
    # The admins outrank the member who joined first; of the two, the earlier one succeeds.
    assert roles == {
        "mia@acme.example": "member",
        "ali@acme.example": "owner",
        "ari@acme.example": "admin",
        "vic@acme.example": "viewer",
    }
    assert chat_titles(session) == {"Mia's chat"}
    assert session.scalar(select(Project.name)) == "Shared"
    # The organisation keeps its usage; the departed user's part of it is no one's now.
    assert session.scalars(select(UsageEvent.user_id)).all() == [None]
    # The log names ids, never the person.
    assert "becomes its owner" in caplog.text and "Deleted account" in caplog.text
    assert "@" not in caplog.text


# The account export


def test_the_export_holds_the_users_data_and_nothing_of_anyone_elses(
    client, session, services, signed_in_only
):
    organization_id = org_of(signup(client))["id"]
    project = create_project(client)
    services.llm.agent_turns = [FakeTurn(TextPart("Ninety days."))]
    chat = new_chat(client, project["id"])
    answered(client, services, chat["id"], "What is the notice period?")
    # A colleague's chat in the same project is theirs, not Ada's.
    bob = User(email="bob@example.com")
    session.add(bob)
    session.flush()
    session.add(Membership(org_id=organization_id, user_id=bob.id, role="member"))
    make_chat(session, session.get(Project, project["id"]), bob, "Bob's own question", 1)

    response = client.get("/api/auth/account/export")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-disposition"] == (
        f'attachment; filename="emudoc-account-{datetime.now(UTC):%Y-%m-%d}.json"'
    )
    export = response.json()
    assert export["account"]["email"] == "ada@example.com"
    assert export["account"]["name"] == "Ada"
    assert [(row["slug"], row["role"]) for row in export["organizations"]] == [("ada", "owner")]
    assert [row["ip"] for row in export["sessions"]] == ["testclient"]
    (exported,) = export["chats"]
    assert exported["project"] == "Contracts" and exported["organization"] == "ada"
    assert [(m["role"], m["content"]) for m in exported["messages"]] == [
        ("user", "What is the notice period?"),
        ("assistant", "Ninety days."),
    ]
    assert {row["kind"] for row in export["usage"]} == {"answer"}
    assert "documents themselves are not in this file" in export["about"]

    # Nothing that guards the account, and nothing of Bob's.
    assert set(export["account"]) == {
        "email",
        "name",
        "created_at",
        "email_verified_at",
        "two_factor_enabled_at",
        "deletion_due_at",
        "sign_in_providers",
    }
    assert ada(session).password_hash not in response.text
    assert "Bob's own question" not in response.text and "bob@" not in response.text


def test_the_account_routes_need_a_session(client, signed_in_only):
    assert client.get("/api/auth/account/export").status_code == 401
    assert delete_account(client, password=PASSWORD).status_code == 401
    assert client.delete("/api/auth/account/deletion").status_code == 401
