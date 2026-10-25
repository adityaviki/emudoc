from io import BytesIO
from pathlib import Path

from PIL import Image
from sqlalchemy import func, select

from app.config import get_settings
from app.indexing.pipeline import process_document
from app.main import KEYWORDS_ONLY, MODELS_UNAVAILABLE, WORK_STOPPED
from app.models import Document, Job
from app.parsing import SUPPORTED_EXTENSIONS, images
from tests.conftest import (
    MISSING_KEY,
    FakeEmbedder,
    compound_file,
    pageless_pdf,
    protected_pdf,
)


def create_project(client, name="Contracts"):
    response = client.post("/api/projects", json={"name": name, "description": "Supplier files"})
    assert response.status_code == 201
    return response.json()


def upload(client, project_id, *paths):
    files = [("files", (Path(path).name, Path(path).read_bytes())) for path in paths]
    response = client.post(f"/api/projects/{project_id}/documents", files=files)
    assert response.status_code == 201
    return response.json()


def listed(client, project_id, folder_id=None):
    """The first page of the documents directly in a folder, the root's by default."""
    response = client.get(
        f"/api/projects/{project_id}/documents",
        params={} if folder_id is None else {"folder_id": folder_id},
    )
    assert response.status_code == 200, response.text
    return response.json()["documents"]


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
    assert set(document["stages"]) == {"scan", "parse", "structure", "enrich", "embed"}
    assert {r["filename"]: r["reason"] for r in result["rejected"]} == {
        "archive.zip": (
            "Unsupported type. Use .csv, .docx, .heic, .heif, .htm, .html, .jpeg, .jpg, .md, .pdf, "
            ".png, .pptx, .rtf, .tif, .tiff, .txt, .webp, .xlsx."
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


def test_a_file_whose_bytes_are_not_its_type_is_rejected(client, sample_pdf, sample_xlsx, tmp_path):
    # The sentence is about the file, at the upload, not about a parser a stage later; and a
    # file refused for its bytes is not kept, counted or looked up as a duplicate. The PDF under
    # a Word name holds the bytes of the one that went in just before it.
    project = create_project(client)
    renamed = tmp_path / "renamed.docx"
    renamed.write_bytes(sample_pdf.read_bytes())
    fake = tmp_path / "fake.pdf"
    fake.write_text("Dear supplier, the delivery was late.\n", encoding="utf-8")
    photo = tmp_path / "photo.jpg"
    Image.new("RGB", (8, 8), "white").save(photo, format="PNG")
    notes = tmp_path / "notes.txt"
    notes.write_bytes(bytes(range(256)) * 4)
    swapped = tmp_path / "budget.docx"
    swapped.write_bytes(sample_xlsx.read_bytes())

    result = upload(client, project["id"], sample_pdf, renamed, fake, photo, notes, swapped)
    (document,) = result["documents"]
    assert document["filename"] == "msa.pdf"
    rename = "Upload the original, or rename it to its real type."
    assert {r["filename"]: r["reason"] for r in result["rejected"]} == {
        "renamed.docx": f"Not a Word document. {rename}",
        "fake.pdf": f"Not a PDF file. {rename}",
        "photo.jpg": f"Not a JPEG image. {rename}",
        "notes.txt": f"Not a text file. {rename}",
        "budget.docx": f"Not a Word document. {rename}",
    }
    assert client.get(f"/api/projects/{project['id']}").json()["document_count"] == 1


def test_a_locked_or_pageless_file_says_why_it_cannot_be_read(client, services, tmp_path):
    # An Office file saved with a password is no zip and is named at the upload. Telling a PDF
    # locked with a password from one that only restricts copying takes a PDF library, which
    # runs in the parse stage's child process, so such a PDF goes in and fails to parse with
    # the reason, as one without pages does.
    project = create_project(client)
    locked = protected_pdf(tmp_path / "locked.pdf", "secret")
    restricted = protected_pdf(tmp_path / "restricted.pdf", "")
    empty = pageless_pdf(tmp_path / "empty.pdf")
    encrypted = {"EncryptionInfo": 0, "EncryptedPackage": 8192}
    workbook = compound_file(tmp_path / "budget.xlsx", encrypted)
    letter = compound_file(tmp_path / "letter.docx", {"WordDocument": 4096, "1Table": 4096})

    result = upload(client, project["id"], locked, restricted, empty, workbook, letter)
    assert {r["filename"]: r["reason"] for r in result["rejected"]} == {
        "budget.xlsx": "Protected by a password. Save a copy without the password and upload that.",
        "letter.docx": "An older Word document (.doc). Save it as .docx and upload that.",
    }
    # Refused by the parser, not lost to a passing fault: the worker would not try again.
    failures = {
        document["filename"]: process_document(document["id"], services)
        for document in result["documents"]
    }
    assert {
        name: [(failure.stage, failure.transient) for failure in stage_failures]
        for name, stage_failures in failures.items()
    } == {
        "locked.pdf": [("parse", False)],
        "restricted.pdf": [],
        "empty.pdf": [("parse", False)],
    }

    documents = listed(client, project["id"])
    assert {d["filename"]: (d["status"], d["error"]) for d in documents} == {
        "locked.pdf": (
            "failed",
            "This PDF is protected by a password. Save a copy without the password and upload "
            "that.",
        ),
        "empty.pdf": (
            "failed",
            "This PDF has no pages. Export it again from the program that made it and upload that.",
        ),
        "restricted.pdf": ("ready", None),
    }
    # pdfium opens neither, so a thumbnail is a 404 rather than a server error.
    for document in documents:
        if document["status"] == "failed":
            assert document["stages"]["scan"]["status"] == "done"
            assert document["stages"]["parse"]["status"] == "failed"
            thumbnail = client.get(f"/api/documents/{document['id']}/thumbnail.png")
            assert thumbnail.status_code == 404


def test_an_upload_of_too_many_files_or_bytes_is_refused_whole(
    client, session, sample_pdf, tmp_path, monkeypatch
):
    project = create_project(client)
    notes = []
    for n in range(3):
        note = tmp_path / f"note{n}.txt"
        note.write_text(f"Note {n}\n" * 100_000)
        notes.append(note)

    monkeypatch.setattr(get_settings(), "max_upload_files", 2)
    files = [("files", (path.name, path.read_bytes())) for path in notes]
    refused = client.post(f"/api/projects/{project['id']}/documents", files=files)
    assert refused.status_code == 413
    assert refused.json() == {
        "detail": (
            "This upload has 3 files, and one upload can have 2 at most. "
            "Upload them in smaller batches."
        )
    }

    # Two files of 700 kB each are more than one request may carry, though each is well
    # within what one file may be.
    monkeypatch.setattr(get_settings(), "max_upload_request_mb", 1)
    files = [("files", (path.name, path.read_bytes())) for path in notes[:2]]
    refused = client.post(f"/api/projects/{project['id']}/documents", files=files)
    assert refused.status_code == 413
    assert refused.json() == {
        "detail": (
            "These files add up to 1.4 MB, and one upload can carry 1 MB at most. "
            "Upload them in smaller batches."
        )
    }
    # Nothing of a refused request was stored or queued.
    assert listed(client, project["id"]) == []
    assert session.scalar(select(func.count()).select_from(Job)) == 0

    # One of them goes in as before.
    result = upload(client, project["id"], notes[0])
    assert [document["filename"] for document in result["documents"]] == ["note0.txt"]


def test_a_simultaneous_upload_of_the_same_bytes_is_rejected_not_a_crash(
    client, session, make_document, monkeypatch, sample_pdf, sample_docx
):
    from app import uploads
    from app.models import Project

    project = create_project(client)
    choose = uploads.unique_filename

    def after_another_request_commits(session_, project_id, folder_id, name):
        # Between the duplicate check and the insert, another request stores the same bytes;
        # the second file of the request must still go in after the first is refused.
        if name == sample_pdf.name:
            make_document(sample_pdf, filename="scan.pdf", project=session.get(Project, project_id))
        return choose(session_, project_id, folder_id, name)

    monkeypatch.setattr(uploads, "unique_filename", after_another_request_commits)
    result = upload(client, project["id"], sample_pdf, sample_docx)
    assert [d["filename"] for d in result["documents"]] == ["policy.docx"]
    scan = session.scalar(select(Document.id).where(Document.filename == "scan.pdf"))
    assert result["rejected"] == [
        {
            "filename": "msa.pdf",
            "reason": "Already in this project as 'scan.pdf'.",
            "limit": None,
            "index": 0,
            "duplicate_of": scan,
        }
    ]
    names = [d["filename"] for d in listed(client, project["id"])]
    assert names == ["policy.docx", "scan.pdf"]


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
        "retry_at": None,
        "deferred": False,
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
    assert original.headers["content-disposition"] == 'attachment; filename="msa.pdf"'


def test_originals_download_and_only_images_open_in_the_browser(
    client, services, sample_docx, sample_csv, tmp_path
):
    # A PDF or a Word file that a browser opened in a tab could run scripts or submit forms; an
    # image runs nothing, and the document pane's preview needs it inline.
    picture = tmp_path / "photo.png"
    Image.new("RGB", (40, 20), "white").save(picture)
    notes = tmp_path / "Notizen März.md"
    notes.write_text("# Notizen\n\nLieferung verspätet.\n")
    project = create_project(client)
    documents = upload(client, project["id"], picture, sample_docx, sample_csv, notes)["documents"]
    for document in documents:
        process_document(document["id"], services)

    def disposition(document):
        return client.get(f"/api/documents/{document['id']}/file").headers["content-disposition"]

    assert {document["filename"]: disposition(document) for document in documents} == {
        "photo.png": 'inline; filename="photo.png"',
        sample_docx.name: f'attachment; filename="{sample_docx.name}"',
        sample_csv.name: f'attachment; filename="{sample_csv.name}"',
        "Notizen März.md": "attachment; filename*=utf-8''Notizen%20M%C3%A4rz.md",
    }


def test_thumbnails_for_images_only_besides_pdfs(client, services, sample_docx, tmp_path):
    picture = tmp_path / "photo.png"
    Image.new("RGB", (800, 400), "white").save(picture)
    project = create_project(client)
    photo, policy = upload(client, project["id"], picture, sample_docx)["documents"]
    for document in (photo, policy):
        process_document(document["id"], services)

    thumbnail = client.get(f"/api/documents/{photo['id']}/thumbnail.png")
    assert thumbnail.status_code == 200
    with Image.open(BytesIO(thumbnail.content)) as image:
        assert image.size == (320, 160)
    assert client.get(f"/api/documents/{policy['id']}/thumbnail.png").status_code == 404


def test_thumbnail_refuses_an_image_too_large_to_decode(client, services, tmp_path, monkeypatch):
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
        process_document(document["id"], services)
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
    # Without a stage, a project runs its partly indexed documents alone
    # (tests/test_partly_indexed.py).
    whole_project = client.post(
        f"/api/projects/{project['id']}/reprocess", params={"from_stage": "enrich"}
    ).json()
    assert whole_project == {"queued": 1}

    assert client.delete(f"/api/documents/{document_id}").status_code == 204
    assert client.get(f"/api/documents/{document_id}").status_code == 404
    assert listed(client, project["id"]) == []
    # In the trash its files wait for a restore; deleted for good there, the original and the
    # pages rendered for it go together.
    assert len(list(services.storage.list(prefix))) == 2
    assert client.delete(f"/api/trash/documents/{document_id}").status_code == 204
    assert list(services.storage.list(prefix)) == []


def storage_key(document_id) -> str:
    from app.db import SessionLocal
    from app.models import Document

    with SessionLocal() as session:
        return session.get(Document, document_id).storage_key


def test_health_says_only_that_the_app_is_up(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_status_reports_what_is_missing(client, services, monkeypatch):
    health = client.get("/api/status").json()
    assert health["llm"] == {
        "available": True,
        "agent_model": "google:gemini-2.5-flash-lite",
        "index_model": "google:gemini-2.5-flash-lite",
        "agent_fallback_model": None,
        "index_fallback_model": None,
        "error": None,
    }
    monkeypatch.setattr(get_settings(), "agent_fallback_model", "anthropic:claude-haiku-5-5")
    health = client.get("/api/status").json()
    assert health["llm"]["agent_fallback_model"] == "anthropic:claude-haiku-5-5"
    assert health["embeddings"]["ok"] is True
    # The currency the costs on usage are in (PRICE_CURRENCY).
    assert health["currency"] == "USD"
    # What one upload may carry, by which the page sends a folder of more in several, and the
    # file types read, by which the upload client leaves the others out.
    monkeypatch.setattr(get_settings(), "max_upload_files", 3)
    assert client.get("/api/status").json()["uploads"] == {
        "files": 3,
        "request_bytes": 500 * 1024 * 1024,
        "file_bytes": 100 * 1024 * 1024,
        "extensions": sorted(SUPPORTED_EXTENSIONS),
    }

    # In development whoever reads the status runs the app: what stops it, and what to do.
    services.embedder.fail = True
    services.llm.available = False
    health = client.get("/api/status").json()
    assert health["llm"]["available"] is False
    assert health["llm"]["error"] == (
        f"{MISSING_KEY} Then restart the backend to get answers, summaries and facts."
    )
    assert health["embeddings"] == {
        "ok": False,
        "model": "nomic-embed-text",
        "error": f"{FakeEmbedder.UNREACHABLE} Search falls back to keywords.",
    }


def test_a_deployments_status_names_nothing_of_how_it_is_set_up(client, services, monkeypatch):
    # Its members do not run it: they hear what they cannot do for now, and the operator reads
    # why in the log, the metrics and the alerts.
    monkeypatch.setattr(get_settings(), "environment", "production")
    monkeypatch.setattr(get_settings(), "agent_fallback_model", "anthropic:claude-haiku-5-5")
    assert client.get("/api/status").json() == {
        "llm": {
            "available": True,
            "agent_model": None,
            "index_model": None,
            "agent_fallback_model": None,
            "index_fallback_model": None,
            "error": None,
        },
        "embeddings": {"ok": True, "model": None, "error": None},
        # The suite runs no worker (tests/test_workers.py).
        "workers": {"running": False, "error": WORK_STOPPED},
        "currency": "USD",
        "uploads": {
            "files": 100,
            "request_bytes": 500 * 1024**2,
            "file_bytes": 100 * 1024**2,
            "extensions": sorted(SUPPORTED_EXTENSIONS),
        },
    }

    services.embedder.fail = True
    services.llm.available = False
    response = client.get("/api/status")
    health = response.json()
    assert health["llm"]["available"] is False
    assert health["llm"]["error"] == MODELS_UNAVAILABLE
    assert health["embeddings"] == {"ok": False, "model": None, "error": KEYWORDS_ONLY}
    for setup in ("GOOGLE_API_KEY", "gemini", "claude", FakeEmbedder.address, "nomic", "restart"):
        assert setup not in response.text
