"""Where files are kept: the storage interface over a local directory and over a bucket.

The two implementations are held to the same contract; the bucket one runs against a fake of
the few boto3 calls it makes, since the suite makes no network calls.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError, ReadTimeoutError
from fastapi.testclient import TestClient
from starlette.responses import FileResponse, RedirectResponse

from app.config import Settings, get_settings
from app.indexing.pipeline import Services, process_document, reset_stages
from app.main import create_app
from app.storage import (
    SANDBOX,
    URL_LIFETIME,
    LocalStorage,
    S3Storage,
    StorageError,
    content_disposition,
    document_prefix,
    is_rendered,
    original_key,
    project_prefix,
    storage_from_settings,
)
from tests.conftest import APP_HEADERS, FakeEmbedder, FakeLLM, FakeS3Client
from tests.test_api import create_project, upload
from tests.test_folders import make_folder, upload_into

DOCUMENT = document_prefix(1, 2, 3)


@pytest.fixture(params=["local", "s3"])
def storage(request, tmp_path):
    if request.param == "local":
        return LocalStorage(tmp_path / "data")
    return S3Storage(FakeS3Client("bucket"), "bucket", "emudoc")


def keys(storage, prefix: str) -> list[str]:
    return [entry.key for entry in storage.list(prefix)]


def test_keys_follow_the_layout():
    assert project_prefix(1, 2) == "orgs/1/projects/2/"
    assert DOCUMENT == "orgs/1/projects/2/documents/3/"
    assert original_key(1, 2, 3, ".pdf") == "orgs/1/projects/2/documents/3/original.pdf"


def test_rendered_pages_and_thumbnails_are_told_from_originals():
    assert is_rendered(f"{DOCUMENT}pages/1.png")
    assert is_rendered(f"{DOCUMENT}pages/120.png")
    assert is_rendered(f"{DOCUMENT}thumbnail.png")
    assert not is_rendered(f"{DOCUMENT}original.pdf")
    assert not is_rendered(f"{DOCUMENT}original.png")
    assert not is_rendered(f"{DOCUMENT}pages/1.png.part")
    assert not is_rendered(f"other/{DOCUMENT}pages/1.png")


def test_objects_are_stored_read_and_found(storage, tmp_path):
    storage.check()
    source = tmp_path / "upload.pdf"
    source.write_bytes(b"%PDF-1.4 original")
    storage.put(f"{DOCUMENT}original.pdf", source, "application/pdf")
    storage.put(f"{DOCUMENT}pages/1.png", b"png one", "image/png")

    assert storage.get(f"{DOCUMENT}original.pdf") == b"%PDF-1.4 original"
    assert storage.exists(f"{DOCUMENT}pages/1.png")
    assert not storage.exists(f"{DOCUMENT}pages/2.png")
    with pytest.raises(FileNotFoundError):
        storage.get(f"{DOCUMENT}pages/2.png")
    # The source of a put is left where it was.
    assert source.is_file()

    storage.put(f"{DOCUMENT}pages/1.png", b"png one again", "image/png")
    assert storage.get(f"{DOCUMENT}pages/1.png") == b"png one again"


def test_localize_gives_a_file_the_parsers_can_open(storage):
    storage.put(f"{DOCUMENT}original.pdf", b"%PDF-1.4 original", "application/pdf")
    with storage.localize(f"{DOCUMENT}original.pdf") as path:
        assert path.suffix == ".pdf"
        assert path.read_bytes() == b"%PDF-1.4 original"
        kept = path
    # A copy made for the block is gone afterwards; the local storage's own file stays.
    assert kept.exists() is isinstance(storage, LocalStorage)

    with pytest.raises(FileNotFoundError):
        with storage.localize(f"{DOCUMENT}missing.pdf"):
            pass


def test_a_prefix_lists_and_deletes_exactly_what_is_under_it(storage):
    neighbour = document_prefix(1, 2, 30)
    other_project = document_prefix(1, 9, 4)
    for key in (
        f"{DOCUMENT}original.pdf",
        f"{DOCUMENT}pages/1.png",
        f"{DOCUMENT}pages/2.png",
        f"{DOCUMENT}thumbnail.png",
        f"{neighbour}original.pdf",
        f"{other_project}original.pdf",
    ):
        storage.put(key, key.encode())

    # Five objects in the document and its neighbour: more than one page of a bucket listing.
    assert keys(storage, DOCUMENT) == [
        f"{DOCUMENT}original.pdf",
        f"{DOCUMENT}pages/1.png",
        f"{DOCUMENT}pages/2.png",
        f"{DOCUMENT}thumbnail.png",
    ]
    assert len(keys(storage, project_prefix(1, 2))) == 5
    # A listing says how big each object is and when it was written, for the sweeps.
    (first, *_) = storage.list(DOCUMENT)
    assert first.size == len(f"{DOCUMENT}original.pdf")
    assert first.modified_at.tzinfo is not None
    assert datetime.now(UTC) - first.modified_at < timedelta(minutes=1)

    # Single objects go without taking their neighbours; one already gone is nothing to report.
    storage.delete([f"{DOCUMENT}pages/1.png", f"{DOCUMENT}pages/2.png", f"{DOCUMENT}pages/9.png"])
    assert keys(storage, DOCUMENT) == [f"{DOCUMENT}original.pdf", f"{DOCUMENT}thumbnail.png"]
    storage.delete([])
    if isinstance(storage, LocalStorage):
        # An emptied folder goes with its last key; one that still holds keys stays.
        assert not (storage.root / DOCUMENT / "pages").exists()
        assert (storage.root / DOCUMENT).is_dir()

    # `documents/3/` must not take `documents/30/` with it.
    storage.delete_prefix(DOCUMENT)
    assert keys(storage, DOCUMENT) == []
    assert storage.exists(f"{neighbour}original.pdf")
    assert storage.exists(f"{other_project}original.pdf")

    storage.delete_prefix(project_prefix(1, 2))
    assert not storage.exists(f"{neighbour}original.pdf")
    assert storage.exists(f"{other_project}original.pdf")
    if isinstance(storage, LocalStorage):
        # The folders left empty above the prefix go with it, up to one still in use.
        assert not (storage.root / "orgs/1/projects/2").exists()
        assert (storage.root / "orgs/1/projects").is_dir()
    # Deleting what is already gone is nothing to report.
    storage.delete_prefix(project_prefix(1, 2))
    assert keys(storage, "orgs/") == [f"{other_project}original.pdf"]

    with pytest.raises(ValueError, match="ends with '/'"):
        storage.delete_prefix("orgs/1/projects/2/documents/3")
    with pytest.raises(ValueError, match="ends with '/'"):
        list(storage.list("orgs/1"))


def test_the_local_storage_keeps_keys_under_its_root(tmp_path):
    storage = LocalStorage(tmp_path / "data")
    for key in ("../outside", "/etc/passwd", "orgs/../../x", ""):
        with pytest.raises(ValueError, match="is not a storage key"):
            storage.put(key, b"x")
    # A local put is written beside its target and renamed into place, so a reader never sees
    # half an object; nothing of that is left behind.
    storage.put(f"{DOCUMENT}original.pdf", b"x")
    assert [p.name for p in (tmp_path / "data" / DOCUMENT).iterdir()] == ["original.pdf"]


def test_the_local_storage_sends_the_file_itself(tmp_path):
    storage = LocalStorage(tmp_path / "data")
    storage.put(f"{DOCUMENT}original.pdf", b"%PDF-1.4", "application/pdf")
    assert storage.presigned_url(f"{DOCUMENT}original.pdf", content_type="application/pdf") is None

    response = storage.response(
        f"{DOCUMENT}original.pdf", media_type="application/pdf", filename="contract.pdf"
    )
    assert isinstance(response, FileResponse)
    assert Path(response.path) == tmp_path / "data" / DOCUMENT / "original.pdf"
    assert response.headers["content-disposition"] == 'attachment; filename="contract.pdf"'
    assert "cache-control" not in response.headers
    # Sent from the app's own origin, the file gets a policy that lets a browser run nothing in
    # it, whether it downloads or is shown.
    assert response.headers["content-security-policy"] == SANDBOX

    shown = storage.response(
        f"{DOCUMENT}original.pdf", media_type="image/png", filename="scan.png", inline=True
    )
    assert shown.headers["content-disposition"] == 'inline; filename="scan.png"'
    assert shown.headers["content-security-policy"] == SANDBOX

    cached = storage.response(f"{DOCUMENT}original.pdf", media_type="image/png", max_age=86400)
    assert cached.headers["cache-control"] == "private, max-age=86400"
    with pytest.raises(FileNotFoundError):
        storage.response(f"{DOCUMENT}missing.png", media_type="image/png")


def test_the_bucket_redirects_to_a_signed_url():
    client = FakeS3Client("bucket")
    storage = S3Storage(client, "bucket", "/emudoc/")
    assert storage.prefix == "emudoc/"
    storage.put(f"{DOCUMENT}original.pdf", b"%PDF-1.4", "application/pdf")
    assert client.objects[f"emudoc/{DOCUMENT}original.pdf"] == (b"%PDF-1.4", "application/pdf")

    response = storage.response(
        f"{DOCUMENT}original.pdf", media_type="application/pdf", filename="Vertrag ü.pdf"
    )
    assert isinstance(response, RedirectResponse) and response.status_code == 307
    location = response.headers["location"]
    assert location.startswith(f"https://bucket.s3.example/emudoc/{DOCUMENT}original.pdf?")
    assert "response-content-type=application%2Fpdf" in location
    assert f"X-Amz-Expires={URL_LIFETIME}" in location
    # The name is fixed when the URL is signed, in the form browsers read for any alphabet, and
    # so is the download: the bucket's answer is the file, and the app adds no header to it.
    assert signed_disposition(location) == "attachment; filename*=utf-8''Vertrag%20%C3%BC.pdf"
    assert "cache-control" not in response.headers
    shown = storage.response(
        f"{DOCUMENT}original.png", media_type="image/png", filename="scan.png", inline=True
    )
    assert signed_disposition(shown.headers["location"]) == 'inline; filename="scan.png"'

    # A cached redirect must expire before the URL it leads to does.
    cached = storage.response(f"{DOCUMENT}pages/1.png", media_type="image/png", max_age=86400)
    assert cached.headers["cache-control"] == f"private, max-age={URL_LIFETIME - 60}"


def signed_disposition(url: str) -> str:
    """The Content-Disposition a signed URL makes the bucket answer with."""
    (disposition,) = parse_qs(urlsplit(url).query)["response-content-disposition"]
    return disposition


def test_content_disposition_names_the_file_as_browsers_read_it():
    assert content_disposition("invoice.pdf") == 'attachment; filename="invoice.pdf"'
    assert content_disposition("Rechnung März.pdf") == (
        "attachment; filename*=utf-8''Rechnung%20M%C3%A4rz.pdf"
    )
    assert content_disposition("scan.png", inline=True) == 'inline; filename="scan.png"'


def test_check_says_what_to_fix(tmp_path):
    local = LocalStorage(tmp_path / "new" / "data")
    local.check()
    assert (tmp_path / "new" / "data").is_dir()

    blocked = tmp_path / "file"
    blocked.write_text("not a directory")
    with pytest.raises(StorageError, match="cannot be created") as error:
        LocalStorage(blocked / "data").check()
    assert "STORAGE_URL" in str(error.value)

    with pytest.raises(StorageError, match="cannot be used") as error:
        S3Storage(FakeS3Client("other"), "bucket").check()
    assert "NoSuchBucket" in str(error.value) and "AWS_ENDPOINT_URL" in str(error.value)


def test_bucket_failures_are_reported_in_words():
    storage = S3Storage(FakeS3Client("other"), "bucket")
    with pytest.raises(StorageError, match="could not be stored"):
        storage.put("k", b"x")
    with pytest.raises(StorageError, match="could not be read"):
        storage.get("k")
    with pytest.raises(StorageError, match="could not be asked"):
        storage.exists("k")
    with pytest.raises(StorageError, match="could not be listed"):
        storage.delete_prefix("orgs/")
    with pytest.raises(StorageError, match="could not be deleted"):
        storage.delete(["k"])
    # A delete after the rows are gone is logged, not raised.
    storage.discard("orgs/1/")


def bucket_error(code: str, status: int) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": "as the bucket put it"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        "GetObject",
    )


def test_bucket_outages_may_pass_and_refusals_will_not(monkeypatch):
    # A missing bucket (the test above) is a refusal; so is a denied key. Throttling, an
    # overloaded service and a connection that could not be made or dropped are passing
    # conditions, which the pipeline tells apart to run the stage again later.
    client = FakeS3Client()
    storage = S3Storage(client, "bucket")
    for outage in (
        bucket_error("SlowDown", 503),
        bucket_error("RequestTimeout", 400),
        bucket_error("InternalError", 500),
        EndpointConnectionError(endpoint_url="http://bucket"),
    ):
        client.outage = outage
        with pytest.raises(StorageError, match="could not be asked") as error:
            storage.exists("k")
        assert error.value.transient, outage
        with pytest.raises(StorageError, match="could not be read") as error:
            storage.get("k")
        assert error.value.transient, outage
    client.outage = bucket_error("AccessDenied", 403)
    with pytest.raises(StorageError, match="could not be read") as error:
        storage.get("k")
    assert not error.value.transient
    client.outage = None
    with pytest.raises(StorageError, match="could not be stored") as error:
        S3Storage(client, "other").put("k", b"x")
    assert not error.value.transient

    # The connection dropped partway through the object the parse stage was copying here.
    class DroppedBody:
        def read(self, size: int = -1) -> bytes:
            raise ReadTimeoutError(endpoint_url="http://bucket")

    monkeypatch.setattr(client, "get_object", lambda **kw: {"Body": DroppedBody()})
    with pytest.raises(StorageError, match="could not be read") as error, storage.localize("k"):
        pass
    assert error.value.transient


def test_storage_from_settings_reads_the_url(tmp_path):
    settings = get_settings()
    local = storage_from_settings(
        Settings(
            _env_file=None,
            database_url=settings.database_url,
            storage_url="file:///var/lib/emu%20doc",
        )
    )
    assert isinstance(local, LocalStorage) and local.root == Path("/var/lib/emu doc")

    bucket = storage_from_settings(
        Settings(
            _env_file=None, database_url=settings.database_url, storage_url="s3://files/emudoc/prod"
        )
    )
    assert isinstance(bucket, S3Storage)
    assert (bucket.bucket, bucket.prefix) == ("files", "emudoc/prod/")
    assert bucket.client.meta.config.signature_version == "s3v4"

    plain = storage_from_settings(
        Settings(_env_file=None, database_url=settings.database_url, storage_url="s3://files")
    )
    assert (plain.bucket, plain.prefix) == ("files", "")


@pytest.fixture
def bucket_client(session, mailer):
    """The API over a bucket: the same app, with the storage behind it swapped."""
    s3 = FakeS3Client("bucket")
    services = Services(FakeLLM(), FakeEmbedder(), S3Storage(s3, "bucket", "emudoc"))
    app = create_app(services=services, mailer=mailer)
    with TestClient(app, headers=APP_HEADERS, follow_redirects=False) as client:
        client.s3 = s3
        client.services = services
        yield client


def test_the_app_works_over_a_bucket(bucket_client, sample_pdf, sample_docx, sample_xlsx):
    client = bucket_client
    assert client.s3.calls[0] == "head_bucket"
    project = create_project(client)
    document = upload(client, project["id"], sample_pdf)["documents"][0]
    key = f"emudoc/orgs/1/projects/{project['id']}/documents/{document['id']}/original.pdf"
    assert client.s3.objects[key] == (sample_pdf.read_bytes(), "application/pdf")

    # The original is fetched to this machine for the parse stage, and the copy is removed.
    process_document(document["id"], client.services)
    assert client.get(f"/api/documents/{document['id']}").json()["status"] == "ready"

    original = client.get(f"/api/documents/{document['id']}/file")
    assert original.status_code == 307
    assert original.headers["location"].startswith(f"https://bucket.s3.example/{key}?")
    assert "filename%3D%22msa.pdf%22" in original.headers["location"]

    page = client.get(f"/api/documents/{document['id']}/pages/2.png")
    assert page.status_code == 307
    rendered = key.replace("original.pdf", "pages/2.png")
    assert rendered in page.headers["location"]
    assert client.s3.objects[rendered][0].startswith(b"\x89PNG")
    assert client.s3.objects[rendered][1] == "image/png"
    # Rendered once: the second request finds the page in the bucket.
    puts = client.s3.calls.count("put_object")
    assert client.get(f"/api/documents/{document['id']}/pages/2.png").status_code == 307
    assert client.s3.calls.count("put_object") == puts
    assert client.get(f"/api/documents/{document['id']}/thumbnail.png").status_code == 307

    # Each kind of delete takes every object of what it deleted, and nothing else.
    folder = make_folder(client, project["id"], "Policies")
    (policy,) = upload_into(client, project["id"], folder["id"], sample_docx)["documents"]
    (table,) = upload(client, project["id"], sample_xlsx)["documents"]
    of = lambda doc: {k for k in client.s3.objects if f"/documents/{doc['id']}/" in k}  # noqa: E731
    assert of(document) and of(policy) and of(table)
    assert client.delete(f"/api/documents/{table['id']}").status_code == 204
    assert not of(table) and of(policy) and of(document)
    assert client.delete(f"/api/folders/{folder['id']}").status_code == 204
    assert not of(policy) and of(document)
    assert client.delete(f"/api/projects/{project['id']}").status_code == 204
    assert client.s3.objects == {}


def test_a_bucket_that_fails_mid_upload_leaves_no_row(bucket_client, sample_pdf):
    client = bucket_client
    project = create_project(client)
    client.s3.buckets.clear()
    response = client.post(
        f"/api/projects/{project['id']}/documents",
        files=[("files", ("msa.pdf", sample_pdf.read_bytes()))],
    )
    assert response.status_code == 503
    assert response.json()["detail"].startswith("The file could not be stored in the bucket")
    client.s3.buckets.add("bucket")
    assert client.get(f"/api/projects/{project['id']}/documents").json() == []


def test_the_app_refuses_to_start_without_its_bucket(session, mailer):
    services = Services(FakeLLM(), FakeEmbedder(), S3Storage(FakeS3Client("other"), "bucket"))
    with pytest.raises(StorageError, match="The bucket 'bucket' cannot be used"):
        with TestClient(create_app(services=services, mailer=mailer)):
            pass


def test_a_missing_original_fails_the_parse_stage_in_words(session, services, indexed):
    services.storage.delete_prefix(indexed.storage_prefix)
    reset_stages(indexed)
    session.commit()
    process_document(indexed.id, services)
    session.refresh(indexed)
    assert indexed.status == "failed"
    assert indexed.error == (
        "The uploaded file is missing from the storage. Delete this document and upload it again."
    )
