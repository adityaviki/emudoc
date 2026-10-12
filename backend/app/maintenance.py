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
one walk over the storage (`sweep_storage`) that removes the rendered pages and thumbnails older
than PAGE_IMAGE_DAYS, which are rendered again when asked for, and the objects of documents and
projects that no longer exist, which a delete leaves behind when the storage fails after the rows
are gone; and the pruning of jobs that finished longer ago than the queue keeps them.
`sweep-page-images` runs the first part by hand, after lowering the setting; `sweep-orphans`
lists the second part and removes it with `--delete`.

`failed-jobs` lists the jobs the worker gave up on, the dead letters (app/deadletters.py), and
with `--requeue` queues their documents again from the stage they stopped at.

`usage` prints what an organisation used per day (app/usage.py): the model calls and tokens
of its answers today, and whatever is metered next.
"""

import argparse
import logging
import re
import shutil
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import Connection, select, text
from sqlalchemy.orm import Session

from app.answers import EVENT_HISTORY, prune_events
from app.config import get_settings
from app.db import MIGRATION_LOCK, SessionLocal, check_schema, engine
from app.deadletters import describe, failed_jobs, requeue
from app.entitlements import PLANS, Entitlements
from app.jobs import BULK, HISTORY, prune_finished, queue_index_by_id
from app.models import PLANS as PLAN_NAMES
from app.models import Document, Organization
from app.parsing import MIME_TYPES
from app.storage import (
    DELETE_BATCH,
    ROOT_PREFIX,
    LocalStorage,
    Storage,
    StorageError,
    is_rendered,
    project_prefix,
    storage_from_settings,
)
from app.usage import DailyUsage, daily

log = logging.getLogger(__name__)

REBUILD_COMMAND = "uv run python -m app.maintenance rebuild-embeddings"
RELOCATE_COMMAND = "uv run python -m app.maintenance relocate-files"
ORPHANS_COMMAND = "uv run python -m app.maintenance sweep-orphans"
# The advisory lock a housekeeping round holds, so that replicas take turns instead of all
# walking the storage at once; a different number from the migrations' lock.
HOUSEKEEPING_LOCK = MIGRATION_LOCK + 1
# The folders of the layout before storage keys, under the data directory.
OLD_LAYOUT = ("files", "pages")
# An upload stores its file before its row is committed, so an object with no row that is this
# young is an upload in flight, not something left behind.
UPLOAD_GRACE = timedelta(hours=1)
# How many documents or projects one query checks for their rows during a sweep.
CHECK_BATCH = 500
# What a key belongs to: the document's prefix when it has one, else the project's. The match
# is the owner's prefix, as `document_prefix` and `project_prefix` in app/storage.py build it.
_OWNER = re.compile(r"orgs/\d+/projects/\d+/(?:documents/\d+/)?")

# pgvector keeps a column's size as the type's modifier, so the catalog has it without a scan.
_STORED_DIM = text(
    "SELECT atttypmod FROM pg_attribute "
    "WHERE attrelid = 'passages'::regclass AND attname = 'embedding'"
)
_PENDING = '{"status": "pending", "detail": ""}'
_DOCUMENT_KEYS = text("SELECT storage_key FROM documents WHERE id = ANY(:ids)")
_PROJECT_IDS = text("SELECT org_id, id FROM projects WHERE id = ANY(:ids)")
_ANY_DOCUMENTS = text("SELECT EXISTS (SELECT 1 FROM documents)")


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


@dataclass(frozen=True)
class Orphan:
    """A document's or a project's prefix in the storage that has no row behind it any more."""

    prefix: str
    objects: int
    size: int


@dataclass
class Sweep:
    """What one walk of the storage did and found."""

    # The rendered pages and thumbnails past their age, removed during the walk.
    images: int = 0
    image_bytes: int = 0
    # The prefixes with no row, in key order, for the caller to remove with `delete_prefix`;
    # a list of prefixes, not of objects, so a storage of any size takes bounded memory.
    orphans: list[Orphan] = field(default_factory=list)
    # Why the orphans must not be removed, when the database looks like the wrong one.
    refusal: str | None = None


@dataclass
class _Owned:
    """What a walk has seen of one prefix so far."""

    objects: int = 0
    size: int = 0
    # Written within UPLOAD_GRACE: an upload in flight, whose row is about to be committed.
    recent: bool = False


def sweep_storage(
    storage: Storage,
    connection: Connection | None = None,
    keep_images: timedelta | None = None,
) -> Sweep:
    """Walk the whole storage once, for both of the things the app tidies.

    The rendered pages and thumbnails older than `keep_images` are deleted as they are found,
    in batches, so nothing waits for the end of the walk; they are a cache of the originals and
    a page asked for again is rendered again. Every other object is matched to the document or
    project its key names, and the prefixes whose row is gone from the database `connection`
    reaches are returned as orphans: what a delete left behind when the storage failed after
    the rows were gone. Each part is skipped when its argument is None.

    Rows are checked in batches of CHECK_BATCH prefixes, never one query per object. A key
    outside the layout is nobody's and is left alone, and so is any prefix with an object
    younger than UPLOAD_GRACE, which is an upload whose row is not committed yet.
    """
    now = datetime.now(UTC)
    image_cutoff = now - keep_images if keep_images else None
    settled = now - UPLOAD_GRACE
    sweep = Sweep()
    stale: list[str] = []
    pending: dict[str, _Owned] = {}

    for entry in storage.list(ROOT_PREFIX):
        if image_cutoff and is_rendered(entry.key) and entry.modified_at < image_cutoff:
            stale.append(entry.key)
            sweep.images += 1
            sweep.image_bytes += entry.size
            if len(stale) == DELETE_BATCH:
                storage.delete(stale)
                stale.clear()
            continue
        if connection is None or (owner := _OWNER.match(entry.key)) is None:
            continue
        # A listing comes in key order, so a document's objects arrive together: a batch is
        # only checked between two owners, never in the middle of one.
        if owner.group() not in pending and len(pending) == CHECK_BATCH:
            _find_orphans(connection, pending, sweep.orphans)
        owned = pending.setdefault(owner.group(), _Owned())
        owned.objects += 1
        owned.size += entry.size
        owned.recent |= entry.modified_at >= settled
    storage.delete(stale)
    if connection is not None:
        _find_orphans(connection, pending, sweep.orphans)
        # Pointed at a database that is not the storage's, this would remove every file. The
        # app ties itself to its database by schema revision, which is not the same thing.
        if sweep.orphans and not connection.scalar(_ANY_DOCUMENTS):
            sweep.refusal = (
                "The database holds no documents at all, but the storage holds "
                f"{_orphan_summary(sweep.orphans)} it does not know. Nothing was removed: "
                "check that DATABASE_URL and STORAGE_URL belong to the same deployment, and "
                "empty the storage by hand if they do."
            )
    return sweep


def _find_orphans(connection: Connection, pending: dict[str, _Owned], found: list[Orphan]) -> None:
    """Add the settled prefixes in `pending` that have no row to `found`, and empty it."""
    settled = {prefix for prefix, owned in pending.items() if not owned.recent}
    for prefix in sorted(settled - _live_prefixes(connection, settled)):
        found.append(Orphan(prefix, pending[prefix].objects, pending[prefix].size))
    pending.clear()


def _live_prefixes(connection: Connection, prefixes: set[str]) -> set[str]:
    """Which of these document and project prefixes still have their row.

    A document's prefix is checked against the key its row holds, not only its id, so a row
    with the same id in another organisation or project does not vouch for it.
    """
    documents = {prefix for prefix in prefixes if "/documents/" in prefix}
    projects = prefixes - documents
    live: set[str] = set()
    if documents:
        keys = connection.scalars(_DOCUMENT_KEYS, {"ids": [_last_id(p) for p in documents]})
        live.update(key.rpartition("/")[0] + "/" for key in keys)
    if projects:
        rows = connection.execute(_PROJECT_IDS, {"ids": [_last_id(p) for p in projects]})
        live.update(project_prefix(org_id, project_id) for org_id, project_id in rows)
    return live & prefixes


def _last_id(prefix: str) -> int:
    return int(prefix.rstrip("/").rpartition("/")[2])


def housekeeping(storage: Storage) -> None:
    """One round of what the app does to the stored data on its own, at intervals.

    Every replica reaches its interval, so a round first takes an advisory lock and skips when
    another replica holds it: the work is idempotent, and walking the storage once is enough.
    The lock is transaction-level, so it ends with the block.
    """
    settings = get_settings()
    keep = timedelta(days=settings.page_image_days) if settings.page_image_days else None
    with engine.begin() as connection:
        taken = connection.scalar(
            text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": HOUSEKEEPING_LOCK}
        )
        if not taken:
            log.info("Housekeeping skipped: another process is on it")
            return
        sweep = sweep_storage(storage, connection, keep)
        if sweep.refusal:
            log.warning("Housekeeping: %s", sweep.refusal)
        else:
            remove_orphans(storage, sweep.orphans)
        pruned = prune_finished(connection)
        events = prune_events(connection)
    if pruned:
        log.info("Housekeeping: removed %s finished jobs older than %s days", pruned, HISTORY.days)
    if events:
        log.info(
            "Housekeeping: removed the %s events of answers finished more than %s days ago",
            events,
            EVENT_HISTORY.days,
        )
    if sweep.images:
        log.info(
            "Housekeeping: %s",
            describe_sweep(sweep.images, sweep.image_bytes, settings.page_image_days),
        )
    if sweep.orphans and not sweep.refusal:
        log.info("Housekeeping: %s", describe_orphans(sweep.orphans, "Removed"))


def remove_orphans(storage: Storage, orphans: Iterable[Orphan]) -> None:
    for orphan in orphans:
        storage.delete_prefix(orphan.prefix)


def describe_sweep(deleted: int, freed: int, days: int) -> str:
    """What a sweep of the rendered images did, for the log and the command."""
    what = "rendered page or thumbnail" if deleted == 1 else "rendered pages and thumbnails"
    return (
        f"Removed {deleted} {what} older than {days} days ({_mb(freed)} MB); they are "
        "rendered again when asked for"
    )


def describe_orphans(orphans: list[Orphan], verb: str) -> str:
    """What a sweep found or removed of deleted documents and projects, for the log and the
    command: "Removed 12 objects (4.2 MB) of 3 documents and 1 project that no longer exist"."""
    exist = "exists" if len(orphans) == 1 else "exist"
    return f"{verb} {_orphan_summary(orphans)} that no longer {exist}"


def _orphan_summary(orphans: list[Orphan]) -> str:
    documents = sum("/documents/" in orphan.prefix for orphan in orphans)
    owners = [(documents, "document"), (len(orphans) - documents, "project")]
    objects = sum(orphan.objects for orphan in orphans)
    size = sum(orphan.size for orphan in orphans)
    return (
        f"{_count(objects, 'object')} ({_mb(size)} MB) of "
        f"{' and '.join(_count(number, noun) for number, noun in owners if number)}"
    )


def usage_report(session: Session, slug: str, days: int) -> list[str]:
    """The lines the `usage` command prints for the organisation `slug`: one per day, kind and
    unit over the last `days` days up to today, in UTC."""
    organization = _organization(session, slug)
    today = datetime.now(UTC).date()
    since = today - timedelta(days=days - 1)
    rows = daily(session, organization.id, since, today)
    span = f"from {since} to {today} (UTC)"
    if not rows:
        return [f"{organization.name} ({slug}) used nothing {span}."]
    return [f"Usage of {organization.name} ({slug}) {span}:", *map(_usage_line, rows)]


def plan_report(session: Session, slug: str, plan: str | None) -> list[str]:
    """The lines the `plan` command prints for the organisation `slug`: its plan and what the
    plan allows (app/entitlements.py), after putting it on `plan` when one is given. The
    caller commits."""
    organization = _organization(session, slug)
    if plan is not None and plan not in PLAN_NAMES:
        raise RuntimeError(f"There is no {plan!r} plan; the plans are {', '.join(PLAN_NAMES)}.")
    was = organization.plan
    if plan is not None:
        organization.plan = plan
    changed = plan is not None and plan != was
    verb = f"moved from the {was.capitalize()} plan to" if changed else "is on"
    lines = [
        f"{organization.name} ({slug}) {verb} the {organization.plan.capitalize()} plan, "
        "which allows:"
    ]
    entitlements = PLANS[organization.plan]
    for field_ in fields(Entitlements):
        lines.append(f"  {field_.name:<20}{_limit(getattr(entitlements, field_.name))}")
    return lines


def _limit(value: int | str | bool | None) -> str:
    if value is None:
        return "no limit"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return f"{value:,}" if isinstance(value, int) else value


def _organization(session: Session, slug: str) -> Organization:
    organization = session.scalar(select(Organization).where(Organization.slug == slug))
    if organization is None:
        raise RuntimeError(f"No organisation has the slug {slug!r}.")
    return organization


def _usage_line(row: DailyUsage) -> str:
    cost = f"  {row.cost_micros / 1_000_000:.4f}" if row.cost_micros is not None else ""
    return f"  {row.day}  {row.kind:<16}{row.unit:<16}{row.quantity:>12,}{cost}"


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}{'' if number == 1 else 's'}"


def _mb(size: int) -> float:
    return round(size / 1_000_000, 1)


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
    queued = connection.scalars(
        text(
            "UPDATE documents SET status = 'queued', "
            "stages = jsonb_set(stages, '{embed}', CAST(:pending AS jsonb)) "
            f"WHERE status <> 'failed' AND id IN ({documents}) RETURNING id"
        ),
        {"model": get_settings().embedding_model, "pending": _PENDING},
    ).all()
    # Bulk work: an upload made meanwhile is not held up behind every old document.
    queue_index_by_id(connection, queued, priority=BULK)
    return len(queued)


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
    orphans = commands.add_parser(
        "sweep-orphans",
        help="list, or with --delete remove, the files of documents that no longer exist",
        description="List the files in the storage STORAGE_URL names that belong to no "
        "document or project in the database: what a delete left behind when the storage "
        "failed after the rows were gone. The running app removes them on its own every few "
        "hours; this shows them at once, and removes them with --delete. Files under an hour "
        "old are left alone, as uploads whose row is not committed yet.",
    )
    orphans.add_argument(
        "--delete", action="store_true", help="remove the files instead of listing them"
    )
    failed = commands.add_parser(
        "failed-jobs",
        help="list, or with --requeue run again, the jobs the worker gave up on",
        description=f"List the jobs the worker gave up on in the last {HISTORY.days} days: lost "
        "to a dying process three times, crashed, or a stage that kept failing through every "
        "retry. Each one's document shows the failure to its user. With --requeue, every "
        'document is queued again from the stage it stopped at, as "Index again" does, and '
        "the jobs leave this list; so does a job whose document was deleted or queued again "
        "meanwhile.",
    )
    failed.add_argument(
        "--requeue", action="store_true", help="queue the documents again instead of listing"
    )
    usage = commands.add_parser(
        "usage",
        help="print what an organisation used per day",
        description="Print what an organisation used per day, in UTC, by kind and unit: the "
        "model calls and tokens of its answers, and whatever else is metered. A cost column "
        "appears once costs are recorded.",
    )
    usage.add_argument("organization", help="the organisation's slug")
    usage.add_argument(
        "--days", type=int, default=30, help="how many days back from today; 30 by default"
    )
    plan = commands.add_parser(
        "plan",
        help="show, or with a plan name set, which plan an organisation is on",
        description="Show which plan an organisation is on and what the plan allows "
        f"({', '.join(PLAN_NAMES)}), or put it on the plan named. A new organisation starts "
        "on the first; the limits take effect at its next upload, question or project.",
    )
    plan.add_argument("organization", help="the organisation's slug")
    plan.add_argument(
        "plan", nargs="?", choices=PLAN_NAMES, help="the plan to put the organisation on"
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
            sweep = sweep_storage(storage, keep_images=timedelta(days=settings.page_image_days))
            print(describe_sweep(sweep.images, sweep.image_bytes, settings.page_image_days) + ".")
        elif args.command == "sweep-orphans":
            storage = storage_from_settings(settings)
            check_schema()
            storage.check()
            with engine.begin() as connection:
                # The app's own round waits rather than running beside this one.
                connection.execute(
                    text("SELECT pg_advisory_xact_lock(:key)"), {"key": HOUSEKEEPING_LOCK}
                )
                sweep = sweep_storage(storage, connection)
                if sweep.refusal:
                    sys.exit(sweep.refusal)
                if not sweep.orphans:
                    print(
                        "Found nothing left behind: every file older than an hour belongs to "
                        "a document or project in the database."
                    )
                elif args.delete:
                    remove_orphans(storage, sweep.orphans)
                    print(describe_orphans(sweep.orphans, "Removed") + ".")
                else:
                    print(describe_orphans(sweep.orphans, "Found") + ":")
                    for orphan in sweep.orphans:
                        print(
                            f"  {orphan.prefix}  {_count(orphan.objects, 'object')}, "
                            f"{_mb(orphan.size)} MB"
                        )
                    print("Run again with --delete to remove them.")
        elif args.command == "failed-jobs":
            check_schema()
            with SessionLocal() as session:
                failures = failed_jobs(session)
                if not failures:
                    print(
                        "No failed jobs: the worker has given up on nothing in the last "
                        f"{HISTORY.days} days."
                    )
                elif args.requeue:
                    outcome = requeue(session, failures)
                    session.commit()
                    for line in outcome.lines:
                        print(line)
                    print(
                        f"{_count(outcome.queued, 'document')} queued again; the worker "
                        "indexes them while it runs, so start the app if it is stopped."
                    )
                else:
                    print(
                        f"Found {_count(len(failures), 'failed job')} from the last "
                        f"{HISTORY.days} days:"
                    )
                    for failure in failures:
                        print(describe(failure))
                    print(
                        "Run again with --requeue to index their documents again from the "
                        "stage they stopped at."
                    )
        elif args.command == "usage":
            if args.days < 1:
                parser.error("--days must be at least 1")
            check_schema()
            with SessionLocal() as session:
                for line in usage_report(session, args.organization, args.days):
                    print(line)
        elif args.command == "plan":
            check_schema()
            with SessionLocal() as session:
                lines = plan_report(session, args.organization, args.plan)
                session.commit()
                for line in lines:
                    print(line)
    except (RuntimeError, StorageError) as error:
        sys.exit(str(error))


if __name__ == "__main__":
    main()
