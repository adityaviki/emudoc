import time

from sqlalchemy import select, text

from app.config import get_settings
from app.db import reconcile_embeddings
from app.indexing.pipeline import process_document
from app.indexing.worker import Worker
from app.models import Passage


def stage_statuses(document):
    return {name: stage["status"] for name, stage in document.stages.items()}


def embedding_dim(session) -> int:
    return session.scalar(
        text(
            "SELECT atttypmod FROM pg_attribute "
            "WHERE attrelid = 'passages'::regclass AND attname = 'embedding'"
        )
    )


def test_changing_the_embedding_model_requeues_only_the_embed_stage(
    session, services, indexed, monkeypatch
):
    settings = get_settings()
    monkeypatch.setattr(settings, "embedding_model", services.embedder.model)
    assert reconcile_embeddings() == 0

    monkeypatch.setattr(settings, "embedding_model", "another-model")
    assert reconcile_embeddings() == 1
    session.refresh(indexed)
    assert indexed.status == "queued"
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
    assert reconcile_embeddings() == 0


def test_changing_the_embedding_size_rebuilds_the_column(session, services, indexed, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "embedding_model", services.embedder.model)
    try:
        monkeypatch.setattr(settings, "embedding_dim", 4)
        assert reconcile_embeddings() == 1
        assert embedding_dim(session) == 4
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
        # The schema is shared by the whole test run: put the column back. Altering the table
        # needs an exclusive lock, so this session's open transaction has to end first.
        session.rollback()
        monkeypatch.setattr(settings, "embedding_dim", 768)
        reconcile_embeddings()
    assert embedding_dim(session) == 768


def test_worker_indexes_queued_documents_and_recovers_interrupted_ones(
    session, services, make_document, sample_pdf, sample_docx
):
    queued = make_document(sample_pdf)
    # A document a previous process was working on when it stopped.
    interrupted = make_document(
        sample_docx, project=session.get(type(queued.project), queued.project_id)
    )
    interrupted.status = "processing"
    session.commit()

    worker = Worker(services, concurrency=2)
    worker.start()
    try:
        worker.notify()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            session.expire_all()
            session.refresh(queued)
            session.refresh(interrupted)
            if queued.status == "ready" and interrupted.status == "ready":
                break
            time.sleep(0.05)
    finally:
        worker.stop()

    assert queued.status == "ready" and interrupted.status == "ready"
    assert stage_statuses(interrupted)["embed"] == "done"


def test_unknown_api_paths_are_not_swallowed_by_the_frontend(client):
    assert client.get("/api/no-such-endpoint").status_code == 404
