import logging
from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

log = logging.getLogger(__name__)

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


def init_db() -> None:
    from app import models  # noqa: F401  (registers the tables on Base.metadata)

    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        # create_all never alters a table that exists: databases made before folders arrived
        # get the column here.
        conn.execute(
            text(
                "ALTER TABLE documents ADD COLUMN IF NOT EXISTS folder_id INTEGER "
                "REFERENCES folders(id) ON DELETE CASCADE"
            )
        )
        conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_documents_folder_id ON documents (folder_id)")
        )


def reconcile_embeddings() -> int:
    """Queue passages for embedding again when the configured model has changed.

    Vectors from different models, or of different sizes, cannot be compared. So when
    EMBEDDING_MODEL or EMBEDDING_DIM no longer matches what is stored, the affected documents
    go back to the embed stage. Returns how many documents were queued.
    """
    settings = get_settings()
    with engine.begin() as conn:
        stored_dim = conn.scalar(
            text(
                "SELECT atttypmod FROM pg_attribute "
                "WHERE attrelid = 'passages'::regclass AND attname = 'embedding'"
            )
        )
        if stored_dim != settings.embedding_dim:
            # The column's size is part of its type, so it is rebuilt along with its index.
            conn.execute(text("DROP INDEX IF EXISTS ix_passages_embedding"))
            conn.execute(text("ALTER TABLE passages DROP COLUMN embedding"))
            conn.execute(
                text(f"ALTER TABLE passages ADD COLUMN embedding vector({settings.embedding_dim})")
            )
            conn.execute(
                text(
                    "CREATE INDEX ix_passages_embedding ON passages "
                    "USING hnsw (embedding vector_cosine_ops)"
                )
            )
            stale = "SELECT document_id FROM passages"
        else:
            stale = (
                "SELECT document_id FROM passages "
                "WHERE embedding IS NOT NULL AND embedding_model IS DISTINCT FROM :model"
            )
        queued = conn.execute(
            text(
                "UPDATE documents SET status = 'queued', "
                "stages = jsonb_set(stages, '{embed}', CAST(:pending AS jsonb)) "
                f"WHERE status <> 'failed' AND id IN ({stale})"
            ),
            {"model": settings.embedding_model, "pending": '{"status": "pending", "detail": ""}'},
        ).rowcount
    if queued:
        log.info(
            "Embedding settings changed: %s documents queued to be embedded with %s",
            queued,
            settings.embedding_model,
        )
    return queued
