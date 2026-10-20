"""The sample project a sign-up's organisation starts with (app/sample): the shipped index holds
together and describes its files, a sign-up copies it ready to ask about, its index jobs embed it,
it counts against nothing the plan holds and takes no uploads, and the builder writes an index
from the pipeline's own rows."""

import hashlib
import json
import logging
import shutil
from dataclasses import replace

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.entitlements import PLANS
from app.indexing.pipeline import process_document
from app.jobs import LIVE
from app.legal import TERMS_VERSION
from app.models import AuditEvent, Document, Job, Organization, Passage, Project, UsageEvent
from app.sample import (
    DESCRIPTION,
    FILES,
    NAME,
    NOTHING_TO_SCAN,
    QUESTIONS,
    SAMPLE_UPLOADS,
    build_sample,
    check_sample,
    sample_files,
    written,
)
from app.storage import StorageError, project_prefix
from tests.conftest import FakeLLM
from tests.test_auth import another_browser, callback, google_claims, signup, start
from tests.test_auth import oauth as oauth  # noqa: F401  the fixture, by name
from tests.test_invitations import invite, sign_up_invited

FILE_COUNT = 8


@pytest.fixture
def sample_on(monkeypatch):
    """Sign-ups start with the sample project, as a deployment's do."""
    monkeypatch.setattr(get_settings(), "sample_project", True)


def projects(client) -> list[dict]:
    response = client.get("/api/projects")
    assert response.status_code == 200, response.text
    return response.json()


def documents_of(client, project_id: int) -> list[dict]:
    response = client.get(f"/api/projects/{project_id}/documents")
    assert response.status_code == 200, response.text
    return response.json()["documents"]


def the_sample(client) -> dict:
    (sample,) = [project for project in projects(client) if project["sample"]]
    return sample


def test_the_index_describes_the_files_beside_it_and_holds_together():
    files = sample_files()
    assert [file.name for file in files] == sorted(path.name for path in FILES.iterdir())
    assert len(files) == FILE_COUNT
    for file in files:
        entry = file.index
        assert hashlib.sha256(file.data).hexdigest() == entry["sha256"]
        assert all(entry["stages"][name]["status"] == "done" for name in entry["stages"])
        blocks = entry["blocks"]
        assert blocks and entry["passages"] and entry["sections"] and entry["facts"]
        for place, section in enumerate(entry["sections"]):
            assert section["parent"] is None or section["parent"] < place
            assert 0 <= section["block_start"] < section["block_end"] <= len(blocks)
            assert section["summary"], (file.name, section["title"])
        for passage in entry["passages"]:
            assert 0 <= passage["section"] < len(entry["sections"])
            assert 0 <= passage["block_start"] < passage["block_end"] <= len(blocks)
        for fact in entry["facts"]:
            # Every quote was found in its block by the enrich stage, as a model's must be.
            assert fact["verified"], (file.name, fact["name"])
            assert 0 <= fact["block_ordinal"] < len(blocks)
            # None for a fact of a heading, which no passage holds.
            if fact["passage"] is not None:
                passage = entry["passages"][fact["passage"]]
                assert passage["block_start"] <= fact["block_ordinal"] < passage["block_end"]
        assert entry["title"] and entry["doc_type"] and entry["summary"]


def test_a_sign_up_starts_with_the_sample_ready_to_ask_about(
    client, session, services, sample_on, signed_in_only
):
    signup(client)
    sample = the_sample(client)
    assert sample["name"] == NAME and sample["description"] == DESCRIPTION
    assert sample["suggestions"] == list(QUESTIONS)
    assert sample["document_count"] == FILE_COUNT and sample["visibility"] == "organization"

    documents = documents_of(client, sample["id"])
    by_name = {file.name: file for file in sample_files()}
    assert [document["filename"] for document in documents] == list(by_name)
    for document in documents:
        entry = by_name[document["filename"]].index
        # Ready, with everything but its vectors, which its index job makes.
        assert document["status"] == "ready"
        assert document["title"] == entry["title"] and document["doc_type"] == entry["doc_type"]
        assert document["stages"]["scan"] == {"status": "skipped", "detail": NOTHING_TO_SCAN}
        for name in ("parse", "structure", "enrich"):
            assert document["stages"][name] == entry["stages"][name]
        assert document["stages"]["embed"]["status"] == "pending"
        # The file is served, as an upload's is once scanned.
        served = client.get(f"/api/documents/{document['id']}/file")
        assert served.status_code == 200 and served.content == by_name[document["filename"]].data

    contract = documents[0]
    outline = client.get(f"/api/documents/{contract['id']}/outline").json()
    assert [section["title"] for section in outline][1:3] == [
        "1. Scope of Services",
        "2. Fees and Payment",
    ]
    facts = client.get(f"/api/projects/{sample['id']}/facts", params={"name": "liability_cap"})
    (cap,) = facts.json()["facts"]
    assert cap["value"] == "EUR 250,000" and cap["verified"] and cap["passage_id"] is not None
    # Found by its words before a vector is made.
    hits = client.get(f"/api/projects/{sample['id']}/search", params={"q": "volume discount"})
    assert hits.status_code == 200
    assert contract["id"] in {hit["document_id"] for hit in hits.json()["hits"]}

    # The vectors are the deployment's: one job per document, before the uploads waiting.
    jobs = session.scalars(select(Job).where(Job.kind == "index_document")).all()
    assert sorted(job.payload["document_id"] for job in jobs) == sorted(d["id"] for d in documents)
    assert {job.priority for job in jobs} == {LIVE}
    assert {job.org_id for job in jobs} == {session.scalar(select(Organization.id))}


def test_its_index_jobs_embed_it_alone(client, session, services, sample_on, signed_in_only):
    signup(client)
    sample = the_sample(client)
    ids = [document["id"] for document in documents_of(client, sample["id"])]
    for document_id in ids:
        assert process_document(document_id, services) == []

    for document in documents_of(client, sample["id"]):
        assert document["status"] == "ready"
        assert document["stages"]["embed"]["status"] == "done"
    assert not session.scalar(
        select(Passage.id).where(Passage.project_id == sample["id"], Passage.embedding.is_(None))
    )
    # Embedding is all the copy cost: no pages of the month, no model's call.
    kinds = session.scalars(select(UsageEvent.kind).distinct()).all()
    assert kinds == ["embed"]
    assert not services.llm.calls
    # Ready as it was made: the service level measures the uploads' wait alone.
    assert all(
        document.ready_at is not None
        for document in session.scalars(select(Document).where(Document.id.in_(ids)))
    )


def test_it_counts_against_nothing_the_plan_holds_and_takes_no_uploads(
    client, session, services, sample_on, signed_in_only, sample_pdf
):
    signup(client)
    sample = the_sample(client)
    usage = client.get("/api/organization/usage").json()
    assert usage["projects"]["used"] == 0 and usage["documents"]["used"] == 0
    assert usage["storage_bytes"]["used"] == 0 and usage["sample_documents"] == FILE_COUNT
    assert client.get("/api/auth/me").json()["organization"]["allowance"]["over"] == []

    with sample_pdf.open("rb") as handle:
        refused = client.post(
            f"/api/projects/{sample['id']}/documents",
            files=[("files", ("contract.pdf", handle, "application/pdf"))],
        )
    assert refused.status_code == 409 and refused.json()["detail"] == SAMPLE_UPLOADS
    assert len(documents_of(client, sample["id"])) == FILE_COUNT

    # The free plan's one project is the member's own.
    made = client.post("/api/projects", json={"name": "Contracts"})
    assert made.status_code == 201, made.text
    assert made.json()["sample"] is False and made.json()["suggestions"] == []
    assert client.post("/api/projects", json={"name": "More"}).status_code == 402

    # Deleting it is deleting a project.
    org_id = client.get("/api/auth/me").json()["organization"]["id"]
    assert list(services.storage.list(project_prefix(org_id, sample["id"])))
    assert client.delete(f"/api/projects/{sample['id']}").status_code == 204
    assert client.get("/api/organization/usage").json()["sample_documents"] == 0
    assert not list(services.storage.list(project_prefix(org_id, sample["id"])))


def test_the_copy_is_the_apps_own_act_in_the_audit_log(client, session, sample_on, signed_in_only):
    signup(client)
    sample = the_sample(client)
    rows = session.scalars(select(AuditEvent).order_by(AuditEvent.id)).all()
    assert [row.action for row in rows] == ["sign_up", "terms_accepted", "sample_added"]
    added = rows[-1]
    assert added.user_id is None and added.project_id == sample["id"]
    assert (added.target_type, added.target_id) == ("project", sample["id"])
    assert added.detail == {"name": NAME, "documents": FILE_COUNT}


def test_a_sign_up_through_a_provider_starts_with_it_too(
    client, identity_provider, sample_on, signed_in_only, request
):
    request.getfixturevalue("oauth")
    _, handshake = start(client)
    identity_provider.claims = google_claims(handshake.nonce)
    assert callback(client, code="the-code", state=handshake.state).status_code == 302
    # Accepted on the page a provider's sign-up lands on, before anything else is shown.
    assert client.post("/api/auth/terms", json={"version": TERMS_VERSION}).status_code == 200
    assert the_sample(client)["document_count"] == FILE_COUNT


def test_only_an_organisation_made_at_sign_up_has_it(
    client, mailer, session, sample_on, signed_in_only, monkeypatch
):
    monkeypatch.setitem(PLANS, "free", replace(PLANS["free"], members=2))
    signup(client, "ada@acme.com")
    invite(client, "bob@acme.com")
    bob = another_browser(client)
    joined = sign_up_invited(bob, mailer.token())
    assert joined.status_code == 201, joined.text
    # One sample, Ada's, which Bob finds in the organisation he joined.
    assert len(session.scalars(select(Project).where(Project.sample.is_(True))).all()) == 1
    assert the_sample(bob)["document_count"] == FILE_COUNT
    # An organisation started later is no sign-up.
    started = client.post("/api/organizations", json={"name": "Second"})
    assert started.status_code == 201, started.text
    assert projects(client) == []


def test_without_the_setting_a_sign_up_starts_with_nothing(client, signed_in_only):
    signup(client)
    assert projects(client) == []


def test_a_storage_that_refuses_the_files_leaves_the_account_without_it(
    client, session, services, sample_on, signed_in_only, monkeypatch, caplog
):
    def refused(*_args, **_kwargs):
        raise StorageError("The storage is out of reach.", transient=True)

    monkeypatch.setattr(services.storage, "put", refused)
    with caplog.at_level(logging.WARNING, logger="app.sample"):
        signup(client)
    assert projects(client) == []
    assert not session.scalars(select(Job).where(Job.kind == "index_document")).all()
    assert "starts without the sample project: The storage is out of reach." in caplog.text


def test_a_start_refuses_a_sample_whose_files_changed(monkeypatch, tmp_path):
    files = tmp_path / "files"
    shutil.copytree(FILES, files)
    monkeypatch.setattr("app.sample.FILES", files)
    sample_files.cache_clear()
    try:
        check_sample(get_settings())  # the suite's setting is off
        monkeypatch.setattr(get_settings(), "sample_project", True)
        check_sample(get_settings())
        (files / "price-list.csv").write_text("sku,price\nX,1\n")
        sample_files.cache_clear()
        with pytest.raises(RuntimeError, match="price-list.csv is not the file"):
            check_sample(get_settings())
        (files / "notes.md").write_text("# Notes\n")
        sample_files.cache_clear()
        with pytest.raises(RuntimeError, match="its files are"):
            check_sample(get_settings())
    finally:
        sample_files.cache_clear()


def test_the_builder_writes_the_index_from_the_pipelines_rows(session, monkeypatch):
    monkeypatch.setattr(get_settings(), "prerender_pages", 0)
    index = build_sample(get_settings(), FakeLLM())
    assert index["built"]["models"] == [get_settings().index_model]
    built = {entry["filename"]: entry for entry in index["documents"]}
    shipped = {file.name: file.index for file in sample_files()}
    assert built.keys() == shipped.keys()
    for name, entry in built.items():
        # The same files, through the same stages, summarised by the fake.
        assert entry["sha256"] == shipped[name]["sha256"]
        assert {stage["status"] for stage in entry["stages"].values()} == {"done"}
        assert entry["blocks"] and entry["passages"]
        assert all(
            section["summary"].startswith("Summary of section ") for section in entry["sections"]
        )
        assert entry["title"] == "Master Services Agreement"
    # As it is kept: JSON, a row to a line.
    text = written(index)
    assert json.loads(text) == index
    assert text.count("\n") > sum(len(entry["blocks"]) for entry in index["documents"])
    # The run's organisation, and what it recorded, went with it.
    assert not session.scalars(select(Organization)).all()
    assert not session.scalars(select(UsageEvent)).all()
