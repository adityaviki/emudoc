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
are gone; the pruning of jobs that finished longer ago than the queue keeps them, of the
workers not seen for a day, of the events of old answers, of the request counts behind the rate
limits (app/ratelimit.py), of the chats past their plan's retention (app/retention.py), and of
the sessions and one-time links that ended a month ago (app/auth.py); and the purge of the
accounts and the organisations whose deletion is due, after setting those that a restore brought
back to be purged again (app/retention.py).
`sweep-page-images` runs the first part by hand, after lowering the setting; `sweep-orphans`
lists the second part and removes it with `--delete`.

`after-restore` is the step between restoring the database to an earlier point and starting
the app again (app/restore.py): it brings back the files deleted since from a versioned bucket,
moves the ids past the storage's, ends every session and records the restore in the audit logs.
With `--dry-run` it changes nothing and exits 1 when the storage lacks a document's original at
that point, or when ENCRYPTION_KEYS lacks a key the restored secrets need, which makes it the
check of the restore drill (deploying.md, "The drill").

`failed-jobs` lists the jobs the worker gave up on, the dead letters (app/deadletters.py), and
with `--requeue` queues their documents again from the stage they stopped at. `jobs` shows the
queue as it stands, the running jobs with their leases, the workers seen lately and whether
any runs, and with `--document` where one document's indexing stands and what that means: the
operator's look before a release and at a document a member says is stuck (deploying.md,
"Runbooks").

`usage` prints what an organisation used per day (app/usage.py): the model calls and tokens
of its answers today, and whatever is metered next. `costs` is the operator's view across the
organisations: what each one's work cost the platform over a span of days, costliest first,
against its plan's daily limit, then the platform's total against DAILY_SPEND_CAP and the cost
by kind of work and model. It reads the usage rows, since the metrics carry no label per
organisation (app/metrics.py), and prints organisations by slug and name, never an address.

`operators` lists the users who may open the operator's area at /operator (app/operators.py),
and `operators add|remove <address>` grants it or takes it back: the area's one way in, from a
shell on the deployment, as the plan's changes are.

`flags` lists the features behind a flag (app/flags.py), how far each is rolled out and the
organisations an operator turned it on or off for, and `flags <slug> <flag> on|off|rollout`
makes or takes back that choice for one, as the operator's area does.

`encrypt-secrets` encrypts with the first key of ENCRYPTION_KEYS what the database keeps
encrypted (app/encrypted.py) and is not yet: the values from before a column was encrypted, and
those of an earlier key, which is a key's rotation. It runs beside the app.

`models` asks every model the settings name, fallbacks included, what the app asks of it
(app/trial.py), and exits 1 when one does not answer as the app needs: the start only builds
them, so this is where a fallback is tried before its day.

`build-sample` is a developer's: it indexes the sample project's files with the configured
models and writes what the pipeline made of them into the checkout's app/sample/index.json,
which every sign-up copies (app/sample).
"""

import argparse
import hashlib
import logging
import re
import shutil
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from email import policy
from email.parser import BytesParser
from io import BytesIO
from pathlib import Path

from sqlalchemy import Connection, func, select, text
from sqlalchemy.orm import Session

from app.answers import EVENT_HISTORY, prune_events
from app.audit import prune_audit
from app.auth import CREDENTIAL_HISTORY, prune_credentials
from app.billing import (
    BILLED,
    SWITCHES,
    change_plan,
    end_grace_periods,
    managed,
    prune_billing_events,
    renews,
)
from app.bounces import SUPPRESSED_FOR, prune_suppressions
from app.config import PACK_UNITS, export_env_file, get_settings
from app.connectors.sync import prune_connectors
from app.db import MIGRATION_LOCK, SessionLocal, check_schema, engine, unbounded
from app.deadletters import NamedJob, describe, failed_jobs, named_jobs, requeue
from app.encrypted import describe_encrypted, encrypt_secrets, keys
from app.entitlements import (
    FIELDS,
    MONTHS,
    PLANS,
    agreed_with,
    bought_this_month,
    entitlements_of,
    month_of,
    over_limits,
    size_label,
)
from app.export import EXPORT_DAYS, EXPORT_KEPT, prune_exports
from app.flags import FLAGS, choose, chosen, flag_on, rollouts
from app.idempotency import KEPT as IDEMPOTENCY_KEPT
from app.idempotency import prune_idempotency_keys
from app.inbound import NO_DOMAIN, Received, prune_inbound, receive
from app.jobs import (
    ANSWER_QUESTION,
    BULK,
    HISTORY,
    INDEX_DOCUMENT,
    JOB_STATES,
    QUEUE_BY_STATE,
    SEEN_WITHIN,
    WORKERS_KEPT,
    SeenWorker,
    prune_finished,
    prune_workers,
    queue_index_by_id,
    workers_seen,
)
from app.logs import configure_logging
from app.models import JOB_KINDS, STAGES, Document, Job, Organization, Project, User
from app.models import PLANS as PLAN_NAMES
from app.notices import (
    NOTIFICATION_DAYS,
    app_address,
    prune_notifications,
    prune_quota_notices,
    settle_uploads,
)
from app.operators import grant, operators, revoke
from app.parsing import MIME_TYPES
from app.postgres import concerns, database_report, describe_database, span
from app.ratelimit import prune_rate_limits
from app.restore import after_restore, describe_restore
from app.retention import (
    day,
    prune_chats,
    prune_drafts,
    prune_runs,
    purge_due_accounts,
    purge_due_organizations,
    reapply_erasures,
    reapply_organization_erasures,
    retention_held,
)
from app.sample import INDEX, build_sample, written
from app.storage import (
    DELETE_BATCH,
    ROOT_PREFIX,
    LocalStorage,
    Storage,
    StorageError,
    is_export,
    is_rendered,
    project_prefix,
    storage_from_settings,
)
from app.trash import TRASH_DAYS, purge_trash
from app.trial import describe_trials, try_models
from app.usage import (
    DailyUsage,
    OrganizationCost,
    daily,
    model_costs,
    money,
    organization_costs,
    summed,
)

# By its name rather than `__name__`, which is "__main__" when the module runs as a command.
log = logging.getLogger("app.maintenance")

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
# How many of a document's index jobs `jobs --document` shows, newest first.
DOCUMENT_JOBS = 5
# What a key belongs to: the document's prefix when it has one, else the project's. The match
# is the owner's prefix, as `document_prefix` and `project_prefix` in app/storage.py build it.
_OWNER = re.compile(r"orgs/\d+/projects/\d+/(?:documents/\d+/)?")
# A size as `plan --set storage_bytes=` takes it: 500 GB, 1.5TB, 100000000.
_SIZE = re.compile(r"([\d.,]+)\s*(bytes|kb|mb|gb|tb)?", re.IGNORECASE)
_UNITS = {"bytes": 1, "kb": 1_000, "mb": 1_000_000, "gb": 1_000_000_000, "tb": 10**12}

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
        # A look at every passage, at every start: on a large database longer than a request.
        unbounded(connection)
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
        unbounded(connection)
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
    # The projects' exports past theirs, removed likewise.
    exports: int = 0
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
    keep_exports: timedelta | None = None,
) -> Sweep:
    """Walk the whole storage once, for the things the app tidies.

    The rendered pages and thumbnails older than `keep_images` are deleted as they are found,
    in batches, so nothing waits for the end of the walk; they are a cache of the originals and
    a page asked for again is rendered again. So are the projects' exports older than
    `keep_exports`, which are served no longer (app/export.py). Every other object is matched to
    the document or
    project its key names, and the prefixes whose row is gone from the database `connection`
    reaches are returned as orphans: what a delete left behind when the storage failed after
    the rows were gone. Each part is skipped when its argument is None.

    Rows are checked in batches of CHECK_BATCH prefixes, never one query per object. A key
    outside the layout is nobody's and is left alone, and so is any prefix with an object
    younger than UPLOAD_GRACE, which is an upload whose row is not committed yet.
    """
    now = datetime.now(UTC)
    image_cutoff = now - keep_images if keep_images else None
    export_cutoff = now - keep_exports if keep_exports else None
    settled = now - UPLOAD_GRACE
    sweep = Sweep()
    stale: list[str] = []
    pending: dict[str, _Owned] = {}

    for entry in storage.list(ROOT_PREFIX):
        if image_cutoff and is_rendered(entry.key) and entry.modified_at < image_cutoff:
            sweep.images += 1
            sweep.image_bytes += entry.size
        elif export_cutoff and is_export(entry.key) and entry.modified_at < export_cutoff:
            sweep.exports += 1
        elif connection is None or (owner := _OWNER.match(entry.key)) is None:
            continue
        else:
            # A listing comes in key order, so a document's objects arrive together: a batch
            # is only checked between two owners, never in the middle of one.
            if owner.group() not in pending and len(pending) == CHECK_BATCH:
                _find_orphans(connection, pending, sweep.orphans)
            owned = pending.setdefault(owner.group(), _Owned())
            owned.objects += 1
            owned.size += entry.size
            owned.recent |= entry.modified_at >= settled
            continue
        stale.append(entry.key)
        if len(stale) == DELETE_BATCH:
            storage.delete(stale)
            stale.clear()
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
    The lock is transaction-level, so it ends with the block. What has been in the trash for
    TRASH_DAYS is deleted for good in it, and its files once it has committed (app/trash.py),
    as a delete for good does them. The accounts due for purging go
    after it, each in a transaction of its own, since their files are discarded only once their
    rows are gone for good, then the organisations whose owners asked for them to be deleted
    (app/retention.py), after the accounts so that no member about to go is given an
    organisation of their own, and so do the organisations whose payment has failed for longer
    than the grace period, whose owners are mailed (app/billing.py), and the uploads whose
    members were not told they were indexed (app/notices.py).
    """
    settings = get_settings()
    keep = timedelta(days=settings.page_image_days) if settings.page_image_days else None
    with engine.begin() as connection:
        # The walk keeps this transaction open, idle, between its batches, for as long as the
        # storage takes to list.
        unbounded(connection)
        taken = connection.scalar(
            text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": HOUSEKEEPING_LOCK}
        )
        if not taken:
            log.info("Housekeeping skipped: another process is on it")
            return
        sweep = sweep_storage(storage, connection, keep, EXPORT_KEPT)
        if sweep.refusal:
            log.warning("Housekeeping: %s", sweep.refusal)
        else:
            remove_orphans(storage, sweep.orphans)
        pruned = prune_finished(connection)
        prune_workers(connection)
        events = prune_events(connection)
        minutes = prune_rate_limits(connection)
        answers_kept = prune_idempotency_keys(connection)
        chats = prune_chats(connection)
        runs = prune_runs(connection)
        drafts = prune_drafts(connection)
        audited = prune_audit(connection)
        credentials = prune_credentials(connection)
        billing_events = prune_billing_events(connection)
        spared = prune_suppressions(connection)
        prune_inbound(connection)
        prune_connectors(connection)
        prune_quota_notices(connection)
        notifications = prune_notifications(connection)
        exports = prune_exports(connection)
        emptied = purge_trash(connection)
    storage.discard(*emptied.prefixes)
    reapply_erasures(storage)
    reapply_organization_erasures(storage)
    accounts = purge_due_accounts(storage)
    organizations = purge_due_organizations(storage, renews=renews)
    lapsed = end_grace_periods()
    uploads = settle_uploads()
    if accounts:
        log.info("Housekeeping: deleted %s accounts whose deletion was due", accounts)
    if organizations:
        log.info("Housekeeping: deleted %s organisations whose deletion was due", organizations)
    if lapsed:
        log.info(
            "Housekeeping: moved %s organisations to the Free plan, their payment failed for "
            "longer than the grace period",
            lapsed,
        )
    if uploads:
        log.info(
            "Housekeeping: found %s uploads indexed that the end of their last work did not",
            uploads,
        )
    if billing_events:
        log.info("Housekeeping: forgot %s Stripe events acted on long ago", billing_events)
    if exports or sweep.exports:
        log.info(
            "Housekeeping: forgot %s project exports and removed %s export files older than %s "
            "days",
            exports,
            sweep.exports,
            EXPORT_DAYS,
        )
    if notifications:
        log.info(
            "Housekeeping: removed %s notices older than %s days from notification centres",
            notifications,
            NOTIFICATION_DAYS,
        )
    if spared:
        log.info(
            "Housekeeping: writes again to %s addresses the mail provider said to spare more "
            "than %s days ago",
            spared,
            SUPPRESSED_FOR.days,
        )
    if chats:
        log.info("Housekeeping: deleted %s chats kept past their plan's retention", chats)
    if runs:
        log.info(
            "Housekeeping: deleted %s runs of cross-checks kept past their plan's retention", runs
        )
    if drafts:
        log.info("Housekeeping: deleted %s drafts kept past their plan's retention", drafts)
    if emptied.projects or emptied.folders or emptied.documents or emptied.chats:
        log.info(
            "Housekeeping: deleted for good what was in the trash for %s days: %s projects, "
            "%s folders, %s documents and %s chats",
            TRASH_DAYS,
            emptied.projects,
            emptied.folders,
            emptied.documents,
            emptied.chats,
        )
    if audited:
        log.info("Housekeeping: deleted %s audit log rows kept past their plan's days", audited)
    if credentials:
        log.info(
            "Housekeeping: removed %s sessions and links that ended more than %s days ago",
            credentials,
            CREDENTIAL_HISTORY.days,
        )
    if minutes:
        log.info("Housekeeping: removed the request counts of %s minutes long past", minutes)
    if answers_kept:
        log.info(
            "Housekeeping: forgot %s idempotency keys claimed more than %s hours ago",
            answers_kept,
            int(IDEMPOTENCY_KEPT.total_seconds() // 3600),
        )
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


def costs_report(session: Session, days: int) -> list[str]:
    """The lines the `costs` command prints for the last `days` days up to today, in UTC:
    every organisation that used anything, costliest first, with its plan's daily limit, its
    costliest day against it and the days it reached it; the platform's line, held to
    DAILY_SPEND_CAP; the cost by kind of work and model; and the models whose tokens were
    recorded without a price, which no cost counts."""
    settings = get_settings()
    today = datetime.now(UTC).date()
    since = today - timedelta(days=days - 1)
    span = f"from {since} to {today} (UTC)"
    costs = organization_costs(session, since, today)
    if not costs:
        return [f"No organisation used anything {span}."]
    organizations = {
        organization.id: organization
        for organization in session.scalars(
            select(Organization).where(Organization.id.in_([cost.org_id for cost in costs]))
        )
    }
    rows = [
        (
            "organisation",
            "plan",
            "cost",
            "limit",
            "top day",
            "at limit",
            "answers",
            "calls",
            "pages",
            "",
        )
    ]
    for cost in costs:
        organization = organizations[cost.org_id]
        cap = entitlements_of(organization).daily_spend_micros
        rows.append(_cost_row(organization.slug, organization.plan, cost, cap, organization.name))
    cap = settings.daily_spend_cap_micros or None
    rows.append(_cost_row("platform", "", summed(costs), cap, _count(len(costs), "organisation")))
    lines = [
        f"Model costs per organisation {span}, in {settings.price_currency}, costliest first:",
        *_table(rows, left={0, 1, 9}),
    ]
    if idle := session.scalar(select(func.count()).select_from(Organization)) - len(costs):
        lines.append(f"{_count(idle, 'other organisation')} used nothing.")
    lines += [
        "The top day is the costliest, with its share of the daily limit: the plan's, or the",
        "platform's DAILY_SPEND_CAP. At limit counts the days that reached it.",
    ]
    models = model_costs(session, since, today)
    if models:
        rows = [("kind", "model", "cost", "calls", "tokens in", "tokens out")]
        for spent in models:
            rows.append(
                (
                    spent.kind,
                    spent.model,
                    _known(spent.cost_micros, spent.input_tokens + spent.output_tokens),
                    f"{spent.calls:,}",
                    f"{spent.input_tokens:,}",
                    f"{spent.output_tokens:,}",
                )
            )
        lines += ["", "By kind of work and model:", *_table(rows, left={0, 1})]
    unpriced: Counter[str] = Counter()
    kinds: dict[str, list[str]] = {}
    for spent in models:
        if spent.unpriced_tokens:
            unpriced[spent.model] += spent.unpriced_tokens
            kinds.setdefault(spent.model, []).append(spent.kind)
    if unpriced:
        lines += [
            "",
            "These models' tokens were recorded without a price, so no cost above counts them;",
            "MODEL_PRICES prices a model per million tokens in and out from its next row on:",
        ]
        rows = [
            (model, _count(tokens, "token"), f"({', '.join(kinds[model])})")
            for model, tokens in unpriced.most_common()
        ]
        lines += _table(rows, left={0, 2})
    return lines


def plan_report(
    session: Session,
    slug: str,
    plan: str | None = None,
    *,
    seats: str | None = None,
    figures: Sequence[str] = (),
    unset: Sequence[str] = (),
) -> list[str]:
    """The lines the `plan` command prints for the organisation `slug`: its plan, its seats and
    what it allows (app/entitlements.py), each figure agreed with it marked, after changing what
    the arguments ask through `change_plan` (app/billing.py): `plan` another plan, which clears
    the seats and figures of the one before; `seats` a number or "none"; `figures` agreed ones
    as NAME=VALUE; `unset` the names of agreed ones that are the plan's again. The caller
    commits."""
    organization = _organization(session, slug)
    was = organization.plan
    moving = plan is not None and plan != was
    bought = None if moving else organization.seats
    agreed = {} if moving else dict(organization.overrides or {})
    if seats is not None:
        bought = None if seats.strip().lower() == "none" else _whole(seats, "--seats")
    for name in unset:
        if name not in FIELDS:
            raise RuntimeError(f"{name!r} is no entitlement; they are {', '.join(FIELDS)}.")
        agreed.pop(name, None)
    for figure in figures:
        name, equals, value = figure.partition("=")
        if not equals:
            raise RuntimeError(f"--set takes NAME=VALUE, such as projects=500, not {figure!r}.")
        agreed[name.strip()] = figure_of(name.strip(), value)
    dropped = (organization.seats, agreed_with(organization)) if moving else (None, {})
    change_plan(session, organization, plan or was, seats=bought, overrides=agreed)

    verb = f"moved from the {was.capitalize()} plan to" if moving else "is on"
    with_seats = f" with {_count(bought, 'seat')}" if bought is not None else ""
    lines = [
        f"{organization.name} ({slug}) {verb} the {organization.plan.capitalize()} plan"
        f"{with_seats}, which allows:"
    ]
    entitlements, own = entitlements_of(organization), PLANS[organization.plan]
    for name in FIELDS:
        line = f"  {name:<20}{entitlement_words(name, getattr(entitlements, name))}"
        if name in agreed:
            line += f" (agreed; the plan's {entitlement_words(name, getattr(own, name))})"
        elif name == "members" and bought is not None:
            line += f" (its seats; the plan's {entitlement_words(name, own.members)})"
        lines.append(line)
    if dropped[0] is not None:
        lines.append(
            f"The {_count(dropped[0], 'seat')} it bought on the {was.capitalize()} plan "
            "no longer count."
        )
    if dropped[1]:
        lines.append(
            f"The figures agreed with it on the {was.capitalize()} plan no longer "
            f"count: {', '.join(dropped[1])}."
        )
    if beyond := over_limits(session, organization):
        lines.append(
            f"It holds more than it allows, {', '.join(excess.words for excess in beyond)}: "
            "it keeps it all, and is read-only until it is within the plan again."
        )
    if standing := retention_held(organization):
        kept = standing.kept
        lines.append(
            f"Until {day(standing.until)} it keeps chats {_kept(kept.chat_days)} and audit log "
            f"entries {_kept(kept.audit_days)}, as before its move to a plan that keeps them for "
            "less time."
        )
    start = month_of()[0]
    if bought := [
        f"{count:,} {unit}"
        for unit in PACK_UNITS
        if (count := bought_this_month(session, organization.id, unit, start))
    ]:
        lines.append(
            f"It bought {' and '.join(bought)} in packs for {MONTHS[start.month - 1]}, on top "
            "of the month's allowance."
        )
    if organization.subscription_status in BILLED:
        # Stripe's next event about it would undo a move made here, unless its terms are agreed.
        lines.append(
            f"It pays for a subscription at Stripe, {organization.stripe_subscription_id} "
            f"({organization.subscription_status}), which sets its plan"
            + (
                ": a change made here lasts until Stripe's next event about it. Change the "
                "subscription in Stripe's dashboard instead."
                if managed(organization)
                else " no longer, since its terms are agreed."
            )
        )
    return lines


def receive_mail_report(storage: Storage, raw: bytes, to: list[str]) -> list[str]:
    """Take a message saved as it was sent into the projects it is addressed to, as a provider's
    way in would, its From address taken as proven since the operator vouches for it; the lines
    that say what became of it in each. Recipients are `to`, or else its To, Cc and
    Delivered-To."""
    if not get_settings().inbound_domain:
        raise RuntimeError(NO_DOMAIN)
    if not to:
        message = BytesParser(policy=policy.default).parse(BytesIO(raw), headersonly=True)
        to = [
            address.addr_spec
            for name in ("To", "Cc", "Delivered-To")
            for header in message.get_all(name) or []
            for address in getattr(header, "addresses", ())
        ]
    received = Received(f"command:{hashlib.sha256(raw).hexdigest()}", raw, to, True)
    with SessionLocal() as session:
        records = receive(session, storage, received, app_address())
        lines = []
        for record in records:
            project = session.get(Project, record.project_id)
            where = f"{project.organization.slug} / {project.name}" if project else "A project"
            if record.outcome == "refused":
                lines.append(f"{where}: turned away. {record.reason}")
                continue
            kept = f"{record.kept} document{'' if record.kept == 1 else 's'}"
            lines.append(f"{where}: kept {kept}." if record.kept else f"{where}: kept nothing.")
            lines += [f"  {file['filename']}: {file['reason']}" for file in record.rejected]
    return lines or [
        "No project took the message: none of its recipients is a project's address at "
        f"{get_settings().inbound_domain} that has not taken it already, or email in is off "
        "for the organisation."
    ]


def flags_report(
    session: Session, slug: str | None = None, name: str | None = None, state: str | None = None
) -> list[str]:
    """The lines the `flags` command prints: every feature flag across the deployment, or with
    `slug` how each stands for that organisation, after turning the flag `name` on or off for
    it, or back to its rollout (`state` on, off or rollout), through `choose` (app/flags.py),
    which writes a row of its audit log. The caller commits."""
    if not FLAGS:
        return ["No feature is behind a flag: FLAGS in app/flags.py names none."]
    if slug is None:
        lines = []
        for rollout in rollouts(session):
            lines.append(
                f"{rollout.name}, {rollout.flag.title}: {_rolled_out(rollout.flag.share)}, on "
                f"for {rollout.on:,} of {_count(rollout.organizations, 'organisation')}."
            )
            for on in (True, False):
                if picked := [slug for _, slug, _, chose in rollout.chosen if chose is on]:
                    lines.append(f"  Turned {'on' if on else 'off'} for {', '.join(picked)}.")
        return lines
    organization = _organization(session, slug)
    lines = []
    if name is not None:
        if name not in FLAGS:
            raise RuntimeError(
                f"There is no feature flag {name!r}; the flags are {', '.join(FLAGS)}."
            )
        on = {"on": True, "off": False, "rollout": None}[state or ""]
        changed = choose(session, organization, name, on)
        said = "follows its rollout" if on is None else f"is {'on' if on else 'off'}"
        lines.append(
            f"{name} {said} for {organization.name} "
            + ("now." if changed else "already, so nothing changed.")
        )
    lines.append(f"{organization.name} ({slug}) has these feature flags:")
    rows = []
    for flag_name, entry in FLAGS.items():
        choice = chosen(organization, flag_name)
        rows.append(
            (
                flag_name,
                "on" if flag_on(organization, flag_name) else "off",
                _rolled_out(entry.share)
                if choice is None
                else f"turned {'on' if choice else 'off'} for it",
            )
        )
    return lines + _table(rows, left={0, 1, 2})


def _rolled_out(share: int) -> str:
    if share == 0:
        return "rolled out to none"
    if share == 100:
        return "rolled out to every organisation"
    return f"rolled out to {share} % of organisations"


def operators_report(session: Session, action: str | None = None, email: str = "") -> list[str]:
    """The lines the `operators` command prints: every operator, after granting the area to the
    account with the address `email` (`action` "add") or taking it back ("remove"), and where
    the area answers from. The caller commits."""
    settings = get_settings()
    lines = []
    if action is not None:
        user = session.scalar(select(User).where(User.email == email.strip().lower()))
        if user is None:
            raise RuntimeError(
                f"No account has the address {email!r}; its owner signs up first, then is made "
                "an operator."
            )
        if action == "add":
            done = grant(session, user)
            lines.append(
                f"{user.name or user.email} is an operator now."
                if done
                else f"{user.name or user.email} was an operator already."
            )
            if settings.environment == "production" and not user.mfa_enabled:
                lines.append(
                    "They turn two-factor sign-in on under their account before the area opens."
                )
        else:
            done = revoke(session, user)
            lines.append(
                f"{user.name or user.email} is no longer an operator."
                if done
                else f"{user.name or user.email} was no operator."
            )
        session.flush()
    listed = operators(session)
    if listed:
        lines.append(f"{_count(len(listed), 'operator')}, who open the area at /operator:")
        rows = [
            (
                str(user.id),
                user.name,
                user.email,
                f"since {granted.astimezone(UTC):%Y-%m-%d}",
                "two-factor" if user.mfa_enabled else "no two-factor",
            )
            for user, granted in listed
        ]
        lines += _table(rows, left={1, 2, 3, 4})
    else:
        lines.append("Nobody is an operator; `operators add <address>` makes one.")
    networks = ", ".join(str(network) for network in settings.operator_nets)
    lines.append(
        f"The area answers requests from {networks} (OPERATOR_NETWORKS)."
        if networks
        else "The area answers nobody until OPERATOR_NETWORKS names the operators' addresses."
    )
    return lines


def _kept(days: int | None) -> str:
    return "for good" if days is None else _count(days, "day")


def jobs_report(session: Session) -> list[str]:
    """The lines the `jobs` command prints: each kind's jobs by state, how long the oldest due
    one has waited for a worker, each running job with what it is for, its attempt and its
    lease, which says whether its worker still holds it, and the workers seen lately."""
    now = session.scalar(select(func.now()))
    return [*_queue_lines(session, now), *workers_lines(workers_seen(session), now)]


def workers_lines(workers: list[SeenWorker], now: datetime) -> list[str]:
    """The workers seen within WORKERS_KEPT, each with its release, its start and how it stands,
    and a line when none runs, which leaves the queue's work waiting."""
    lines = []
    if workers:
        lines.append(f"The workers seen in the last {span(WORKERS_KEPT)}:")
        rows = [("worker", "release", "started", "stands")]
        rows += [
            (seen.id, _release(seen.version), _at(seen.started_at), _stands(seen, now))
            for seen in workers
        ]
        lines += _table(rows, left={0, 1, 2, 3})
    if not any(seen.running for seen in workers):
        lines.append(
            f"No worker has been seen running in the last {span(SEEN_WITHIN)}: nothing is "
            "indexed, answered, mailed or exported until one runs (`python -m app.worker`)."
        )
    return lines


def _release(version: str) -> str:
    """Seven digits of a commit's SHA, as the user menu shows it, or the word a checkout runs as."""
    return version[:7] if re.fullmatch(r"[0-9a-f]{40}", version) else version


def _stands(seen: SeenWorker, now: datetime) -> str:
    if seen.running:
        return f"running, seen {span(now - seen.last_seen_at)} ago"
    if seen.stopped_at is not None:
        return f"stopped {span(now - seen.stopped_at)} ago"
    # Its heartbeat stopped without a stop: the process died, or lost the database.
    return f"not seen for {span(now - seen.last_seen_at)}"


def _queue_lines(session: Session, now: datetime) -> list[str]:
    counts: dict[tuple[str, str], int] = {}
    oldest: dict[str, float] = {}
    for kind, state, count, waited in session.execute(QUEUE_BY_STATE):
        counts[kind, state] = count
        if state == "due":
            oldest[kind] = float(waited)
    if not counts:
        return [f"Nothing is queued or running, and no job failed in the last {HISTORY.days} days."]
    kinds = [kind for kind in JOB_KINDS if any(found == kind for found, _ in counts)]
    rows = [("kind", *JOB_STATES)]
    rows += [
        (kind, *(f"{counts.get((kind, state), 0):,}" for state in JOB_STATES)) for kind in kinds
    ]
    lines = [f"The queue at {_at(now)}:", *_table(rows, left={0})]
    lines += [
        f"The oldest due {kind} job has waited {span(timedelta(seconds=oldest[kind]))} for a "
        "worker."
        for kind in kinds
        if kind in oldest
    ]
    running = named_jobs(session, Job.status == "running")
    if running:
        lines.append("Running:")
        rows = [("job", "kind", "attempt", "due", "lease", "about")]
        rows += [
            (
                str(named.job.id),
                named.job.kind,
                f"{named.job.attempts} of {named.job.max_attempts}",
                f"{span(now - named.job.run_at)} ago",
                _lease(named.job, now),
                _about(named),
            )
            for named in running
        ]
        lines += _table(rows, left={1, 2, 4, 5})
        if any(named.job.lease_until < now for named in running):
            lines.append(
                "A lease that ran out is a worker that stopped or lost the database: the next "
                "free thread of its lane takes the job over."
            )
    if any(state == "failed" for _, state in counts):
        lines.append("`failed-jobs` lists the failed ones with why.")
    return lines


def document_report(session: Session, document_id: int) -> list[str]:
    """The lines `jobs --document` prints: where the document stands, each stage with what its
    members read of it, its index jobs newest first, and what the newest one means."""
    document = session.get(Document, document_id)
    if document is None:
        raise RuntimeError(f"No document has the id {document_id}; it may have been deleted.")
    now = session.scalar(select(func.now()))
    project = document.project
    ready = f"first ready {_at(document.ready_at)}" if document.ready_at else "not ready yet"
    lines = [
        f"Document {document.id}, {document.filename} in {project.organization.slug} / "
        f"{project.name}, is {document.status}: uploaded {_at(document.created_at)}, {ready}."
    ]
    rows = [("stage", "status", "what its members read")]
    for name in STAGES:
        stage = document.stages.get(name, {})
        rows.append((name, stage.get("status", "pending"), stage.get("detail", "")))
    lines += _table(rows, left={0, 1, 2})
    jobs = session.scalars(
        select(Job)
        .where(Job.kind == INDEX_DOCUMENT, Job.payload["document_id"].as_integer() == document.id)
        .order_by(Job.id.desc())
        .limit(DOCUMENT_JOBS)
    ).all()
    if jobs:
        lines.append("Its index jobs, newest first:")
        rows = [("job", "status", "attempt", "due", "lease or end")]
        for job in jobs:
            ended = f"ended {span(now - job.finished_at)} ago" if job.finished_at else ""
            lease = _lease(job, now) if job.status == "running" else ended
            due = _at(job.run_at)
            rows.append(
                (str(job.id), job.status, f"{job.attempts} of {job.max_attempts}", due, lease)
            )
        lines += _table(rows, left={1, 2, 3, 4})
        if jobs[0].status == "failed" and jobs[0].last_error:
            lines.append(f"  Why job {jobs[0].id} failed: {jobs[0].last_error}")
    lines.append(_standing(document, jobs[0] if jobs else None, now))
    return lines


def _standing(document: Document, job: Job | None, now: datetime) -> str:
    """What the newest index job says of the document, and what to do about it, if anything."""
    if job is not None and job.status == "queued":
        if job.run_at <= now:
            return (
                f"Its job has been due for {span(now - job.run_at)} and waits for a free indexer "
                "thread: `jobs` shows the queue, and more workers or WORKER_CONCURRENCY shorten "
                "it."
            )
        return (
            f"Its job waits until {_at(job.run_at)}: a retry's backoff, or the day's spending "
            "limit until midnight UTC, as its stage's detail says."
        )
    if job is not None and job.status == "running":
        if job.lease_until >= now:
            return (
                f"Worker {job.worker_id} is running it; its run's line in that worker's log says "
                'where the time went once it ends (deploying.md, "Traces").'
            )
        return (
            f"Its lease ran out {span(now - job.lease_until)} ago: worker {job.worker_id} "
            "stopped or lost the database, and the next free indexer thread takes the job over "
            "from the stage it stopped at."
        )
    if job is not None and job.status == "failed":
        return (
            "The worker gave up on it, and its members see the failure; once the cause is "
            "fixed, `failed-jobs --requeue` runs it again from the stage it stopped at."
        )
    if document.status in ("queued", "processing"):
        return (
            'No job is queued or running for it, which the app never leaves: "Index again" in '
            "the library queues one."
        )
    return {
        "ready": "It is ready.",
        "failed": "A required stage failed for a reason a retry would not cure, which the stage "
        'says; "Index again" in the library runs it again.',
        "quarantined": "The malware scan flagged it: it is neither indexed nor served.",
    }[document.status]


def _lease(job: Job, now: datetime) -> str:
    left = job.lease_until - now
    if left >= timedelta(0):
        return f"{span(left)} left"
    return f"ran out {span(-left)} ago"


def _about(named: NamedJob) -> str:
    """What a running job is for, as "acme / Contracts / invoice.pdf", an answer by its id."""
    if named.job.kind == ANSWER_QUESTION:
        return f"{named.about} / answer {named.job.payload.get('message_id')}"
    return named.about


def _at(moment: datetime) -> str:
    return f"{moment.astimezone(UTC):%Y-%m-%d %H:%M} UTC"


def entitlement_words(name: str, value: int | str | bool | None) -> str:
    """A figure as the `plan` command prints it, which is also how `--set` takes it, and as the
    operator's area shows it (app/api/operator.py)."""
    if value is None:
        return "no limit"
    if isinstance(value, bool):
        return "yes" if value else "no"
    # The day's spending limit is an amount of money, and the storage a size.
    if name == "daily_spend_micros":
        return f"{money(value)} a day"
    if name == "storage_bytes":
        return size_label(value)
    return f"{value:,}" if isinstance(value, int) else value


def figure_of(name: str, given: str) -> object:
    """A figure as the operator gives it after `--set NAME=` or in the operator's area, read as
    `entitlement_words` prints it: "none" for no limit, yes or no, a count (1,000), a size
    (500 GB) or an amount of PRICE_CURRENCY a day (25.00, or USD 25.00 a day). What it cannot
    read is returned as it was, for `change_plan` to refuse with what the figure should be."""
    value = given.strip()
    if value.lower() in ("none", "no limit"):
        return None
    if name in SWITCHES:
        return {"yes": True, "no": False}.get(value.lower(), value)
    if name == "model_tier":
        return value
    try:
        if name == "storage_bytes" and (size := _SIZE.fullmatch(value)):
            number, unit = size.groups()
            return int(Decimal(number.replace(",", "")) * _UNITS[(unit or "bytes").lower()])
        if name == "daily_spend_micros":
            amount = value.removeprefix(get_settings().price_currency).removesuffix("a day")
            amount = amount.strip()
            return int(Decimal(amount.replace(",", "")) * 1_000_000)
        return int(value.replace(",", "").replace("_", ""))
    except (InvalidOperation, ValueError):
        return value


def _whole(given: str, option: str) -> int:
    try:
        return int(given.replace(",", ""))
    except ValueError:
        raise RuntimeError(f"{option} takes a whole number or none, not {given!r}.") from None


def _organization(session: Session, slug: str) -> Organization:
    organization = session.scalar(select(Organization).where(Organization.slug == slug))
    if organization is None:
        raise RuntimeError(f"No organisation has the slug {slug!r}.")
    return organization


def _usage_line(row: DailyUsage) -> str:
    cost = f"  {row.cost_micros / 1_000_000:.4f}" if row.cost_micros is not None else ""
    return f"  {row.day}  {row.kind:<16}{row.unit:<16}{row.quantity:>12,}{cost}"


def _cost_row(
    who: str, plan: str, cost: OrganizationCost, cap: int | None, name: str
) -> tuple[str, ...]:
    """One line of the `costs` table: an organisation's, or the platform's."""
    tokens = cost.input_tokens + cost.output_tokens
    top = _known(cost.top_day, tokens)
    if cost.top_day is not None and cap:
        top += f" ({cost.top_day * 100 // cap} %)"
    return (
        who,
        plan,
        _known(cost.cost_micros, tokens),
        "none" if cap is None else _amount(cap),
        top,
        "" if cap is None else f"{cost.days_at(cap):,}",
        f"{cost.answers:,}",
        f"{cost.calls:,}",
        f"{cost.pages:,}",
        name,
    )


def _known(micros: int | None, tokens: int) -> str:
    """A cost as the `costs` command shows it: unpriced when tokens were recorded without a
    price, and nothing when there were no tokens to price."""
    if micros is not None:
        return _amount(micros)
    return "unpriced" if tokens else _amount(0)


def _amount(micros: int) -> str:
    # Four places, as the `usage` command prints a cost: a cheap model's answer costs a tenth
    # of a cent.
    return f"{micros / 1_000_000:,.4f}"


def _table(rows: list[tuple[str, ...]], left: set[int]) -> list[str]:
    """The rows as lines of columns two spaces apart: the columns in `left` read left to right,
    the others, numbers, line up on the right."""
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
    return [
        "  "
        + "  ".join(
            cell.ljust(width) if column in left else cell.rjust(width)
            for column, (cell, width) in enumerate(zip(row, widths, strict=True))
        ).rstrip()
        for row in rows
    ]


def _count(number: int, noun: str) -> str:
    return f"{number:,} {noun}{'' if number == 1 else 's'}"


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


def _moment(value: str) -> datetime:
    """A point in time as the operator gives it, which must say its zone. pgBackRest's
    `--target` form, `2026-10-15 09:30:00+00`, is one, so the same string serves both."""
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{value!r} is not a time such as '2026-10-15 09:30:00+00'"
        ) from None
    if moment.tzinfo is None:
        raise argparse.ArgumentTypeError(
            f"{value!r} has no zone; add +00 for UTC, as in '2026-10-15 09:30:00+00'"
        )
    return moment.astimezone(UTC)


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
    restore = commands.add_parser(
        "after-restore",
        help="set the files, the ids and the sign-ins right after a restore of the database",
        description="Run once after the database was restored to an earlier point, with the "
        "app stopped, before it starts again: bring back from a versioned bucket the files "
        "deleted since that point, move the ids of organisations, projects and documents past "
        "every id the storage holds, end every session and unused link, record the restore in "
        "each organisation's audit log, and purge again the accounts deleted since. It then "
        "names the documents whose original the storage lacks, and the secrets encrypted "
        "with a key ENCRYPTION_KEYS no longer holds. Running it again is safe. With --dry-run "
        "it changes neither the database nor the storage, says what it would do, and exits "
        "with 1 when the storage lacks a document's original at that point or a secret's key "
        "is gone: the check of a restore drill, against a restored copy of the database and "
        "the live storage.",
    )
    restore.add_argument(
        "restored_to",
        metavar="TIME",
        type=_moment,
        help="the point the database was restored to, with its zone: '2026-10-15 09:30:00+00'",
    )
    restore.add_argument(
        "--dry-run",
        action="store_true",
        help="change nothing; say what would be done and whether the restore is whole",
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
    costs = commands.add_parser(
        "costs",
        help="print what each organisation's model use cost, costliest first",
        description="Print what each organisation's model use cost the platform per day, in "
        "UTC, over the last days up to today, costliest first: its plan, the plan's daily "
        "limit, its costliest day against that limit and the days it reached it, its answers, "
        "model calls and pages indexed; then the platform's total against DAILY_SPEND_CAP, the "
        "cost by kind of work and model, and the models whose tokens were recorded without a "
        "price in MODEL_PRICES. `usage <slug>` breaks one organisation down by day.",
    )
    costs.add_argument(
        "--days", type=int, default=30, help="how many days back from today; 30 by default"
    )
    plan = commands.add_parser(
        "plan",
        help="show, or change, an organisation's plan, seats and agreed figures",
        description="Show which plan an organisation is on and what it allows "
        f"({', '.join(PLAN_NAMES)}), or put it on the plan named, which clears the seats and "
        "the figures agreed on the one before. A new organisation starts on the first; the "
        "limits take effect at its next upload, question or project, and lowering them "
        "removes nothing.",
    )
    plan.add_argument("organization", help="the organisation's slug")
    plan.add_argument(
        "plan", nargs="?", choices=PLAN_NAMES, help="the plan to put the organisation on"
    )
    plan.add_argument(
        "--seats",
        metavar="N",
        help="the members it bought, on a plan sold by the seat (team); none for the plan's own",
    )
    plan.add_argument(
        "--set",
        dest="figures",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="agree a figure in place of the plan's, as the report prints it: projects=500, "
        "storage_bytes=1 TB, daily_spend_micros=25.00 (an amount of PRICE_CURRENCY), "
        "api_access=yes, retention_days=none for no limit; again for each",
    )
    plan.add_argument(
        "--unset",
        action="append",
        default=[],
        metavar="NAME",
        help="make an agreed figure the plan's again; again for each",
    )
    flagging = commands.add_parser(
        "flags",
        help="list the feature flags, or turn one on or off for an organisation",
        description="List the features behind a flag (app/flags.py), each with the share of "
        "organisations it is rolled out to, how many it is on for and those it was turned on "
        "or off for by hand. With an organisation's slug, how each stands for it; with a flag "
        "and on, off or rollout, turn it on or off for it whatever its rollout, or back to its "
        "rollout. Each change is a row of the organisation's audit log.",
    )
    flagging.add_argument("organization", nargs="?", help="the organisation's slug")
    flagging.add_argument("flag", nargs="?", help="the flag to turn on or off for it")
    flagging.add_argument(
        "state",
        nargs="?",
        choices=("on", "off", "rollout"),
        help="on or off whatever the rollout, or back to the rollout",
    )
    granting = commands.add_parser(
        "operators",
        help="list the operators, or make an account one or no longer one",
        description="List the users who may open the operator's area at /operator, where the "
        "people who run the deployment read the organisations, users, costs, jobs and "
        "services. With add or remove and an account's address, grant it or take it back. An "
        "operator opens the area only from an address OPERATOR_NETWORKS lists and, in "
        "production, with two-factor sign-in on.",
    )
    granting.add_argument(
        "action", nargs="?", choices=("add", "remove"), help="grant the area, or take it back"
    )
    granting.add_argument("email", nargs="?", metavar="ADDRESS", help="the account's address")
    receiving = commands.add_parser(
        "receive-mail",
        help="hand a message saved as a file to the projects it is addressed to",
        description="Take a message saved as it was sent (an .eml file) into the projects "
        "whose addresses at INBOUND_DOMAIN it is addressed to, as the provider's way in "
        "would, its From address taken as proven since you vouch for it: for a message the "
        "provider kept that the app did not take, and for trying a project's address by "
        "hand. Each project keeps what became of it, as for any message, and takes a file "
        "given again once.",
    )
    receiving.add_argument("path", type=Path, help="the message's file")
    receiving.add_argument(
        "--to",
        action="append",
        default=[],
        metavar="ADDRESS",
        help="a project's address to deliver it to, in place of its To, Cc and Delivered-To; "
        "again for each",
    )
    jobs = commands.add_parser(
        "jobs",
        help="show what the queue holds now, or where one document's indexing stands",
        description="Show the jobs of each kind by state (due, waiting for a retry or for "
        "midnight UTC, running, and failed in the last "
        f"{HISTORY.days} days), how long the oldest due one has waited for a worker, and each "
        "running job with what it is for, its attempt and its lease, which says whether its "
        "worker still holds it, and the workers seen in the last day, each with its release "
        "and whether it runs, stopped, or was lost. With --document, one document instead: "
        "where it stands, each stage with what its members read, its index jobs newest first, "
        "and what the newest one means. It changes nothing.",
    )
    jobs.add_argument(
        "--document",
        type=int,
        metavar="ID",
        help="the document's id, as the log, the alerts and the library's address name it",
    )
    commands.add_parser(
        "database",
        help="report on the database server: versions, timeouts, tables and vacuum",
        description="Say which Postgres and pgvector the database has, the timeouts of the "
        "role the app connects as, how the pooler is set up, the largest tables with their "
        "dead rows and when vacuum and analyse last ran, and the oldest open transaction. It "
        "changes nothing, and exits with 1 when something needs looking at: a pgvector before "
        "0.8, in production a role without timeouts, a table autovacuum is behind on, or a "
        "transaction open for over an hour.",
    )
    commands.add_parser(
        "encrypt-secrets",
        help="encrypt the database's secrets with the first key of ENCRYPTION_KEYS",
        description="Encrypt with the first key of ENCRYPTION_KEYS every secret the database "
        "keeps encrypted that is not yet: the authenticators' secrets from before they were "
        "encrypted, and those an earlier key of the list encrypted. Run it after putting a new "
        "key first, to rotate the key; it runs beside the app. It exits with 1 when a secret "
        "is encrypted with a key the list does not hold, which it leaves as it is.",
    )
    commands.add_parser(
        "models",
        help="ask every model the settings name, fallbacks included, what the app asks of it",
        description="Ask each model that AGENT_MODEL, INDEX_MODEL and their fallbacks name "
        "what the app asks of it, alone, without its fallback: a model for indexing two facts "
        "of a short delivery note and the number on a drawn scan, a model for answering one "
        "turn in which it must call one of the agent's tools. It prints what each did, how long "
        "it took and what it cost, records nothing as an organisation's usage, and exits with "
        "1 when a model failed or answered in a way the app cannot use. It costs a few "
        "thousand tokens per model; run it after changing a model, before a launch, and at "
        "intervals, since providers retire models on their own schedules.",
    )
    commands.add_parser(
        "build-sample",
        help="index the sample project's files with the model and write app/sample/index.json",
        description="Index the files of the sample project that the organisation an account "
        "signs up with starts with (backend/app/sample/files) as an upload is, with the models "
        "INDEX_MODEL and its fallback name, in an organisation made for the run and deleted "
        "after it, and write what the pipeline made of them, all but the vectors, into "
        "backend/app/sample/index.json, which every sign-up copies. It costs a few model calls, "
        "which are no organisation's usage. Run it in a checkout after changing the sample's "
        "files, or when a change to the parsing or the pipeline should show in the sample.",
    )
    args = parser.parse_args(argv)
    # Text whatever LOG_FORMAT says: the commands are run, and their lines read, by a person.
    configure_logging("text", "%(levelname)s %(name)s: %(message)s")
    # The schema check sets up a migration context, which announces itself; nothing to act on.
    logging.getLogger("alembic.runtime.migration").setLevel(logging.WARNING)

    settings = get_settings()
    # The bucket's keys, like the models', are copied from `.env` in development.
    export_env_file(settings)
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
                unbounded(connection)
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
        elif args.command == "after-restore":
            if args.restored_to > datetime.now(UTC):
                parser.error("TIME is in the future; give the point the database was restored to")
            storage = storage_from_settings(settings)
            check_schema()
            storage.check(writing=not args.dry_run)
            restored = after_restore(storage, args.restored_to, dry_run=args.dry_run)
            for line in describe_restore(restored, args.restored_to):
                print(line)
            if restored.dry_run and not restored.whole:
                sys.exit(1)
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
        elif args.command == "costs":
            if args.days < 1:
                parser.error("--days must be at least 1")
            check_schema()
            with SessionLocal() as session:
                for line in costs_report(session, args.days):
                    print(line)
        elif args.command == "jobs":
            check_schema()
            with SessionLocal() as session:
                if args.document is None:
                    lines = jobs_report(session)
                else:
                    lines = document_report(session, args.document)
                for line in lines:
                    print(line)
        elif args.command == "database":
            check_schema()
            with engine.connect() as connection:
                report = database_report(connection)
            for line in describe_database(report, settings):
                print(line)
            if concerns(report, settings):
                sys.exit(1)
        elif args.command == "encrypt-secrets":
            check_schema()
            done = encrypt_secrets()
            for line in describe_encrypted(done, keys(), settings.backup_days):
                print(line)
            if any(outcome.lost for outcome in done):
                sys.exit(1)
        elif args.command == "models":
            # Each request's outcome is in the lines below; the model layer's and the HTTP
            # clients' own lines would only say it again.
            logging.getLogger().setLevel(logging.ERROR)
            trials = try_models(settings)
            for line in describe_trials(trials, settings):
                print(line)
            if any(trial.failed for trial in trials):
                sys.exit(1)
        elif args.command == "build-sample":
            check_schema()
            # The run's documents are deleted as it ends: nothing is to draw their pages first.
            settings.prerender_pages = 0
            index = build_sample(settings)
            INDEX.write_text(written(index))
            documents = index["documents"]
            print(
                f"Indexed the sample's {_count(len(documents), 'document')} with "
                f"{', '.join(index['built']['models'])}: "
                f"{_count(sum(len(entry['passages']) for entry in documents), 'passage')} and "
                f"{_count(sum(len(entry['facts']) for entry in documents), 'fact')}, written to "
                f"{INDEX}. Read the summaries and facts before committing it."
            )
        elif args.command == "plan":
            check_schema()
            with SessionLocal() as session:
                lines = plan_report(
                    session,
                    args.organization,
                    args.plan,
                    seats=args.seats,
                    figures=args.figures,
                    unset=args.unset,
                )
                session.commit()
                for line in lines:
                    print(line)
        elif args.command == "flags":
            if (args.flag is None) != (args.state is None):
                parser.error("flags takes a flag with on, off or rollout, or neither")
            check_schema()
            with SessionLocal() as session:
                lines = flags_report(session, args.organization, args.flag, args.state)
                session.commit()
                for line in lines:
                    print(line)
        elif args.command == "receive-mail":
            check_schema()
            storage = storage_from_settings(settings)
            for line in receive_mail_report(storage, args.path.read_bytes(), args.to):
                print(line)
        elif args.command == "operators":
            if (args.action is None) != (args.email is None):
                parser.error("operators takes add or remove with an address, or neither")
            check_schema()
            with SessionLocal() as session:
                lines = operators_report(session, args.action, args.email or "")
                session.commit()
                for line in lines:
                    print(line)
    except (RuntimeError, StorageError) as error:
        sys.exit(str(error))


if __name__ == "__main__":
    main()
