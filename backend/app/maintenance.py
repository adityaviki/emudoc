"""Upkeep around the stored data: what the app checks at start, and the operator commands
(`uv run python -m app.maintenance <command>`).

The app never alters the schema at start. A changed EMBEDDING_MODEL only needs data queued
again, which is safe for any number of replicas to do at once, so the app does it. A changed
EMBEDDING_DIM needs the vector column dropped and remade, which discards every stored vector and
races between replicas, so the app refuses to start and names `rebuild-embeddings` instead.

`relocate-files` moves the files of a checkout from before storage keys (2026-10-11), which lay
under `files/{project}/{document}/` and `pages/{document}/` in the data directory, to where their
keys say, in whatever storage STORAGE_URL names. The migration that made the keys could not move
them: it runs in a transaction and the filesystem does not.

`housekeeping` is what the app does on its own at intervals (the worker's housekeeper thread):
today, removing the rendered pages and thumbnails older than PAGE_IMAGE_DAYS, which are rendered
again when asked for. `sweep-page-images` runs that sweep by hand, after lowering the setting.
"""

import argparse
import logging
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import Connection, select, text

from app.config import get_settings
from app.db import MIGRATION_LOCK, SessionLocal, check_schema, engine
from app.models import Document
from app.parsing import MIME_TYPES
from app.storage import (
    DELETE_BATCH,
    ROOT_PREFIX,
    LocalStorage,
    Storage,
    is_rendered,
    storage_from_settings,
)

log = logging.getLogger(__name__)

REBUILD_COMMAND = "uv run python -m app.maintenance rebuild-embeddings"
RELOCATE_COMMAND = "uv run python -m app.maintenance relocate-files"
# The advisory lock a housekeeping round holds, so that replicas take turns instead of all
# walking the storage at once; a different number from the migrations' lock.
HOUSEKEEPING_LOCK = MIGRATION_LOCK + 1
# The folders of the layout before storage keys, under the data directory.
OLD_LAYOUT = ("files", "pages")

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


def old_layout_dir(storage: Storage) -> Path | None:
    """The data directory, when it still holds files in the layout before storage keys."""
    if isinstance(storage, LocalStorage) and any(
        (storage.root / folder).is_dir() for folder in OLD_LAYOUT
    ):
        return storage.root
    return None


def warn_about_old_layout(storage: Storage) -> None:
    """At start: files left in the old layout are the originals the app now reports missing."""
    if old_layout_dir(storage) is not None:
        log.warning(
            "%s still holds files in the layout before storage keys, which the app no longer "
            "reads. Run `%s` in the backend directory to move them to where the documents "
            "expect them.",
            storage.root,
            RELOCATE_COMMAND,
        )


def relocate_files(storage: Storage, old_root: Path) -> tuple[int, int]:
    """Move every document's files from the old layout under `old_root` into the storage, under
    the document's key, and remove the folders that are empty afterwards.

    Returns how many documents had files to move and how many files were left behind. A
    document whose files are nowhere is left as it is, and so is a file that belongs to no
    document: it may be an orphan of a deleted document, or the command may have been run
    against the wrong database, and only the operator can tell which.
    """
    moved = 0
    with SessionLocal() as session:
        documents = session.scalars(select(Document).order_by(Document.id)).all()
    for document in documents:
        old_files = old_root / OLD_LAYOUT[0] / str(document.project_id) / str(document.id)
        old_pages = old_root / OLD_LAYOUT[1] / str(document.id)
        originals = sorted(old_files.glob("original.*")) if old_files.is_dir() else []
        pages = sorted(old_pages.glob("*.png")) if old_pages.is_dir() else []
        if not originals and not pages:
            continue
        for original in originals:
            storage.put(document.storage_key, original, MIME_TYPES.get(original.suffix.lower()))
        for image in pages:
            key = (
                document.thumbnail_key
                if image.stem == "thumbnail"
                else f"{document.storage_prefix}pages/{image.name}"
            )
            storage.put(key, image, "image/png")
        shutil.rmtree(old_files, ignore_errors=True)
        shutil.rmtree(old_pages, ignore_errors=True)
        moved += 1
    left = 0
    for folder in OLD_LAYOUT:
        _prune_empty(old_root / folder)
        if (old_root / folder).is_dir():
            left += sum(1 for path in (old_root / folder).rglob("*") if path.is_file())
    return moved, left


def sweep_rendered_images(
    storage: Storage, keep: timedelta, now: datetime | None = None
) -> tuple[int, int]:
    """Delete the rendered pages and thumbnails that were rendered more than `keep` ago.

    They are a cache of the originals, so nothing is lost: a page asked for again is rendered
    again. Returns how many were deleted and how many bytes they held. The whole storage is
    walked, which a listing does in pages, and the stale objects go in batches as they are
    found rather than all at the end, so a storage of any size takes bounded memory.
    """
    cutoff = (now or datetime.now(UTC)) - keep
    deleted = freed = 0
    batch: list[str] = []
    for entry in storage.list(ROOT_PREFIX):
        if not is_rendered(entry.key) or entry.modified_at >= cutoff:
            continue
        batch.append(entry.key)
        freed += entry.size
        if len(batch) == DELETE_BATCH:
            storage.delete(batch)
            deleted += len(batch)
            batch.clear()
    storage.delete(batch)
    return deleted + len(batch), freed


def housekeeping(storage: Storage) -> None:
    """One round of what the app does to the stored data on its own, at intervals.

    Every replica reaches its interval, so a round first takes an advisory lock and skips when
    another replica holds it: the work is idempotent, and walking the storage once is enough.
    The lock is transaction-level and the transaction reads nothing, so it ends with the block.
    """
    settings = get_settings()
    if not settings.page_image_days:
        return
    with engine.begin() as connection:
        taken = connection.scalar(
            text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": HOUSEKEEPING_LOCK}
        )
        if not taken:
            log.info("Housekeeping skipped: another process is on it")
            return
        deleted, freed = sweep_rendered_images(storage, timedelta(days=settings.page_image_days))
    if deleted:
        log.info("Housekeeping: %s", describe_sweep(deleted, freed, settings.page_image_days))


def describe_sweep(deleted: int, freed: int, days: int) -> str:
    """What a sweep did, for the log and the command."""
    what = "rendered page or thumbnail" if deleted == 1 else "rendered pages and thumbnails"
    return (
        f"Removed {deleted} {what} older than {days} days ({round(freed / 1_000_000, 1)} MB); "
        "they are rendered again when asked for"
    )


def _prune_empty(folder: Path) -> None:
    """Remove the folder and whatever folders under it hold no files."""
    if not folder.is_dir():
        return
    for child in folder.iterdir():
        if child.is_dir():
            _prune_empty(child)
    if not any(folder.iterdir()):
        folder.rmdir()


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
    relocate = commands.add_parser(
        "relocate-files",
        help="move the files of a checkout from before storage keys to where STORAGE_URL says",
        description="Move uploaded files and rendered pages from the layout before storage "
        "keys (files/ and pages/ in the data directory) into the storage STORAGE_URL names, "
        "under each document's key, and remove the old folders. Run it once after upgrading a "
        "checkout from before 2026-10-11, with the app stopped.",
    )
    relocate.add_argument(
        "--from",
        dest="old_root",
        type=Path,
        help="the data directory of the old layout; the directory STORAGE_URL names by default",
    )
    commands.add_parser(
        "sweep-page-images",
        help="remove the rendered pages and thumbnails older than PAGE_IMAGE_DAYS now",
        description="Remove the rendered pages and thumbnails that are older than "
        "PAGE_IMAGE_DAYS from the storage STORAGE_URL names. The running app does this on its "
        "own every few hours; this runs it at once, say after lowering the setting. Nothing "
        "is lost: a page asked for again is rendered again from the original.",
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
        elif args.command == "relocate-files":
            storage = storage_from_settings(settings)
            old_root = args.old_root
            if old_root is None:
                if not isinstance(storage, LocalStorage):
                    parser.error(
                        "STORAGE_URL names a bucket, so say where the old files are: "
                        "--from /path/to/the/data/directory"
                    )
                old_root = storage.root
            check_schema()
            storage.check()
            moved, left = relocate_files(storage, old_root)
            print(
                f"Moved the files of {moved} document{'' if moved == 1 else 's'} from "
                f"{old_root} into the storage."
            )
            if left:
                print(
                    f"{left} file{' belongs' if left == 1 else 's belong'} to no document in "
                    f"the database and {'was' if left == 1 else 'were'} left in {old_root}: "
                    f"{', '.join(OLD_LAYOUT)}. Delete them by hand once you are sure the app "
                    "is reading the right database."
                )
            print("Start the app again.")
        elif args.command == "sweep-page-images":
            if not settings.page_image_days:
                parser.error(
                    "PAGE_IMAGE_DAYS is 0, which keeps rendered pages for good; set it to the "
                    "number of days to keep them, then run this again."
                )
            storage = storage_from_settings(settings)
            storage.check()
            deleted, freed = sweep_rendered_images(
                storage, timedelta(days=settings.page_image_days)
            )
            print(describe_sweep(deleted, freed, settings.page_image_days) + ".")
    except RuntimeError as error:
        sys.exit(str(error))


if __name__ == "__main__":
    main()
