"""The database engine, the base every table derives from, and the schema's upkeep at start."""

import logging
from collections.abc import Iterator

from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import BACKEND_DIR, get_settings

log = logging.getLogger(__name__)

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)

# Constraints are named the way Postgres names them when left unnamed, so a migration can name
# the constraint it drops, and a database made before there were migrations needs no renaming.
# Check constraints are named by hand in models.py.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "%(table_name)s_%(column_0_N_name)s_key",
    "fk": "%(table_name)s_%(column_0_N_name)s_fkey",
    "pk": "%(table_name)s_pkey",
}

MIGRATIONS_DIR = BACKEND_DIR / "migrations"
# The migration whose tables a database made by `create_all`, before there were migrations,
# already has.
FIRST_REVISION = "bedb624406a6"


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


def alembic_config() -> Config:
    """Alembic set up for this backend, wherever the process was started from."""
    config = Config(BACKEND_DIR / "alembic.ini")
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return config


def migrate() -> None:
    """Apply the migrations the database has not seen yet.

    Before there were migrations the tables came from `create_all`, and every change to a table
    meant a reset, so a database that has all the tables but no version is at the first
    migration and is stamped as such. One that has only some of them is older than that and
    cannot be upgraded; the error says to reset it.
    """
    from app import models  # noqa: F401  (registers the tables on Base.metadata)

    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        tables = set(inspect(connection).get_table_names())
        if tables and "alembic_version" not in tables:
            missing = sorted(set(Base.metadata.tables) - tables)
            if missing:
                raise RuntimeError(
                    "The database was made by an older version of Emudoc and lacks the tables "
                    f"{', '.join(missing)}, so it cannot be upgraded. Reset it with "
                    "`docker compose down -v` and start the app again."
                )
            log.info("Recording a database made before migrations as revision %s", FIRST_REVISION)
            command.stamp(config, FIRST_REVISION)
        command.upgrade(config, "head")


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
