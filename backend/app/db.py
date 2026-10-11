"""The database engine, the base every table derives from, and the schema check at start."""

import logging
from collections.abc import Iterator

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import MetaData, create_engine, text
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
# The advisory lock a migration run holds (migrations/env.py), so that two processes upgrading
# the same database take turns instead of both altering tables. Any fixed number works as long
# as nothing else in the database locks on it.
MIGRATION_LOCK = 20261011
UPGRADE_COMMAND = "uv run alembic upgrade head"


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


def check_schema() -> None:
    """Refuse to serve a database whose schema is not the one this code was written for.

    The migrations are applied by `alembic upgrade head`, run before the app starts (`dev.sh`
    does it; a deployment does it as a release step), not by the app itself: several replicas
    starting at once would race one another altering tables, and a migration that fails should
    stop a release rather than take the app down with it. So at start the app only compares the
    database's revision with the newest migration and says what to run when they differ.
    """
    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    with engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
    if current == head:
        return
    state = (
        "has no schema yet"
        if current is None
        else f"is at schema revision {current}, and this version of Emudoc needs {head}"
    )
    raise RuntimeError(
        f"The database {state}. Run `{UPGRADE_COMMAND}` in the backend directory, then start "
        "the app again."
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
