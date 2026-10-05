"""Background indexing: threads that claim queued documents and run the pipeline."""

import logging
import threading

from sqlalchemy import text

from app.db import SessionLocal
from app.indexing.pipeline import Services, process_document

log = logging.getLogger(__name__)

POLL_SECONDS = 2.0

_CLAIM = text(
    """
    UPDATE documents SET status = 'processing', updated_at = now()
    WHERE id = (
        SELECT id FROM documents WHERE status = 'queued'
        ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1
    )
    RETURNING id
    """
)


class Worker:
    def __init__(self, services: Services, concurrency: int):
        self._services = services
        self._concurrency = concurrency
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        # Documents left mid-run by a previous process resume at their first unfinished stage.
        with SessionLocal() as session:
            session.execute(
                text("UPDATE documents SET status = 'queued' WHERE status = 'processing'")
            )
            session.commit()
        for index in range(self._concurrency):
            thread = threading.Thread(target=self._loop, name=f"indexer-{index}", daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        for thread in self._threads:
            thread.join(timeout=5)

    def notify(self) -> None:
        """Wake the workers after documents were queued."""
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            document_id = self._claim()
            if document_id is None:
                self._wake.wait(POLL_SECONDS)
                self._wake.clear()
                continue
            try:
                process_document(document_id, self._services)
            except Exception:
                log.exception("Indexing crashed for document %s", document_id)
                self._mark_failed(document_id)

    def _claim(self) -> int | None:
        with SessionLocal() as session:
            row = session.execute(_CLAIM).first()
            session.commit()
            return row[0] if row else None

    def _mark_failed(self, document_id: int) -> None:
        with SessionLocal() as session:
            session.execute(
                text("UPDATE documents SET status = 'failed', error = :error WHERE id = :id"),
                {
                    "id": document_id,
                    "error": "Indexing stopped unexpectedly. Try reprocessing.",
                },
            )
            session.commit()
