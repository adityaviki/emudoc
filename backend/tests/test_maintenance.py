"""The upkeep around the app: the embedding checks at start, the operator commands, and the
sweep of the storage (stale rendered pages, and what a failed delete left behind). The worker
and the job queue are in tests/test_jobs.py.

The size tests rebuild the column at 4 and put 768 back in a `finally`, because the whole run
shares the schema and `FakeEmbedder.dim` is 768.
"""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

import app.maintenance as maintenance
from app.config import get_settings
from app.db import MIGRATION_LOCK, engine
from app.indexing.pipeline import process_document
from app.jobs import BULK
from app.main import create_app
from app.maintenance import (
    HOUSEKEEPING_LOCK,
    REBUILD_COMMAND,
    RELOCATE_COMMAND,
    check_embeddings,
    housekeeping,
    main,
    old_layout_dir,
    rebuild_embeddings,
    relocate_files,
    remove_orphans,
    requeue_stale_embeddings,
    sweep_storage,
)
from app.models import Job, Passage, Project
from app.storage import LocalStorage, S3Storage, Storage, document_prefix, project_prefix
from tests.conftest import STORAGE_ROOT, FakeS3Client


def stage_statuses(document):
    return {name: stage["status"] for name, stage in document.stages.items()}


def embedding_dim(session) -> int:
    return session.scalar(
        text(
            "SELECT atttypmod FROM pg_attribute "
            "WHERE attrelid = 'passages'::regclass AND attname = 'embedding'"
        )
    )


@pytest.fixture
def embedder_settings(services, monkeypatch):
    """The settings named after the fake embedder, as a deployment's are after its model."""
    settings = get_settings()
    monkeypatch.setattr(settings, "embedding_model", services.embedder.model)
    monkeypatch.setattr(settings, "embedding_dim", services.embedder.dim)
    return settings


def test_changing_the_embedding_model_requeues_only_the_embed_stage(
    session, services, indexed, embedder_settings, monkeypatch
):
    assert requeue_stale_embeddings() == 0

    monkeypatch.setattr(embedder_settings, "embedding_model", "another-model")
    assert requeue_stale_embeddings() == 1
    session.refresh(indexed)
    assert indexed.status == "queued"
    # The queue gets the job the worker runs, behind any upload a user is waiting for.
    job = session.scalars(select(Job).order_by(Job.id.desc())).first()
    assert (job.payload, job.priority) == ({"document_id": indexed.id}, BULK)
    assert stage_statuses(indexed) == {
        "parse": "done",
        "structure": "done",
        "enrich": "done",
        "embed": "pending",
    }

    services.embedder.model = "another-model"
    process_document(indexed.id, services)
    session.expire_all()
    models = set(
        session.scalars(select(Passage.embedding_model).where(Passage.document_id == indexed.id))
    )
    assert models == {"another-model"}
    assert requeue_stale_embeddings() == 0


def test_app_refuses_a_database_whose_embeddings_have_another_size(
    session, services, indexed, embedder_settings, monkeypatch
):
    check_embeddings()

    # The setting changed, the column did not: nothing is altered, the message says what to run.
    monkeypatch.setattr(embedder_settings, "embedding_dim", 4)
    with pytest.raises(RuntimeError, match="holds embeddings of size 768, but EMBEDDING_DIM is 4"):
        check_embeddings()
    with pytest.raises(RuntimeError, match=REBUILD_COMMAND):
        with TestClient(create_app(services=services, start_worker=False)):
            pass
    assert embedding_dim(session) == 768
    session.refresh(indexed)
    assert indexed.status == "ready"


def test_the_command_rebuilds_the_column_at_the_new_size(
    session, services, indexed, embedder_settings, monkeypatch
):
    try:
        monkeypatch.setattr(embedder_settings, "embedding_dim", 4)
        assert rebuild_embeddings() == 1
        assert embedding_dim(session) == 4
        check_embeddings()
        session.refresh(indexed)
        assert indexed.status == "queued" and indexed.stages["embed"]["status"] == "pending"
        assert (
            session.scalar(select(Passage.embedding).where(Passage.document_id == indexed.id))
            is None
        )
        indexes = session.scalars(
            text("SELECT indexname FROM pg_indexes WHERE tablename = 'passages'")
        ).all()
        assert "ix_passages_embedding" in indexes
    finally:
        # Altering the table needs an exclusive lock, so this session's open transaction has to
        # end first.
        session.rollback()
        monkeypatch.setattr(embedder_settings, "embedding_dim", 768)
        rebuild_embeddings()
    assert embedding_dim(session) == 768


def test_the_command_keeps_the_vectors_while_the_size_matches(
    session, services, indexed, embedder_settings, capsys
):
    # Run for another reason (a changed prefix, say), it queues every document but leaves the
    # column and its vectors, which serve searches until the embed stage replaces them.
    main(["rebuild-embeddings"])
    assert "Queued 1 document to be embedded again with fake-embedder" in capsys.readouterr().out
    session.refresh(indexed)
    assert indexed.status == "queued" and indexed.stages["embed"]["status"] == "pending"
    assert (
        session.scalar(select(Passage.embedding).where(Passage.document_id == indexed.id))
        is not None
    )


def test_the_command_waits_for_a_migration_run(session, embedder_settings):
    # A rebuild holds the lock a migration run holds, so the two never alter the table at once.
    with engine.connect() as holder, ThreadPoolExecutor(1) as pool:
        holder.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": MIGRATION_LOCK})
        waiting = pool.submit(rebuild_embeddings)
        with pytest.raises(TimeoutError):
            waiting.result(timeout=0.5)
        holder.rollback()
        assert waiting.result(timeout=10) == 0


def lay_out_the_old_way(root, document, sample_pdf) -> None:
    """Put a document's files where a checkout from before storage keys kept them."""
    old_files = root / "files" / str(document.project_id) / str(document.id)
    old_files.mkdir(parents=True)
    (old_files / "original.pdf").write_bytes(sample_pdf.read_bytes())
    old_pages = root / "pages" / str(document.id)
    old_pages.mkdir(parents=True)
    (old_pages / "1.png").write_bytes(b"page one")
    (old_pages / "thumbnail.png").write_bytes(b"thumb")


def test_relocate_files_moves_the_old_layout_to_the_keys(
    session, services, indexed, sample_pdf, caplog, capsys
):
    # The migration gave the rows keys; the files are still where the old layout left them.
    services.storage.delete_prefix(indexed.storage_prefix)
    lay_out_the_old_way(STORAGE_ROOT, indexed, sample_pdf)
    assert old_layout_dir(services.storage) == STORAGE_ROOT
    with caplog.at_level("WARNING"):
        with TestClient(create_app(services=services, start_worker=False)):
            pass
    assert RELOCATE_COMMAND in caplog.text

    # A file of a document the database does not know stays where it is, and is reported.
    stray = STORAGE_ROOT / "files" / "9" / "99" / "original.pdf"
    stray.parent.mkdir(parents=True)
    stray.write_bytes(b"orphan")
    (STORAGE_ROOT / "pages" / "98").mkdir(parents=True)

    main(["relocate-files"])
    out = capsys.readouterr().out
    assert "Moved the files of 1 document from" in out
    assert "1 file belongs to no document in the database and was left in" in out
    assert stray.read_bytes() == b"orphan"
    assert not (STORAGE_ROOT / "pages").exists()
    stray.unlink()
    assert relocate_files(services.storage, STORAGE_ROOT) == (0, 0)
    assert services.storage.get(indexed.storage_key) == sample_pdf.read_bytes()
    assert services.storage.get(indexed.page_image_key(1)) == b"page one"
    assert services.storage.get(indexed.thumbnail_key) == b"thumb"
    assert not (STORAGE_ROOT / "files").exists() and not (STORAGE_ROOT / "pages").exists()
    assert old_layout_dir(services.storage) is None
    # Nothing left to move the second time, and nothing lost.
    assert relocate_files(services.storage, STORAGE_ROOT) == (0, 0)
    assert services.storage.exists(indexed.storage_key)


def test_relocate_files_can_fill_a_bucket_from_the_old_directory(
    session, indexed, sample_pdf, tmp_path
):
    old_root = tmp_path / "old-data"
    lay_out_the_old_way(old_root, indexed, sample_pdf)
    bucket = S3Storage(FakeS3Client("bucket"), "bucket")
    assert old_layout_dir(bucket) is None

    assert relocate_files(bucket, old_root) == (1, 0)
    assert bucket.get(indexed.storage_key) == sample_pdf.read_bytes()
    assert [entry.key for entry in bucket.list(indexed.storage_prefix)] == [
        indexed.storage_key,
        indexed.page_image_key(1),
        indexed.thumbnail_key,
    ]
    assert not (old_root / "files").exists()


def test_relocate_files_needs_the_old_directory_when_the_storage_is_a_bucket(monkeypatch, capsys):
    monkeypatch.setattr(get_settings(), "storage_url", "s3://bucket/emudoc")
    with pytest.raises(SystemExit) as stop:
        main(["relocate-files"])
    assert stop.value.code == 2
    assert "--from /path/to/the/data/directory" in capsys.readouterr().err


def age(storage: Storage, key: str, days: int) -> None:
    """Make an object look as if it was written `days` ago."""
    then = datetime.now(UTC) - timedelta(days=days)
    if isinstance(storage, LocalStorage):
        os.utime(storage.root / key, (then.timestamp(), then.timestamp()))
    else:
        storage.client.modified[key] = then


@pytest.fixture(params=["local", "s3"])
def any_storage(request, tmp_path) -> Storage:
    if request.param == "local":
        return LocalStorage(tmp_path / "data")
    return S3Storage(FakeS3Client("bucket"), "bucket")


def test_the_sweep_removes_old_rendered_pages_and_never_originals(any_storage, monkeypatch):
    storage = any_storage
    contract, invoice = document_prefix(1, 2, 3), document_prefix(1, 2, 4)
    kept = [f"{contract}original.pdf", f"{contract}pages/2.png", f"{invoice}original.pdf"]
    stale = [f"{contract}pages/1.png", f"{contract}thumbnail.png"]
    stale += [f"{invoice}pages/{n}.png" for n in range(1, 4)]
    for key in kept + stale:
        storage.put(key, b"x" * 10, "image/png")
    for key in stale + [f"{contract}original.pdf", f"{invoice}original.pdf"]:
        age(storage, key, days=31)
    age(storage, f"{contract}pages/2.png", days=29)

    # Five stale objects in batches of two: the last, short batch goes too.
    monkeypatch.setattr(maintenance, "DELETE_BATCH", 2)
    sweep = sweep_storage(storage, keep_images=timedelta(days=30))
    assert (sweep.images, sweep.image_bytes) == (5, 50)
    assert [entry.key for entry in storage.list("orgs/")] == kept
    if isinstance(storage, S3Storage):
        assert storage.client.calls.count("delete_objects") == 3
    # Nothing left to do the second time, and without a database nothing is an orphan.
    sweep = sweep_storage(storage, keep_images=timedelta(days=30))
    assert (sweep.images, sweep.image_bytes, sweep.orphans) == (0, 0, [])


def test_the_sweep_finds_what_a_failed_delete_left_behind(
    session, any_storage, make_document, sample_pdf, sample_docx, monkeypatch
):
    # The rows are what an upload makes; the objects go into the storage under test by hand.
    storage = any_storage
    kept = make_document(sample_pdf)
    project = session.get(Project, kept.project_id)
    gone = make_document(sample_docx, project=project)
    org = project.org_id
    settled = {
        kept.storage_key: 2,
        kept.page_image_key(1): 31,
        gone.storage_key: 2,
        gone.page_image_key(1): 2,
        gone.thumbnail_key: 2,
        # A document that never had a row, and a row's id under another organisation.
        f"{document_prefix(org, project.id, 999)}original.pdf": 2,
        f"{document_prefix(org + 1, project.id, kept.id)}original.pdf": 2,
        # A project whose delete failed after its rows were gone: its own objects and its
        # documents' are both found, each under its owner.
        f"{project_prefix(org, 77)}notes.txt": 2,
        f"{document_prefix(org, 77, 5)}original.pdf": 2,
        # A key outside the layout is nobody's.
        "orgs/readme.txt": 2,
    }
    for key, days in settled.items():
        storage.put(key, b"x" * 10)
        age(storage, key, days=days)
    # A fresh page of a live document, and an upload in flight: an object with no row yet.
    storage.put(kept.page_image_key(2), b"x" * 10)
    storage.put(f"{document_prefix(org, project.id, 998)}original.pdf", b"x" * 10)
    session.delete(gone)
    session.commit()

    # Eight owners checked two at a time: the batches are exercised, and a document's objects
    # are never split across two.
    monkeypatch.setattr(maintenance, "CHECK_BATCH", 2)
    with engine.begin() as connection:
        sweep = sweep_storage(storage, connection, keep_images=timedelta(days=30))
    assert (sweep.images, sweep.image_bytes, sweep.refusal) == (1, 10, None)
    assert not storage.exists(kept.page_image_key(1))
    assert sorted((orphan.prefix, orphan.objects, orphan.size) for orphan in sweep.orphans) == [
        (gone.storage_prefix, 3, 30),
        (document_prefix(org, project.id, 999), 1, 10),
        (project_prefix(org, 77), 1, 10),
        (document_prefix(org, 77, 5), 1, 10),
        (document_prefix(org + 1, project.id, kept.id), 1, 10),
    ]

    remove_orphans(storage, sweep.orphans)
    assert [entry.key for entry in storage.list("orgs/")] == [
        kept.storage_key,
        kept.page_image_key(2),
        f"{document_prefix(org, project.id, 998)}original.pdf",
        "orgs/readme.txt",
    ]
    if isinstance(storage, LocalStorage):
        # The folders of what was removed went with it; the live project's are still there.
        assert not (storage.root / project_prefix(org, 77)).exists()
        assert (storage.root / project_prefix(org, project.id)).is_dir()


def test_housekeeping_removes_orphans_and_refuses_an_empty_database(
    session, services, make_document, sample_pdf, sample_docx, monkeypatch, caplog
):
    storage = services.storage
    kept = make_document(sample_pdf)
    gone = make_document(sample_docx, project=session.get(Project, kept.project_id))
    storage.put(gone.page_image_key(1), b"page", "image/png")
    for key in (gone.storage_key, gone.page_image_key(1)):
        age(storage, key, days=1)
    session.delete(gone)
    session.commit()

    # PAGE_IMAGE_DAYS=0 keeps rendered pages for good; it says nothing about orphans.
    monkeypatch.setattr(get_settings(), "page_image_days", 0)
    with caplog.at_level("INFO", logger="app.maintenance"):
        housekeeping(storage)
    assert [entry.key for entry in storage.list("orgs/")] == [kept.storage_key]
    assert "Removed 2 objects (0.0 MB) of 1 document that no longer exists" in caplog.text

    # A database with no documents at all is more likely another deployment's than the owner
    # of a storage full of orphans: nothing goes, and the log says what to check.
    age(storage, kept.storage_key, days=1)
    session.delete(kept)
    session.commit()
    caplog.clear()
    with caplog.at_level("WARNING", logger="app.maintenance"):
        housekeeping(storage)
    assert storage.exists(kept.storage_key)
    assert (
        "The database holds no documents at all, but the storage holds 1 object (0.0 MB) of "
        "1 document it does not know. Nothing was removed: check that DATABASE_URL and "
        "STORAGE_URL belong to the same deployment"
    ) in caplog.text

    # With a document in the database the sweep is itself again.
    make_document(sample_pdf)
    housekeeping(storage)
    assert not storage.exists(kept.storage_key)


def test_the_orphan_command_lists_and_removes_on_request(
    session, services, make_document, sample_pdf, sample_docx, capsys
):
    storage = services.storage
    kept = make_document(sample_pdf)
    gone = make_document(sample_docx, project=session.get(Project, kept.project_id))
    age(storage, gone.storage_key, days=1)
    session.delete(gone)
    session.commit()

    main(["sweep-orphans"])
    out = capsys.readouterr().out
    assert "Found 1 object (0.0 MB) of 1 document that no longer exists:" in out
    assert f"  {gone.storage_prefix}  1 object, 0.0 MB" in out
    assert "Run again with --delete to remove them." in out
    assert storage.exists(gone.storage_key)

    main(["sweep-orphans", "--delete"])
    assert "Removed 1 object (0.0 MB) of 1 document that no longer exists." in (
        capsys.readouterr().out
    )
    assert not storage.exists(gone.storage_key)
    assert storage.exists(kept.storage_key)

    main(["sweep-orphans"])
    assert "Found nothing left behind" in capsys.readouterr().out

    # The command refuses an empty database like the app does, with or without --delete.
    age(storage, kept.storage_key, days=1)
    session.delete(kept)
    session.commit()
    with pytest.raises(SystemExit) as stop:
        main(["sweep-orphans", "--delete"])
    assert "The database holds no documents at all" in str(stop.value.code)
    assert storage.exists(kept.storage_key)


def test_housekeeping_sweeps_once_across_processes_and_not_at_all_when_off(
    session, services, indexed, monkeypatch, caplog
):
    page = indexed.page_image_key(1)
    services.storage.put(page, b"old page", "image/png")
    age(services.storage, page, days=31)

    # Another process holds the round's lock: this one skips rather than walk the storage too.
    with engine.connect() as holder:
        holder.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": HOUSEKEEPING_LOCK})
        housekeeping(services.storage)
        assert services.storage.exists(page)
        holder.rollback()

    with caplog.at_level("INFO", logger="app.maintenance"):
        housekeeping(services.storage)
    assert not services.storage.exists(page)
    assert services.storage.exists(indexed.storage_key)
    assert "Removed 1 rendered page or thumbnail older than 30 days (0.0 MB)" in caplog.text

    # PAGE_IMAGE_DAYS=0 keeps rendered pages for good.
    services.storage.put(page, b"old page", "image/png")
    age(services.storage, page, days=400)
    monkeypatch.setattr(get_settings(), "page_image_days", 0)
    housekeeping(services.storage)
    assert services.storage.exists(page)


def test_the_sweep_command_runs_at_once(session, services, indexed, monkeypatch, capsys):
    page = indexed.page_image_key(1)
    services.storage.put(page, b"old page", "image/png")
    age(services.storage, page, days=31)
    main(["sweep-page-images"])
    assert "Removed 1 rendered page or thumbnail older than 30 days" in capsys.readouterr().out
    assert not services.storage.exists(page)

    monkeypatch.setattr(get_settings(), "page_image_days", 0)
    with pytest.raises(SystemExit) as stop:
        main(["sweep-page-images"])
    assert stop.value.code == 2
    assert "PAGE_IMAGE_DAYS is 0" in capsys.readouterr().err


def test_unknown_api_paths_are_not_swallowed_by_the_frontend(client):
    assert client.get("/api/no-such-endpoint").status_code == 404
