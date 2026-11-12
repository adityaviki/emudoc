"""My files (app/my_files.py, app/api/my_files.py): each member's own Word and Excel files in a
project, seen by them alone, kept with their versions, scanned and shown as they look by a job, and
copied into the project's library as a new document or as the next version of the one a file was
copied from."""

import io
from datetime import timedelta

import pytest
from docx import Document as WordDocument
from openpyxl import Workbook
from sqlalchemy import func, select

from app.config import get_settings
from app.conversion import GAVE_UP, MAX_TRIES, UNSHOWN, ConvertError
from app.db import engine
from app.indexing.pipeline import process_document
from app.jobs import VIEW_MY_FILE, claim
from app.maintenance import describe_orphans, sweep_storage
from app.models import Document, Job, MyFile, MyFileVersion, UsageEvent
from app.my_files import MAX_FILES, SAVED_MEANWHILE, WRONG_KIND, abandon_view
from app.storage import my_file_prefix
from app.usage import my_files_bytes
from app.worker import HANDLERS
from tests.conftest import SCANNER_DOWN
from tests.test_api import create_project
from tests.test_audit import actions as audited
from tests.test_conversion import FakeConverter
from tests.test_maintenance import age


@pytest.fixture
def project(client) -> dict:
    return create_project(client)


def word(*paragraphs: str) -> bytes:
    """A Word document of these paragraphs."""
    document = WordDocument()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def workbook(*rows: tuple) -> bytes:
    sheet = Workbook()
    for row in rows:
        sheet.active.append(row)
    out = io.BytesIO()
    sheet.save(out)
    return out.getvalue()


def keep(client, project_id: int, name: str = "Letter.docx", data: bytes | None = None):
    return client.post(
        f"/api/projects/{project_id}/my-files",
        files={"file": (name, data if data is not None else word("Dear Acme,"))},
    )


def kept(client, project_id: int, name: str = "Letter.docx", data: bytes | None = None) -> dict:
    response = keep(client, project_id, name, data)
    assert response.status_code == 201, response.text
    return response.json()


def shown(client, file_id: int) -> dict:
    response = client.get(f"/api/my-files/{file_id}")
    assert response.status_code == 200, response.text
    return response.json()


def actions(session) -> list[str]:
    return [action for action, _ in audited(session)]


def view(services) -> Job:
    """Claim the next version's job and run it as the worker would."""
    job = claim("w:1", [VIEW_MY_FILE])
    assert job is not None
    HANDLERS[VIEW_MY_FILE].run(job, services)
    return job


def queued(session) -> list[Job]:
    session.expire_all()
    return session.scalars(
        select(Job).where(Job.kind == VIEW_MY_FILE, Job.status == "queued").order_by(Job.id)
    ).all()


def test_a_member_keeps_a_word_file_scanned_before_it_is_served(client, session, services, project):
    data = word("Dear Acme,", "Your invoice INV-1 is 5 EUR over the order.")
    file = kept(client, project["id"], "Dispute.docx", data)
    assert (file["name"], file["kind"], file["versions"]) == ("Dispute.docx", "docx", 1)
    latest = file["latest"]
    assert (latest["number"], latest["by_model"], latest["note"]) == (1, False, "Uploaded")
    # The suite runs a scanner and no converter: the scan waits, and no view is made.
    assert (latest["scan"], latest["view_state"]) == ("pending", None)
    download = f"/api/my-files/{file['id']}/versions/1/file"
    assert client.get(download).status_code == 409

    view(services)
    assert shown(client, file["id"])["latest"]["scan"] == "passed"
    response = client.get(download)
    assert response.status_code == 200
    assert response.content == data
    assert "Dispute.docx" in response.headers["content-disposition"]
    listed = client.get(f"/api/projects/{project['id']}/my-files").json()
    assert ([f["name"] for f in listed["files"]], listed["limit"]) == (["Dispute.docx"], MAX_FILES)
    assert actions(session)[-2:] == ["my_file_added", "my_file_downloaded"]
    # Without a scanner the file is served at once.
    services.scanner = None
    plain = kept(client, project["id"], "Memo.docx", word("A memo."))
    assert plain["latest"]["scan"] == "skipped"
    assert client.get(f"/api/my-files/{plain['id']}/versions/1/file").status_code == 200


def test_a_file_is_shown_as_it_looks(client, session, services, project, sample_pdf):
    services.converter = FakeConverter(sample_pdf)
    file = kept(client, project["id"], "Budget.xlsx", workbook(("Item", "Cost"), ("Pallets", 40)))
    assert file["latest"]["view_state"] == "queued"
    page = f"/api/my-files/{file['id']}/versions/1/pages/1.png"
    assert client.get(page).status_code == 409  # waiting for its scan

    view(services)
    latest = shown(client, file["id"])["latest"]
    assert (latest["scan"], latest["view_state"]) == ("passed", "ready")
    assert len(latest["view_page_sizes"]) >= 1
    assert services.converter.converted == ["xlsx"]
    drawn = client.get(page)
    assert drawn.status_code == 200
    assert drawn.headers["content-type"] == "image/png"
    assert client.get(page).status_code == 200
    pages = session.scalar(
        select(func.sum(UsageEvent.quantity)).where(
            UsageEvent.kind == "render", UsageEvent.project_id == project["id"]
        )
    )
    assert pages == 1  # drawn once, served again from the storage
    beyond = f"/api/my-files/{file['id']}/versions/1/pages/99.png"
    assert client.get(beyond).status_code == 404

    # A file the converter refused says so, and "Try again" asks again.
    services.converter.refusal = ConvertError("Not a workbook.", members="Not a workbook.")
    again = kept(client, project["id"], "Broken.xlsx", workbook(("x",)))
    view(services)
    latest = shown(client, again["id"])["latest"]
    assert (latest["view_state"], latest["view_error"]) == ("failed", "Not a workbook.")
    services.converter.refusal = None
    retried = client.post(f"/api/my-files/{again['id']}/versions/1/view").json()
    assert retried["latest"]["view_state"] == "queued"
    view(services)
    assert shown(client, again["id"])["latest"]["view_state"] == "ready"


def test_a_flagged_file_is_never_served(client, services, project):
    services.scanner.flagging = True
    file = kept(client, project["id"])
    view(services)
    latest = shown(client, file["id"])["latest"]
    assert (latest["scan"], latest["view_state"]) == ("flagged", "failed")
    assert latest["view_error"].startswith("The malware scan flagged this file as")
    for response in (
        client.get(f"/api/my-files/{file['id']}/versions/1/file"),
        client.get(f"/api/my-files/{file['id']}/versions/1/pages/1.png"),
        client.post(f"/api/my-files/{file['id']}/copy", json={}),
    ):
        assert response.status_code == 409, response.text
        assert "flagged" in response.json()["detail"]
    # Nothing brings it back: "Try again" does nothing, and deleting it is the way.
    assert client.post(f"/api/my-files/{file['id']}/versions/1/view").status_code == 200
    assert shown(client, file["id"])["latest"]["scan"] == "flagged"


def test_a_scanner_out_of_reach_is_asked_again_then_gives_up(
    client, session, services, project, monkeypatch
):
    # A deployment's members are told the members' sentence, not the operator's.
    monkeypatch.setattr(get_settings(), "environment", "production")
    services.scanner.outage = SCANNER_DOWN
    file = kept(client, project["id"])
    for tries in range(1, MAX_TRIES):
        view(services)
        (job,) = queued(session)
        assert job.payload == {"version_id": job.payload["version_id"], "try": tries + 1}
        session.execute(Job.__table__.update().where(Job.id == job.id).values(run_at=func.now()))
        session.commit()
    view(services)
    latest = shown(client, file["id"])["latest"]
    assert (latest["scan"], latest["view_state"], latest["view_error"]) == (
        "pending",
        "failed",
        GAVE_UP,
    )
    services.scanner.outage = None
    client.post(f"/api/my-files/{file['id']}/versions/1/view")
    view(services)
    assert shown(client, file["id"])["latest"]["scan"] == "passed"


def test_a_lost_job_leaves_the_view_failed_to_ask_again(
    client, session, services, project, sample_pdf
):
    services.converter = FakeConverter(sample_pdf)
    file = kept(client, project["id"])
    (job,) = queued(session)
    abandon_view(job)
    latest = shown(client, file["id"])["latest"]
    assert (latest["view_state"], latest["view_error"]) == ("failed", UNSHOWN)


def test_only_word_documents_and_excel_workbooks_are_kept(client, project, sample_pdf):
    refused = keep(client, project["id"], "contract.pdf", sample_pdf.read_bytes())
    assert (refused.status_code, refused.json()["detail"]) == (400, WRONG_KIND)
    # The bytes must be the type the name says, and there must be some.
    assert keep(client, project["id"], "fake.docx", b"plain text").status_code == 400
    assert keep(client, project["id"], "empty.xlsx", b"").status_code == 400
    sheet = kept(client, project["id"], "Prices.XLSX", workbook(("Pallet", 12.5)))
    assert (sheet["name"], sheet["kind"]) == ("Prices.xlsx", "xlsx")

    # A rename keeps the extension of the file's kind whatever is typed.
    renamed = client.patch(f"/api/my-files/{sheet['id']}", json={"name": "Price list"})
    assert renamed.json()["name"] == "Price list.xlsx"
    renamed = client.patch(f"/api/my-files/{sheet['id']}", json={"name": "Old.xlsx"})
    assert renamed.json()["name"] == "Old.xlsx"


def test_a_file_keeps_every_version(client, session, services, project):
    services.scanner = None
    file = kept(client, project["id"], "Letter.docx", word("First draft."))
    versions = f"/api/my-files/{file['id']}/versions"
    second = word("Second draft.")
    given = client.post(
        versions, files={"file": ("Letter (edited).docx", second)}, data={"base": 1}
    )
    assert given.status_code == 201, given.text
    body = given.json()
    assert (body["versions"], body["name"]) == (2, "Letter.docx")
    assert [version["number"] for version in body["history"]] == [2, 1]
    assert client.get(f"{versions}/2/file").content == second
    assert client.get(f"{versions}/1/file").status_code == 200
    assert client.get(f"{versions}/3/file").status_code == 404

    # A version made from one that is no longer the newest is refused, the member's file kept.
    stale = client.post(versions, files={"file": ("Letter.docx", word("Third."))}, data={"base": 1})
    assert (stale.status_code, stale.json()["detail"]) == (409, SAVED_MEANWHILE)
    other = client.post(versions, files={"file": ("Letter.xlsx", workbook(("x",)))})
    assert other.status_code == 400
    assert "must be one too" in other.json()["detail"]
    assert shown(client, file["id"])["versions"] == 2
    assert actions(session).count("my_file_version_added") == 1


def test_a_file_is_its_members_alone(client, services, project, account, make_account, act_as):
    services.scanner = None
    file = kept(client, project["id"])
    for role in ("admin", "member", "viewer"):
        act_as(make_account(f"{role}@example.com", account.organization, role=role))
        assert client.get(f"/api/projects/{project['id']}/my-files").json()["files"] == []
        assert client.get(f"/api/my-files/{file['id']}").status_code == 404
        assert client.get(f"/api/my-files/{file['id']}/versions/1/file").status_code == 404
        assert client.delete(f"/api/my-files/{file['id']}").status_code == 404

    # A viewer keeps files of their own, changing nothing the members share, but copying one
    # into the library adds a document, which a viewer may not.
    own = kept(client, project["id"], "Notes.docx")
    assert [
        f["id"] for f in client.get(f"/api/projects/{project['id']}/my-files").json()["files"]
    ] == [own["id"]]
    assert client.post(f"/api/my-files/{own['id']}/copy", json={}).status_code == 403


def test_copying_a_file_into_the_library(client, session, services, project):
    services.scanner = None
    folder = client.post(f"/api/projects/{project['id']}/folders", json={"name": "Letters"}).json()
    file = kept(client, project["id"], "Dispute.docx", word("Dear Acme, the invoice is wrong."))
    copied = client.post(f"/api/my-files/{file['id']}/copy", json={"folder_id": folder["id"]})
    assert copied.status_code == 201, copied.text
    document = copied.json()
    assert (document["filename"], document["folder_id"]) == ("Dispute.docx", folder["id"])
    process_document(document["id"], services)
    session.expire_all()
    assert session.get(Document, document["id"]).status == "ready"
    # The file stays in My files, and the library holds it once.
    assert shown(client, file["id"])["versions"] == 1
    again = client.post(f"/api/my-files/{file['id']}/copy", json={})
    assert again.status_code == 409
    assert "Already in this project" in again.json()["detail"]
    assert actions(session)[-2:] == ["document_uploaded", "my_file_copied"]


def test_copying_a_file_back_as_its_documents_next_version(client, session, services, project):
    services.scanner = None
    uploaded = client.post(
        f"/api/projects/{project['id']}/documents",
        files=[("files", ("policy.docx", word("Book flights 14 days ahead.")))],
    ).json()["documents"][0]
    process_document(uploaded["id"], services)
    file = kept(client, project["id"], "policy.docx", word("Book flights 21 days ahead."))
    # The agent's copy of a document names it (the next checklist item); here by hand.
    session.execute(
        MyFile.__table__.update()
        .where(MyFile.id == file["id"])
        .values(origin_document_id=uploaded["id"])
    )
    session.commit()
    assert shown(client, file["id"])["origin_name"] == "policy.docx"

    given = client.post(f"/api/my-files/{file['id']}/copy", json={"as_version": True})
    assert given.status_code == 201, given.text
    assert (given.json()["id"], given.json()["version"]) == (uploaded["id"], 2)
    assert "document_version_uploaded" in actions(session)

    # A file whose document is gone from the library has nothing to copy back to.
    process_document(uploaded["id"], services)
    assert client.delete(f"/api/documents/{uploaded['id']}").status_code == 204
    assert shown(client, file["id"])["origin_name"] is None
    refused = client.post(f"/api/my-files/{file['id']}/copy", json={"as_version": True})
    assert refused.status_code == 409
    alone = kept(client, project["id"], "Mine.docx", word("Mine."))
    assert (
        client.post(f"/api/my-files/{alone['id']}/copy", json={"as_version": True}).status_code
        == 409
    )


def test_deleting_a_file_takes_its_versions_and_their_files(client, session, services, project):
    services.scanner = None
    file = kept(client, project["id"])
    client.post(f"/api/my-files/{file['id']}/versions", files={"file": ("Letter.docx", word("2"))})
    row = session.get(MyFile, file["id"])
    prefix = my_file_prefix(row.org_id, row.project_id, row.id)
    assert len(list(services.storage.list(prefix))) == 2
    assert client.delete(f"/api/my-files/{file['id']}").status_code == 204
    assert client.get(f"/api/my-files/{file['id']}").status_code == 404
    assert list(services.storage.list(prefix)) == []
    session.expire_all()
    assert session.scalar(select(func.count(MyFileVersion.id))) == 0
    assert actions(session)[-1] == "my_file_deleted"


def test_my_files_count_in_the_workspaces_storage(client, session, services, account):
    services.scanner = None
    project = create_project(client)
    data = word("A letter.")
    kept(client, project["id"], data=data)
    assert my_files_bytes(session, account.organization.id) == len(data)
    assert client.get("/api/organization/usage").json()["storage_bytes"] == len(data)


def test_a_member_keeps_no_more_files_than_the_ceiling(client, project, monkeypatch, services):
    services.scanner = None
    monkeypatch.setattr("app.my_files.MAX_FILES", 1)
    kept(client, project["id"])
    refused = keep(client, project["id"], "Second.docx")
    assert refused.status_code == 409
    assert "as many as it keeps" in refused.json()["detail"]


def test_a_member_who_leaves_takes_their_files(
    client, session, services, project, account, make_account, act_as
):
    services.scanner = None
    leaving = make_account("lee@example.com", account.organization, role="member")
    act_as(leaving)
    kept(client, project["id"])
    client.app.dependency_overrides.clear()
    assert client.delete(f"/api/organization/members/{leaving.user.id}").status_code == 204
    session.expire_all()
    assert session.scalar(select(func.count(MyFile.id))) == 0


def test_the_storage_walk_sweeps_the_files_of_a_file_whose_row_is_gone(
    session, client, services, project
):
    services.scanner = None
    live = kept(client, project["id"], "Live.docx")
    row = session.get(MyFile, live["id"])
    storage = services.storage
    gone = f"{my_file_prefix(row.org_id, row.project_id, 999)}versions/1/original.docx"
    storage.put(gone, b"x" * 10)
    for key in (row_key := session.scalar(select(MyFileVersion.storage_key)), gone):
        age(storage, key, days=2)
    with engine.begin() as connection:
        sweep = sweep_storage(storage, connection, keep_images=timedelta(days=30))
    assert [orphan.prefix for orphan in sweep.orphans] == [
        my_file_prefix(row.org_id, row.project_id, 999)
    ]
    assert describe_orphans(sweep.orphans, "Found") == (
        "Found 1 object (0.0 MB) of 1 file of My files that no longer exists"
    )
    assert storage.exists(row_key)


def test_the_account_export_lists_the_members_files_and_nobody_elses(
    client, services, project, account, make_account, act_as
):
    services.scanner = None
    file = kept(client, project["id"], "Mine.docx")
    client.post(f"/api/my-files/{file['id']}/versions", files={"file": ("Mine.docx", word("2"))})
    act_as(make_account("bo@example.com", account.organization, role="member"))
    kept(client, project["id"], "Bo's.docx")
    act_as(account)
    exported = client.get("/api/auth/account/export")
    assert exported.status_code == 200, exported.text
    (listed,) = exported.json()["my_files"]
    assert (listed["name"], listed["kind"], listed["project"]) == ("Mine.docx", "docx", "Contracts")
    assert [version["number"] for version in listed["versions"]] == [1, 2]
