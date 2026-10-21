"""A project's export: a member asks, the worker writes one ZIP file of the originals, what was
read from them and the chats the member may read, tells them, and serves it to them alone until
it expires; the trash, quarantined files and other members' private chats stay out."""

import io
import json
import zipfile
from datetime import UTC, datetime, timedelta

import pytest
from pydantic_ai.messages import TextPart
from sqlalchemy import select, update

from app.api.exports import EXPORT_GONE, MEMBERS_EXPORT, UNDER_WAY
from app.export import EXPORT_DAYS, EXPORT_FAILED, PROJECT_ABOUT, PROJECT_GONE
from app.indexing.pipeline import process_document
from app.jobs import EXPORT_PROJECT, claim
from app.maintenance import housekeeping
from app.models import Job, Membership, Notification, Organization, Passage, ProjectExport
from app.notices import app_address
from app.storage import StorageError, export_key
from app.worker import Worker
from tests.conftest import EICAR, FakeTurn
from tests.test_api import create_project, upload
from tests.test_audit import actions
from tests.test_auth import PASSWORD, another_browser, signup
from tests.test_chats import answered, new_chat
from tests.test_folders import make_folder
from tests.test_maintenance import age
from tests.test_organizations import join


def ask(client, project_id: int):
    return client.post(f"/api/projects/{project_id}/exports")


def run_export(services) -> Job:
    """Run the next export job as the worker does."""
    worker = Worker(services, concurrency=1)
    job = claim(worker.id, [EXPORT_PROJECT])
    assert job is not None
    worker._run(job)
    return job


def exports(client, project_id: int) -> list[dict]:
    response = client.get(f"/api/projects/{project_id}/exports")
    assert response.status_code == 200, response.text
    return response.json()


def archive_of(client, project_id: int, export_id: int) -> zipfile.ZipFile:
    response = client.get(f"/api/projects/{project_id}/exports/{export_id}/file")
    assert response.status_code == 200, response.text
    return zipfile.ZipFile(io.BytesIO(response.content))


@pytest.fixture
def pair(client, session, signed_in_only):
    """Ada, signed in on `client`, the owner of her organisation on Team, and Bob on a browser of
    his own, a member of it switched into it."""
    ada = signup(client, "ada@example.com", name="Ada")
    bob_client = another_browser(client)
    bob = signup(bob_client, "bob@example.com", name="Bob")
    org_id = ada["organization"]["id"]
    session.execute(
        update(Organization).where(Organization.id == org_id).values(plan="team", seats=5)
    )
    join(session, org_id, bob["id"])
    bob_client.post(f"/api/organizations/{org_id}/switch")
    return org_id, bob_client, bob


def test_a_member_exports_a_project_and_downloads_it(
    client, session, services, mailer, pair, sample_pdf, sample_docx
):
    org_id, bob_client, bob = pair
    project = create_project(client, "Supplier contracts")
    contract, notes = upload(client, project["id"], sample_pdf, sample_docx)["documents"]
    for document in (contract, notes):
        process_document(document["id"], services)
    # A folder, which the archive keeps; and a document in the trash, which it leaves out.
    signed = make_folder(client, project["id"], "Signed")
    folder = make_folder(client, project["id"], "2026", signed["id"])
    moved = client.patch(f"/api/documents/{contract['id']}", json={"folder_id": folder["id"]})
    assert moved.status_code == 200, moved.text
    assert client.delete(f"/api/documents/{notes['id']}").status_code == 204
    # Ada's own chat and Bob's shared one go in; Bob's private one does not.
    services.llm.agent_turns = [FakeTurn(TextPart("It runs for a year."))]
    answered(client, services, new_chat(client, project["id"])["id"], "How long does it run?")
    shared = new_chat(bob_client, project["id"])
    services.llm.agent_turns = [FakeTurn(TextPart("Thirty days."))]
    answered(bob_client, services, shared["id"], "What is the notice period?")
    bob_client.patch(f"/api/chats/{shared['id']}", json={"visibility": "project"})
    services.llm.agent_turns = [FakeTurn(TextPart("Nothing."))]
    answered(bob_client, services, new_chat(bob_client, project["id"])["id"], "Private?")
    mailer.sent.clear()

    asked = ask(client, project["id"])
    assert asked.status_code == 202, asked.text
    assert asked.json()["status"] == "queued" and asked.json()["expires_at"] is None
    again = ask(client, project["id"])
    assert again.status_code == 409 and again.json()["detail"] == UNDER_WAY
    assert ("project_exported", {"name": "Supplier contracts"}) in actions(session, org_id)

    run_export(services)
    (made,) = exports(client, project["id"])
    assert made["status"] == "ready" and made["documents"] == 1 and made["error"] is None
    expires = datetime.fromisoformat(made["expires_at"])
    assert abs(expires - (datetime.now(UTC) + timedelta(days=EXPORT_DAYS))) < timedelta(minutes=1)
    ((to, subject, body),) = [sent for sent in mailer.sent if sent[1].startswith("Your project")]
    assert (to, subject) == ("ada@example.com", "Your project export is ready")
    assert f"{app_address()}/?export={project['id']}&organization={org_id}" in body
    told = session.scalars(select(Notification).where(Notification.kind == "project_export"))
    assert [(n.title, n.link) for n in told] == [
        ("Your project export is ready", f"/?export={project['id']}")
    ]

    response = client.get(f"/api/projects/{project['id']}/exports/{made['id']}/file")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    named = f"Supplier%20contracts%20{datetime.now(UTC):%Y-%m-%d}.zip"
    assert response.headers["content-disposition"] == f"attachment; filename*=utf-8''{named}"
    assert made["size_bytes"] == len(response.content)
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    original = f"originals/Signed/2026/{contract['filename']}"
    assert sorted(archive.namelist()) == sorted(
        [original, f"documents/{contract['id']}.json", "project.json"]
    )
    assert archive.read(original) == sample_pdf.read_bytes()

    listing = json.loads(archive.read("project.json"))
    assert listing["about"] == PROJECT_ABOUT
    assert listing["project"]["name"] == "Supplier contracts"
    assert listing["exported_by"] == {"name": "Ada", "email": "ada@example.com"}
    assert listing["folders"] == ["Signed", "Signed/2026"]
    (document,) = listing["documents"]
    assert (document["id"], document["folder"], document["original"]) == (
        contract["id"],
        "Signed/2026",
        original,
    )
    assert sorted(chat["started_by"] for chat in listing["chats"]) == ["Ada", "Bob"]
    assert [m["content"] for m in listing["chats"][0]["messages"]] == [
        "How long does it run?",
        "It runs for a year.",
    ]
    data = json.loads(archive.read(f"documents/{contract['id']}.json"))
    passages = session.scalars(
        select(Passage.id).where(Passage.document_id == contract["id"]).order_by(Passage.ordinal)
    ).all()
    assert [passage["id"] for passage in data["passages"]] == passages
    assert data["sections"] and all("embedding" not in p for p in data["passages"])


def test_a_viewer_is_refused_and_nobody_else_finds_a_members_export(
    client, session, services, pair, sample_pdf
):
    org_id, bob_client, bob = pair
    project = create_project(client)
    upload(client, project["id"], sample_pdf)
    made = ask(client, project["id"]).json()
    run_export(services)

    assert exports(bob_client, project["id"]) == []
    gone = bob_client.get(f"/api/projects/{project['id']}/exports/{made['id']}/file")
    assert gone.status_code == 404 and gone.json()["detail"] == EXPORT_GONE
    session.execute(update(Membership).where(Membership.user_id == bob["id"]).values(role="viewer"))
    session.commit()
    refused = ask(bob_client, project["id"])
    assert refused.status_code == 403 and refused.json()["detail"] == MEMBERS_EXPORT


def test_an_export_expires_and_its_row_and_file_go(client, session, services, pair, sample_pdf):
    org_id, _, _ = pair
    project = create_project(client)
    upload(client, project["id"], sample_pdf)
    made = ask(client, project["id"]).json()
    run_export(services)
    key = export_key(org_id, project["id"], made["id"])
    assert services.storage.exists(key)

    session.execute(
        update(ProjectExport)
        .where(ProjectExport.id == made["id"])
        .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
    )
    session.commit()
    assert exports(client, project["id"]) == []
    gone = client.get(f"/api/projects/{project['id']}/exports/{made['id']}/file")
    assert gone.status_code == 404
    age(services.storage, key, days=EXPORT_DAYS + 1)
    housekeeping(services.storage)
    session.expire_all()
    assert session.get(ProjectExport, made["id"]) is None
    assert not services.storage.exists(key)


@pytest.mark.usefixtures("pair")
def test_an_export_of_a_project_put_in_the_trash_meanwhile_fails(client, session, services):
    project = create_project(client)
    made = ask(client, project["id"]).json()
    assert client.delete(f"/api/projects/{project['id']}").status_code == 204
    job = run_export(services)
    session.expire_all()
    export = session.get(ProjectExport, made["id"])
    assert (export.status, export.error) == ("failed", PROJECT_GONE)
    assert session.get(Job, job.id).status == "done"


@pytest.mark.usefixtures("pair")
def test_a_failed_export_says_so_and_reaches_the_operator(
    client, session, services, sample_pdf, monkeypatch
):
    project = create_project(client)
    upload(client, project["id"], sample_pdf)
    assert ask(client, project["id"]).status_code == 202

    def unavailable(key, source, content_type=None):
        raise StorageError("The storage is out of reach.", transient=True)

    monkeypatch.setattr(services.storage, "put", unavailable)
    job = run_export(services)
    (failed,) = exports(client, project["id"])
    assert (failed["status"], failed["error"]) == ("failed", EXPORT_FAILED)
    session.expire_all()
    stored = session.get(Job, job.id)
    assert stored.status == "failed" and "out of reach" in stored.last_error
    # Another may be asked for at once.
    assert ask(client, project["id"]).status_code == 202


@pytest.mark.usefixtures("pair")
def test_a_quarantined_file_is_left_out_and_an_unscanned_one_has_no_original(
    client, services, sample_pdf, tmp_path
):
    project = create_project(client)
    infected = tmp_path / "infected.txt"
    infected.write_bytes(EICAR)
    waiting = tmp_path / "waiting.txt"
    waiting.write_text("Not scanned yet.")
    scanned, flagged = upload(client, project["id"], sample_pdf, infected)["documents"]
    for document in (scanned, flagged):
        process_document(document["id"], services)
    upload(client, project["id"], waiting)
    made = ask(client, project["id"]).json()
    run_export(services)
    archive = archive_of(client, project["id"], made["id"])
    documents = json.loads(archive.read("project.json"))["documents"]
    assert {document["filename"]: document["original"] for document in documents} == {
        scanned["filename"]: f"originals/{scanned['filename']}",
        "waiting.txt": None,
    }
    assert f"documents/{flagged['id']}.json" not in archive.namelist()


@pytest.mark.usefixtures("pair")
def test_an_organisation_set_to_be_deleted_may_export(client):
    project = create_project(client)
    assert client.post("/api/organization/deletion", json={"password": PASSWORD}).status_code == 200
    assert ask(client, project["id"]).status_code == 202


def test_a_member_who_leaves_takes_their_exports_along(client, session, services, pair):
    _, bob_client, _ = pair
    project = create_project(client)
    assert ask(bob_client, project["id"]).status_code == 202
    assert ask(client, project["id"]).status_code == 202
    assert bob_client.post("/api/organization/leave").status_code == 200
    session.expire_all()
    assert [export.user_id for export in session.scalars(select(ProjectExport))] == [
        session.scalar(select(Membership.user_id).where(Membership.role == "owner"))
    ]
    # Their job finds nothing left to make.
    job = run_export(services)
    session.expire_all()
    assert session.get(Job, job.id).status == "done"
