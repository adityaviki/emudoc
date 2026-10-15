"""The audit log: uploads, downloads, deletions, the sharing of chats, sign-ins and account
changes are recorded with who did them and from where, in the transaction of the action; what
the operator and the app do on their own is recorded as nobody's; no row holds what a document,
a question or a title says; the owners and admins of an organisation read its rows and no one
else's; an account's deletion clears its addresses from the rows its organisations keep; and
the rows go after their plan's days."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from starlette.requests import Request

from app.api.audit import ADMINS_ONLY
from app.audit import audit_event
from app.auth import totp_code, totp_step
from app.config import get_settings
from app.indexing.pipeline import process_document
from app.maintenance import housekeeping, main
from app.models import AuditEvent, Document, Organization, Project, User
from app.retention import purge_due_accounts
from tests.conftest import EICAR_NAME
from tests.test_api import create_project, upload
from tests.test_auth import (
    NEW_PASSWORD,
    PASSWORD,
    another_browser,
    callback,
    google_claims,
    login,
    signup,
    start,
)
from tests.test_chats import new_chat
from tests.test_deadletters import fail, job_of
from tests.test_folders import make_folder, upload_into
from tests.test_retention import make_due
from tests.test_scanning import flagged_pdf

CLIENT = "testclient"


def rows(session, org_id: int | None = None) -> list[AuditEvent]:
    """The audit rows, the oldest first: one organisation's, or everyone's."""
    session.expire_all()
    query = select(AuditEvent).order_by(AuditEvent.id)
    if org_id is not None:
        query = query.where(AuditEvent.org_id == org_id)
    return list(session.scalars(query))


def actions(session, org_id: int | None = None) -> list[tuple[str, dict]]:
    return [(row.action, row.detail) for row in rows(session, org_id)]


def page(client, **params) -> dict:
    response = client.get("/api/audit", params=params)
    assert response.status_code == 200, response.text
    return response.json()


# Documents and chats


def test_uploads_downloads_and_deletions_are_recorded_with_who_and_where(
    client, session, services, account, sample_pdf, sample_docx
):
    project = create_project(client)
    pdf, docx = upload(client, project["id"], sample_pdf, sample_docx)["documents"]
    # A file the project holds already is rejected, and a rejected file leaves no row.
    assert upload(client, project["id"], sample_pdf)["rejected"]
    process_document(pdf["id"], services)

    assert client.get(f"/api/documents/{pdf['id']}/file").status_code == 200
    # The rendered page and the thumbnail are views of the document, not the file itself.
    assert client.get(f"/api/documents/{pdf['id']}/pages/1.png").status_code == 200
    assert client.get(f"/api/documents/{pdf['id']}/thumbnail.png").status_code == 200
    assert client.delete(f"/api/documents/{docx['id']}").status_code == 204

    folder = make_folder(client, project["id"], "Invoices")
    upload_into(client, project["id"], folder["id"], sample_docx)
    assert client.delete(f"/api/folders/{folder['id']}").status_code == 204
    assert client.delete(f"/api/projects/{project['id']}").status_code == 204

    in_folder = rows(session)[-3].target_id
    assert actions(session) == [
        ("document_uploaded", {"filename": sample_pdf.name, "size_bytes": pdf["size_bytes"]}),
        ("document_uploaded", {"filename": sample_docx.name, "size_bytes": docx["size_bytes"]}),
        ("document_downloaded", {"filename": sample_pdf.name}),
        ("document_deleted", {"filename": sample_docx.name}),
        ("document_uploaded", {"filename": sample_docx.name, "size_bytes": docx["size_bytes"]}),
        ("folder_deleted", {"name": "Invoices", "documents": 1}),
        ("project_deleted", {"name": "Contracts", "documents": 1}),
    ]
    targets = [(row.target_type, row.target_id) for row in rows(session)]
    assert targets == [
        ("document", pdf["id"]),
        ("document", docx["id"]),
        ("document", pdf["id"]),
        ("document", docx["id"]),
        ("document", in_folder),
        ("folder", folder["id"]),
        ("project", project["id"]),
    ]
    # Each row names the member, the project and the address; the project outlives its row.
    assert {(row.org_id, row.user_id, row.project_id, row.ip) for row in rows(session)} == {
        (account.organization.id, account.user.id, project["id"], CLIENT)
    }
    assert session.get(Project, project["id"]) is None

    # The owner reads it back, the newest first, with the member's name.
    events = page(client)["events"]
    assert [event["action"] for event in events][:2] == ["project_deleted", "folder_deleted"]
    assert events[0]["user_email"] == account.user.email and events[0]["ip"] == CLIENT


def test_a_chat_shared_and_deleted_is_recorded_without_its_title(client, session, account):
    project = create_project(client)
    chat = new_chat(client, project["id"])
    title = "What is the notice period of the supplier contract?"
    url = f"/api/chats/{chat['id']}"
    assert client.patch(url, json={"title": title, "visibility": "project"}).status_code == 200
    # Sharing a chat that is shared already changes nothing, and records nothing.
    assert client.patch(url, json={"visibility": "project"}).status_code == 200
    assert client.patch(url, json={"title": "Renamed"}).status_code == 200
    assert client.patch(url, json={"visibility": "private"}).status_code == 200
    assert client.delete(url).status_code == 204

    assert actions(session) == [("chat_shared", {}), ("chat_unshared", {}), ("chat_deleted", {})]
    assert {(row.target_type, row.target_id, row.project_id) for row in rows(session)} == {
        ("chat", chat["id"], project["id"])
    }


# Sign-ins and accounts


def test_sign_ins_and_changes_to_an_account_are_recorded(
    client, session, mailer, monkeypatch, signed_in_only
):
    # This signs the one account in a dozen times, more than RATE_SIGN_INS lets it in a minute.
    monkeypatch.setattr(get_settings(), "rate_sign_ins", 100)
    ada = signup(client)
    org_id = ada["organization"]["id"]
    assert client.post("/api/auth/logout").status_code == 204

    wrong = client.post("/api/auth/login", json={"email": ada["email"], "password": "wrong"})
    assert wrong.status_code == 401
    # An address with no account is a stranger's: refused the same, and recorded nowhere.
    stranger = client.post(
        "/api/auth/login", json={"email": "nobody@example.com", "password": PASSWORD}
    )
    assert stranger.status_code == 401
    login(client)
    laptop = another_browser(client)
    login(laptop)
    phone = another_browser(client)
    login(phone)
    phone_session = max(row["id"] for row in client.get("/api/auth/sessions").json())
    assert client.delete(f"/api/auth/sessions/{phone_session}").status_code == 204
    assert (
        client.post(
            "/api/auth/password",
            json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        ).status_code
        == 204
    )
    login(laptop, password=NEW_PASSWORD)
    assert client.delete("/api/auth/sessions").status_code == 204

    assert client.post("/api/auth/password/forgot", json={"email": ada["email"]}).status_code == 202
    reset = client.post(
        "/api/auth/password/reset", json={"token": mailer.token(), "password": PASSWORD}
    )
    assert reset.status_code == 204
    login(client)

    secret = client.post("/api/auth/mfa/setup").json()["secret"]
    recovery = client.post(
        "/api/auth/mfa/enable", json={"code": totp_code(secret, totp_step())}
    ).json()["recovery_codes"]
    # With two-factor on, the password alone is no sign-in yet; the code makes it one.
    assert login(laptop) == {"mfa_required": True}
    assert laptop.post("/api/auth/mfa/verify", json={"code": "000000"}).status_code == 401
    following = totp_code(secret, totp_step() + 1)
    assert laptop.post("/api/auth/mfa/verify", json={"code": following}).status_code == 200
    login(phone)
    assert phone.post("/api/auth/mfa/verify", json={"code": recovery[0]}).status_code == 200
    assert client.post("/api/auth/mfa/disable", json={"password": PASSWORD}).status_code == 204

    assert client.get("/api/auth/account/export").status_code == 200
    deletion = client.post("/api/auth/account/deletion", json={"password": PASSWORD})
    assert deletion.status_code == 200
    assert client.delete("/api/auth/account/deletion").status_code == 200
    # Keeping an account that is not set to be deleted keeps nothing, and records nothing.
    assert client.delete("/api/auth/account/deletion").status_code == 200

    recorded = [(action, dict(detail)) for action, detail in actions(session, org_id)]
    due_at = recorded[-2][1].pop("due_at")
    assert datetime.fromisoformat(due_at) == datetime.fromisoformat(
        deletion.json()["deletion_due_at"]
    )
    assert recorded == [
        ("sign_up", {"method": "password"}),
        ("sign_out", {}),
        ("sign_in_failed", {"reason": "password"}),
        ("sign_in", {"method": "password"}),
        ("sign_in", {"method": "password"}),
        ("sign_in", {"method": "password"}),
        ("session_revoked", {}),
        ("password_changed", {}),
        ("sign_in", {"method": "password"}),
        ("other_sessions_revoked", {"count": 1}),
        ("password_reset_requested", {}),
        ("password_reset", {}),
        ("sign_in", {"method": "password"}),
        ("mfa_enabled", {}),
        ("sign_in_failed", {"reason": "code"}),
        ("sign_in", {"second_factor": "authenticator"}),
        ("sign_in", {"second_factor": "recovery_code"}),
        ("mfa_disabled", {}),
        ("account_exported", {}),
        ("account_deletion_requested", {}),
        ("account_deletion_cancelled", {}),
    ]
    # Every row is the account's, from the browser's address, and nothing names a stranger.
    assert {(row.user_id, row.ip) for row in rows(session)} == {(ada["id"], CLIENT)}
    assert rows(session)[6].target_type == "session"
    assert rows(session)[6].target_id == phone_session


def test_the_export_holds_what_the_audit_log_recorded_of_its_user(client, signed_in_only):
    signup(client)
    create_project(client)
    first = client.get("/api/auth/account/export").json()
    assert [(row["action"], row["ip"]) for row in first["activity"]] == [("sign_up", CLIENT)]
    # The export itself is recorded, so the next one holds it.
    second = client.get("/api/auth/account/export").json()
    assert [row["action"] for row in second["activity"]] == ["sign_up", "account_exported"]
    assert second["activity"][0]["organization"] == "ada"


@pytest.fixture
def google(monkeypatch):
    """Sign-in through Google set up, as .env would."""
    monkeypatch.setattr(get_settings(), "google_oauth_client_id", "google-client")
    monkeypatch.setattr(get_settings(), "google_oauth_client_secret", "google-secret")


def test_a_sign_in_through_a_provider_is_recorded_with_it(
    client, session, identity_provider, google, signed_in_only
):
    for _ in range(2):
        _, handshake = start(client)
        identity_provider.claims = google_claims(handshake.nonce)
        assert callback(client, code="the-code", state=handshake.state).status_code == 302
        client.post("/api/auth/logout")
    assert actions(session) == [
        ("sign_up", {"method": "google"}),
        ("sign_in", {"method": "google"}),
        ("sign_out", {}),
        ("sign_in", {"method": "google"}),
        ("sign_out", {}),
    ]


# What the operator and the app do


def test_the_operator_and_the_app_act_as_nobody(
    client, session, services, account, sample_pdf, tmp_path, capsys
):
    main(["plan", "local", "pro"])
    main(["plan", "local", "pro"])
    project = create_project(client)
    (flagged,) = upload(client, project["id"], flagged_pdf(tmp_path, sample_pdf))["documents"]
    process_document(flagged["id"], services)
    # Say the job gave up on it; the operator queues it again once the signatures are fixed.
    fail(session, job_of(session, session.get(Document, flagged["id"])).id, "RuntimeError: boom")
    main(["failed-jobs", "--requeue"])
    capsys.readouterr()

    nobodys = [row for row in rows(session) if row.user_id is None]
    assert [(row.action, row.detail) for row in nobodys] == [
        ("plan_changed", {"from": "enterprise", "to": "pro"}),
        ("document_quarantined", {"filename": "bad.pdf", "threat": EICAR_NAME}),
        ("document_requeued", {"filename": "bad.pdf", "stage": "scan"}),
    ]
    assert {row.ip for row in nobodys} == {None}
    assert {(row.project_id, row.target_id) for row in nobodys[1:]} == {
        (project["id"], flagged["id"])
    }


def test_an_account_purge_keeps_a_shared_organisations_rows_without_its_addresses(
    session, services, make_account
):
    una = make_account("una@acme.example")
    mia = make_account("mia@acme.example", una.organization, "member")
    for who in (una, mia):
        audit_event(
            session,
            "document_downloaded",
            org_id=una.organization.id,
            user_id=who.user.id,
            request=Request({"type": "http", "client": ("192.0.2.7", 50000), "headers": []}),
            target=("document", 1),
            detail={"filename": "contract.pdf"},
        )
    session.commit()
    una_id = una.user.id
    make_due(session, una.user)

    assert purge_due_accounts(services.storage) == 1

    kept = rows(session, una.organization.id)
    assert [(row.action, row.user_id, row.ip) for row in kept] == [
        # The departed member's row stays with the organisation, without its address.
        ("document_downloaded", None, None),
        ("document_downloaded", mia.user.id, "192.0.2.7"),
        ("role_changed", None, None),
        ("account_purged", None, None),
    ]
    assert kept[2].detail == {"from": "member", "to": "owner"}
    assert (kept[2].target_type, kept[2].target_id) == ("user", mia.user.id)
    assert (kept[3].target_type, kept[3].target_id) == ("user", una_id)


def test_the_rows_of_an_organisation_it_alone_was_in_go_with_the_account(
    client, session, services, signed_in_only
):
    ada = signup(client)
    assert client.post("/api/auth/account/deletion", json={"password": PASSWORD}).status_code
    make_due(session, session.get(User, ada["id"]))
    assert rows(session)
    assert purge_due_accounts(services.storage) == 1
    assert rows(session) == []


# Reading and keeping it


def test_only_the_owners_and_admins_read_their_organisations_rows(
    client, session, make_account, act_as
):
    owner = make_account("una@acme.example")
    admin = make_account("ali@acme.example", owner.organization, "admin")
    member = make_account("mia@acme.example", owner.organization, "member")
    viewer = make_account("vic@acme.example", owner.organization, "viewer")
    other = make_account("ann@globex.example")
    for number in range(5):
        audit_event(
            session,
            "document_downloaded",
            org_id=owner.organization.id,
            user_id=member.user.id,
            detail={"filename": f"{number}.pdf"},
        )
    audit_event(session, "document_downloaded", org_id=other.organization.id)
    session.commit()

    for refused in (member, viewer):
        act_as(refused)
        response = client.get("/api/audit")
        assert response.status_code == 403 and response.json()["detail"] == ADMINS_ONLY

    for reader in (owner, admin):
        act_as(reader)
        first = page(client, limit=2)
        assert [event["detail"]["filename"] for event in first["events"]] == ["4.pdf", "3.pdf"]
        assert first["events"][0]["user_name"] == "Mia"
        second = page(client, limit=2, before=first["next_before"])
        assert [event["detail"]["filename"] for event in second["events"]] == ["2.pdf", "1.pdf"]
        last = page(client, limit=2, before=second["next_before"])
        assert [event["detail"]["filename"] for event in last["events"]] == ["0.pdf"]
        assert last["next_before"] is None

    act_as(other)
    (theirs,) = page(client)["events"]
    assert theirs["user_id"] is None and theirs["user_name"] is None
    assert client.get("/api/audit", params={"limit": 500}).status_code == 422


def test_rows_go_after_their_plans_days(session, services, make_account):
    free = make_account("ann@globex.example")
    enterprise = make_account("una@acme.example")
    enterprise.organization.plan = "enterprise"
    session.commit()
    for organization in (free.organization, enterprise.organization):
        for days_ago in (100, 10):
            audit_event(
                session,
                "document_downloaded",
                org_id=organization.id,
                detail={"days_ago": days_ago},
            )
    session.commit()
    session.execute(
        update(AuditEvent)
        .where(AuditEvent.detail["days_ago"].as_integer() == 100)
        .values(occurred_at=datetime.now(UTC) - timedelta(days=100))
    )
    session.commit()

    housekeeping(services.storage)

    kept = {
        (session.get(Organization, row.org_id).plan, row.detail["days_ago"])
        for row in rows(session)
    }
    # The free plan keeps a row for 90 days; enterprise keeps it until a policy says otherwise.
    assert kept == {("free", 10), ("enterprise", 100), ("enterprise", 10)}
