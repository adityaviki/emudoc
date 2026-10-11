"""Upkeep of the stored embeddings: what the app checks at start, and the operator command that
rebuilds them (`uv run python -m app.maintenance rebuild-embeddings`).

The app never alters the schema at start. A changed EMBEDDING_MODEL only needs data queued
again, which is safe for any number of replicas to do at once, so the app does it. A changed
EMBEDDING_DIM needs the vector column dropped and remade, which discards every stored vector and
races between replicas, so the app refuses to start and names the command instead.
"""

import argparse
import logging
import sys

from sqlalchemy import Connection, text

from app.config import get_settings
from app.db import MIGRATION_LOCK, check_schema, engine

log = logging.getLogger(__name__)

REBUILD_COMMAND = "uv run python -m app.maintenance rebuild-embeddings"

# pgvector keeps a column's size as the type's modifier, so the catalog has it without a scan.
_STORED_DIM = text(
    "SELECT atttypmod FROM pg_attribute "
    "WHERE attrelid = 'passages'::regclass AND attname = 'embedding'"
)
_PENDING = '{"status": "pending", "detail": ""}'


def stored_embedding_dim(connection: Connection) -> int:
    """The size of the vectors `passages.embedding` holds."""
    return connection.scalar(_STORED_DIM)


def check_embeddings() -> None:
    """Refuse to serve when the vector column is not sized for EMBEDDING_DIM.

    Vectors of another size cannot be stored or compared, and resizing the column discards
    every vector in it, so that step is the operator's: the message says what to run.
    """
    settings = get_settings()
    with engine.connect() as connection:
        stored = stored_embedding_dim(connection)
    if stored == settings.embedding_dim:
        return
    raise RuntimeError(
        f"The database holds embeddings of size {stored}, but EMBEDDING_DIM is "
        f"{settings.embedding_dim}. Run `{REBUILD_COMMAND}` in the backend directory, which "
        "resizes the column and queues every document to be embedded again, then start the "
        "app again."
    )


def requeue_stale_embeddings() -> int:
    """Queue the documents whose passages were embedded with a model other than EMBEDDING_MODEL.

    Vectors from different models cannot be compared, so after a model change a document keeps
    its old vectors until it has been through the embed stage again. This only updates rows and
    finds nothing while the model is unchanged, so the app runs it at every start. Returns how
    many documents were queued.
    """
    settings = get_settings()
    with engine.begin() as connection:
        queued = _queue_embed_stage(
            connection,
            "SELECT document_id FROM passages "
            "WHERE embedding IS NOT NULL AND embedding_model IS DISTINCT FROM :model",
        )
    if queued:
        log.info(
            "Embedding model changed: %s documents queued to be embedded with %s",
            queued,
            settings.embedding_model,
        )
    return queued


def rebuild_embeddings() -> int:
    """Size `passages.embedding` for EMBEDDING_DIM and queue every document to be embedded again.

    The size is part of the column's type, so a different size means dropping the column and
    its index and creating them again, empty. When the size already matches, the column stays
    and only the documents are queued; their old vectors serve searches until the embed stage
    replaces them. Returns how many documents were queued.
    """
    settings = get_settings()
    check_schema()
    with engine.begin() as connection:
        # The same lock a migration run holds, so this never interleaves with `alembic upgrade
        # head`, and a second copy of the command waits and then finds the column already right.
        connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": MIGRATION_LOCK})
        stored = stored_embedding_dim(connection)
        if stored != settings.embedding_dim:
            connection.execute(text("DROP INDEX IF EXISTS ix_passages_embedding"))
            connection.execute(text("ALTER TABLE passages DROP COLUMN embedding"))
            connection.execute(
                text(f"ALTER TABLE passages ADD COLUMN embedding vector({settings.embedding_dim})")
            )
            connection.execute(
                text(
                    "CREATE INDEX ix_passages_embedding ON passages "
                    "USING hnsw (embedding vector_cosine_ops)"
                )
            )
            log.info(
                "Rebuilt passages.embedding for vectors of size %s; it held size %s",
                settings.embedding_dim,
                stored,
            )
        queued = _queue_embed_stage(connection, "SELECT document_id FROM passages")
    return queued


def _queue_embed_stage(connection: Connection, documents: str) -> int:
    """Send the documents the subquery names back to the embed stage; the other stages keep
    their output. Failed documents are left alone: they have nothing to embed."""
    return connection.execute(
        text(
            "UPDATE documents SET status = 'queued', "
            "stages = jsonb_set(stages, '{embed}', CAST(:pending AS jsonb)) "
            f"WHERE status <> 'failed' AND id IN ({documents})"
        ),
        {"model": get_settings().embedding_model, "pending": _PENDING},
    ).rowcount


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m app.maintenance",
        description="Commands that change the Emudoc database on purpose. Run them in the "
        "backend directory with `uv run`; they read the same .env as the app.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "rebuild-embeddings",
        help="size the vector column for EMBEDDING_DIM and embed every document again",
        description="Size the passages.embedding column for EMBEDDING_DIM (the app refuses to "
        "start while they differ) and queue every document to be embedded again with "
        "EMBEDDING_MODEL. Resizing discards the stored vectors, so run it with the app stopped.",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # The schema check sets up a migration context, which announces itself; nothing to act on.
    logging.getLogger("alembic.runtime.migration").setLevel(logging.WARNING)

    settings = get_settings()
    try:
        if args.command == "rebuild-embeddings":
            queued = rebuild_embeddings()
            print(
                f"Queued {queued} document{'' if queued == 1 else 's'} to be embedded again "
                f"with {settings.embedding_model} at size {settings.embedding_dim}. The app's "
                "worker embeds them while it runs; start the app if it is stopped."
            )
    except RuntimeError as error:
        sys.exit(str(error))


if __name__ == "__main__":
    main()
