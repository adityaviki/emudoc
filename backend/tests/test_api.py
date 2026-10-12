from io import BytesIO
from pathlib import Path

from PIL import Image

from app.indexing.pipeline import process_document
from app.parsing import images
from tests.conftest import MISSING_KEY


def create_project(client, name="Contracts"):
    response = client.post("/api/projects", json={"name": name, "description": "Supplier files"})
    assert response.status_code == 201
    return response.json()


def upload(client, project_id, *paths):
    files = [("files", (Path(path).name, Path(path).read_bytes())) for path in paths]
    response = client.post(f"/api/projects/{project_id}/documents", files=files)
    assert response.status_code == 201
    return response.json()


def test_project_crud(client):
    project = create_project(client)
    assert project["name"] == "Contracts" and project["document_count"] == 0

    assert [p["id"] for p in client.get("/api/projects").json()] == [project["id"]]
    renamed = client.patch(
        f"/api/projects/{project['id']}", json={"name": "Suppliers", "description": ""}
    ).json()
    assert renamed["name"] == "Suppliers"
    assert client.post("/api/projects", json={"name": ""}).status_code == 422

    assert client.delete(f"/api/projects/{project['id']}").status_code == 204
    assert client.get(f"/api/projects/{project['id']}").status_code == 404
    assert client.get("/api/projects").json() == []


def test_upload_queues_documents_and_rejects_what_it_cannot_take(client, sample_pdf, tmp_path):
    project = create_project(client)
    unsupported = tmp_path / "archive.zip"
    unsupported.write_bytes(b"data")
    empty = tmp_path / "empty.txt"
    empty.write_bytes(b"")

    result = upload(client, project["id"], sample_pdf, unsupported, empty)
    (document,) = result["documents"]
    assert document["filename"] == "msa.pdf"
    assert document["mime_type"] == "application/pdf"
    assert document["status"] == "queued"
    assert set(document["stages"]) == {"parse", "structure", "enrich", "embed"}
    assert {r["filename"]: r["reason"] for r in result["rejected"]} == {
        "archive.zip": (
            "Unsupported type. Use .csv, .docx, .jpeg, .jpg, .md, .pdf, .png, .txt, .webp, .xlsx."
        ),
        "empty.txt": "Empty file.",
    }

    # The same bytes again are recognised, whatever the file is called.
    again = upload(client, project["id"], sample_pdf)
    assert again["documents"] == []
    assert again["rejected"][0]["reason"] == "Already in this project as 'msa.pdf'."
    assert client.get(f"/api/projects/{project['id']}").json()["document_count"] == 1
    assert (
        client.post("/api/projects/999/documents", files=[("files", ("a.txt", b"x"))]).status_code
        == 404
    )


def test_a_simultaneous_upload_of_the_same_bytes_is_rejected_not_a_crash(
    client, session, make_document, monkeypatch, sample_pdf, sample_docx
):
    from app.api import documents as documents_api
    from app.models import Project

    project = create_project(client)
    choose = documents_api.unique_filename

    def after_another_request_commits(session_, project_id, folder_id, name):
        # Between the duplicate check and the insert, another request stores the same bytes;
        # the second file of the request must still go in after the first is refused.
        if name == sample_pdf.name:
            make_document(sample_pdf, filename="scan.pdf", project=session.get(Project, project_id))
        return choose(session_, project_id, folder_id, name)

    monkeypatch.setattr(documents_api, "unique_filename", after_another_request_commits)
    result = upload(client, project["id"], sample_pdf, sample_docx)
    assert [d["filename"] for d in result["documents"]] == ["policy.docx"]
    assert result["rejected"] == [
        {"filename": "msa.pdf", "reason": "Already in this project as 'scan.pdf'."}
    ]
    listed = client.get(f"/api/projects/{project['id']}/documents").json()
    assert [d["filename"] for d in listed] == ["policy.docx", "scan.pdf"]


def test_document_views_after_indexing(client, services, sample_pdf):
    project = create_project(client)
    document_id = upload(client, project["id"], sample_pdf)["documents"][0]["id"]
    process_document(document_id, services)

    document = client.get(f"/api/documents/{document_id}").json()
    assert document["status"] == "ready"
    assert document["title"] == "Master Services Agreement"
    assert document["stages"]["enrich"] == {
        "status": "done",
        "detail": "5 section summaries, 3 facts",
    }

    outline = client.get(f"/api/documents/{document_id}/outline").json()
    assert [node["title"] for node in outline] == [
        "Master Services Agreement",
        "1. Definitions",
        "2. Fees and payment",
        "3. Termination",
    ]
    assert [child["title"] for child in outline[1]["children"]] == ["1.1 Interpretation"]
    assert outline[3]["summary"].startswith("Summary of section")

    content = client.get(f"/api/documents/{document_id}/content").json()
    assert len(content["page_sizes"]) == 2
    assert content["blocks"][0]["kind"] == "heading"
    assert len(content["blocks"][0]["bbox"]) == 4

    facts = client.get(f"/api/documents/{document_id}/facts").json()
    notice = next(fact for fact in facts if fact["name"] == "notice_period")
    assert notice["number"] == 90 and notice["verified"] is True and notice["page"] == 2

    passage = client.get(f"/api/passages/{notice['passage_id']}").json()
    assert passage["document_id"] == document_id
    assert passage["document_title"] == "Master Services Agreement"
    assert "ninety (90) days" in passage["text"]
    assert passage["block_start"] <= notice["block_ordinal"] < passage["block_end"]

    page = client.get(f"/api/documents/{document_id}/pages/2.png")
    assert page.status_code == 200 and page.content.startswith(b"\x89PNG")
    assert client.get(f"/api/documents/{document_id}/pages/3.png").status_code == 404

    thumbnail = client.get(f"/api/documents/{document_id}/thumbnail.png")
    assert thumbnail.status_code == 200
    with Image.open(BytesIO(thumbnail.content)) as image:
        assert image.format == "PNG" and image.height == 320 and image.width < 320

    original = client.get(f"/api/documents/{document_id}/file")
    assert original.content == sample_pdf.read_bytes()
    assert original.headers["content-disposition"].startswith("inline")


def test_thumbnails_for_images_only_besides_pdfs(client, sample_docx, tmp_path):
    picture = tmp_path / "photo.png"
    Image.new("RGB", (800, 400), "white").save(picture)
    project = create_project(client)
    photo, policy = upload(client, project["id"], picture, sample_docx)["documents"]

    thumbnail = client.get(f"/api/documents/{photo['id']}/thumbnail.png")
    assert thumbnail.status_code == 200
    with Image.open(BytesIO(thumbnail.content)) as image:
        assert image.size == (320, 160)
    assert client.get(f"/api/documents/{policy['id']}/thumbnail.png").status_code == 404


def test_thumbnail_refuses_an_image_too_large_to_decode(client, tmp_path, monkeypatch):
    # The cap reads the size from the header before a pixel is decoded, and Pillow's own, higher
    # ceiling refuses the largest headers before the cap sees them; either way the document has
    # no thumbnail, like one whose file is damaged.
    monkeypatch.setattr(images, "MAX_IMAGE_PIXELS", 2_000_000)
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 3_000_000)
    large, huge = tmp_path / "large.png", tmp_path / "huge.png"
    Image.new("RGB", (2000, 1500), "white").save(large)
    Image.new("RGB", (2600, 2600), "white").save(huge)
    project = create_project(client)
    for document in upload(client, project["id"], large, huge)["documents"]:
        response = client.get(f"/api/documents/{document['id']}/thumbnail.png")
        assert response.status_code == 404
        assert response.json()["detail"] == "The file could not be rendered"


def test_search_and_facts_endpoints(client, services, sample_pdf, sample_docx):
    project = create_project(client)
    uploaded = upload(client, project["id"], sample_pdf, sample_docx)["documents"]
    for document in uploaded:
        process_document(document["id"], services)
    pdf_id = next(d["id"] for d in uploaded if d["filename"] == "msa.pdf")

    result = client.get(
        f"/api/projects/{project['id']}/search", params={"q": "termination notice"}
    ).json()
    assert result["semantic"] is True
    top = result["hits"][0]
    assert top["document_id"] == pdf_id and top["page_start"] == 2
    assert any(segment["match"] for segment in top["snippet"])

    filtered = client.get(
        f"/api/projects/{project['id']}/search", params={"q": "days", "document_id": pdf_id}
    ).json()
    assert {hit["document_id"] for hit in filtered["hits"]} == {pdf_id}

    names = client.get(f"/api/projects/{project['id']}/facts/names").json()
    assert {"name": "notice_period", "count": 1} in names
    facts = client.get(
        f"/api/projects/{project['id']}/facts",
        params={"name": "notice_period", "document_id": pdf_id},
    ).json()
    assert facts["total"] == 1 and facts["facts"][0]["value"] == "90 days"
    by_text = client.get(f"/api/projects/{project['id']}/facts", params={"q": "england"}).json()
    assert by_text["total"] == 1 and by_text["facts"][0]["verified"] is False
    assert client.get("/api/projects/999/search", params={"q": "x"}).status_code == 404


def test_reprocess_and_delete(client, services, sample_pdf):
    project = create_project(client)
    document_id = upload(client, project["id"], sample_pdf)["documents"][0]["id"]
    process_document(document_id, services)
    key = storage_key(document_id)
    assert key == f"orgs/1/projects/{project['id']}/documents/{document_id}/original.pdf"
    assert services.storage.exists(key)
    assert client.get(f"/api/documents/{document_id}/pages/1.png").status_code == 200
    prefix = key.rsplit("/", 1)[0] + "/"
    assert [entry.key for entry in services.storage.list(prefix)] == [key, f"{prefix}pages/1.png"]

    queued = client.post(f"/api/documents/{document_id}/reprocess", params={"from_stage": "enrich"})
    assert queued.status_code == 202
    body = queued.json()
    assert body["status"] == "queued"
    assert body["stages"]["parse"]["status"] == "done"
    assert body["stages"]["enrich"]["status"] == "pending"
    assert (
        client.post(
            f"/api/documents/{document_id}/reprocess", params={"from_stage": "x"}
        ).status_code
        == 422
    )
    whole_project = client.post(f"/api/projects/{project['id']}/reprocess").json()
    assert whole_project == {"queued": 1}

    assert client.delete(f"/api/documents/{document_id}").status_code == 204
    assert client.get(f"/api/documents/{document_id}").status_code == 404
    # The original and the pages rendered for it go together.
    assert list(services.storage.list(prefix)) == []
    assert client.get(f"/api/projects/{project['id']}/documents").json() == []


def storage_key(document_id) -> str:
    from app.db import SessionLocal
    from app.models import Document

    with SessionLocal() as session:
        return session.get(Document, document_id).storage_key


def test_health_says_only_that_the_app_is_up(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_status_reports_what_is_missing(client, services):
    health = client.get("/api/status").json()
    assert health["llm"] == {
        "available": True,
        "agent_model": "google:gemini-2.5-flash-lite",
        "index_model": "google:gemini-2.5-flash-lite",
        "error": None,
    }
    assert health["embeddings"]["ok"] is True

    services.embedder.fail = True
    services.llm.available = False
    health = client.get("/api/status").json()
    assert health["llm"]["available"] is False
    assert health["llm"]["error"] == MISSING_KEY
    assert health["embeddings"] == {
        "ok": False,
        "model": "nomic-embed-text",
        "error": "Embedding endpoint unreachable",
    }
