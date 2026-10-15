"""What the app must do after its database is restored to an earlier point, before it serves
again, so that the database, the storage and the people using them agree once more
(`uv run python -m app.maintenance after-restore <time>`; deploying.md, "Backups").

A restored database is the deployment as it was at the restore point, and the rest moved on
without it: the storage lacks the files deleted since and holds those written since, the
sequences hand out again ids the storage has seen, the sessions signed out since are live
again, and the accounts purged since are back. `after_restore` sets each right, in order:

1. The files deleted since the restore point come back from a versioned bucket's earlier
   versions (`S3Storage.deletions`), since the database has their rows again. A directory
   keeps no versions; its backup is restored before this, to the same point or later. The
   files written since stay, as the files of documents the database does not know, which the
   worker's housekeeping removes like any other (`sweep_storage` in app/maintenance.py).
2. The ids of organisations, projects and documents continue past every id the storage holds.
   A document's files sit under its ids (app/storage.py), so a new document given a lost
   one's ids would be served the lost one's rendered pages, another organisation's among
   them. The upload refuses a place in the storage that holds files already
   (`upload_documents` in app/api/documents.py), so a restore that skips this step fails
   uploads rather than mixing them up.
3. Every session ends and every unused one-time link expires: one signed out or used since
   the restore point would work again otherwise, a session its member ended on a lost laptop
   among them. Everyone signs in again.
4. Every organisation's audit log records the restore and its point, since what was done
   after that moment is gone from it.
5. The accounts purged since are purged again at once (`reapply_erasures` in
   app/retention.py), rather than at the worker's first housekeeping round.

It then reports what it cannot set right by itself: documents whose original the storage
lacks, as a storage restored to an earlier point than the database leaves them, and the files
of documents the database does not know.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import text

from app.audit import audit_event
from app.db import SessionLocal
from app.retention import purge_due_accounts, reapply_erasures
from app.storage import ROOT_PREFIX, Deletion, S3Storage, Storage, is_rendered

AFTER_RESTORE_COMMAND = "uv run python -m app.maintenance after-restore <time>"
# The tables whose ids name places in the storage, and how a key names them.
TABLES = ("organizations", "projects", "documents")
_IDS = re.compile(r"orgs/(\d+)/(?:projects/(\d+)/(?:documents/(\d+)/)?)?")
# Inserts take a lock this one waits for and holds off, so no id is handed out between
# reading a sequence and moving it.
_HOLD_INSERTS = text("LOCK TABLE organizations, projects, documents IN SHARE ROW EXCLUSIVE MODE")
_LAST_ID = text(
    "SELECT pg_sequence_last_value(CAST(pg_get_serial_sequence(:table, 'id') AS regclass))"
)
_CONTINUE_AFTER = text("SELECT setval(CAST(pg_get_serial_sequence(:table, 'id') AS regclass), :id)")
_END_SESSIONS = text(
    "UPDATE sessions SET revoked_at = now() WHERE revoked_at IS NULL AND expires_at > now()"
)
_END_LINKS = text(
    "UPDATE auth_tokens SET expires_at = now() WHERE used_at IS NULL AND expires_at > now()"
)
_DOCUMENT_KEYS = text("SELECT id, storage_key FROM documents ORDER BY id")
# The organisations whose log does not record this restore yet, so that a second run, after
# the storage was restored again, adds no second row.
_UNRECORDED = text(
    """
    SELECT id FROM organizations
    WHERE NOT EXISTS (
        SELECT 1 FROM audit_events
        WHERE org_id = organizations.id
          AND action = 'database_restored'
          AND detail = CAST(:detail AS jsonb)
    )
    ORDER BY id
    """
)
# How many documents without their original the report names.
MISSING_SHOWN = 20


@dataclass
class Restored:
    """What `after_restore` did and found, for the command to say."""

    # The files brought back; None when the storage keeps no versions to bring them from.
    brought_back: list[Deletion] | None = None
    # The tables whose ids now continue after the given one, the highest the storage holds.
    advanced: dict[str, int] = field(default_factory=dict)
    sessions: int = 0
    links: int = 0
    organizations: int = 0
    # The accounts purged since the restore point, purged again.
    purged: list[int] = field(default_factory=list)
    # Documents whose original the storage does not have.
    missing: list[int] = field(default_factory=list)
    # The storage's folders of documents the database does not know, and their size.
    unknown: int = 0
    unknown_bytes: int = 0


@dataclass
class _Survey:
    """What a walk of the storage found: the highest id of each table in its keys, the
    originals, and the size of each document's folder."""

    highest: dict[str, int] = field(default_factory=lambda: dict.fromkeys(TABLES, 0))
    originals: set[str] = field(default_factory=set)
    folders: dict[str, int] = field(default_factory=dict)


def after_restore(storage: Storage, restored_to: datetime) -> Restored:
    """Set the storage, the ids, the sign-ins and the purges right after the database was
    restored to `restored_to`, and say what is left; run with the app stopped."""
    restored = Restored()
    # A bucket that would not say whether it keeps versions is asked for them all the same:
    # one without lists none.
    if isinstance(storage, S3Storage) and (storage.versions is None or storage.versions.status):
        restored.brought_back = storage.deletions(ROOT_PREFIX, restored_to)
        storage.undelete(restored.brought_back)
    survey = _survey(storage)
    with SessionLocal() as session:
        session.execute(_HOLD_INSERTS)
        for table in TABLES:
            seen = survey.highest[table]
            if seen > (session.scalar(_LAST_ID, {"table": table}) or 0):
                session.execute(_CONTINUE_AFTER, {"table": table, "id": seen})
                restored.advanced[table] = seen
        restored.sessions = session.execute(_END_SESSIONS).rowcount
        restored.links = session.execute(_END_LINKS).rowcount
        detail = {"restored_to": restored_to.isoformat()}
        for org_id in session.scalars(_UNRECORDED, {"detail": json.dumps(detail)}).all():
            audit_event(session, "database_restored", org_id=org_id, detail=detail)
            restored.organizations += 1
        known: set[str] = set()
        for document_id, key in session.execute(_DOCUMENT_KEYS):
            known.add(key.rpartition("/")[0] + "/")
            if key not in survey.originals:
                restored.missing.append(document_id)
        session.commit()
    unknown = survey.folders.keys() - known
    restored.unknown = len(unknown)
    restored.unknown_bytes = sum(survey.folders[folder] for folder in unknown)
    restored.purged = reapply_erasures(storage)
    purge_due_accounts(storage)
    return restored


def _survey(storage: Storage) -> _Survey:
    survey = _Survey()
    for entry in storage.list(ROOT_PREFIX):
        found = _IDS.match(entry.key)
        if found is None:
            continue
        for table, number in zip(TABLES, found.groups(), strict=True):
            if number:
                survey.highest[table] = max(survey.highest[table], int(number))
        if found[3]:
            folder = found.group()
            survey.folders[folder] = survey.folders.get(folder, 0) + entry.size
            if not is_rendered(entry.key):
                survey.originals.add(entry.key)
    return survey


def place_taken(document_id: int, prefix: str) -> str:
    """Why an upload was refused a place in the storage that holds files already, for the log."""
    return (
        f"Document {document_id} was not stored: {prefix} in the storage holds the files of an "
        "earlier document with the same ids that the database does not know. The database was "
        "restored to an earlier point, or DATABASE_URL and STORAGE_URL belong to different "
        f"deployments; after a restore, stop the app and run `{AFTER_RESTORE_COMMAND}`."
    )


def describe_restore(restored: Restored, restored_to: datetime) -> list[str]:
    """What `after_restore` did and found, a line each, for the command to print."""
    moment = f"{restored_to.astimezone(UTC):%Y-%m-%d %H:%M:%S} UTC"
    lines = []
    if restored.brought_back is None:
        lines.append(
            "The storage keeps no earlier versions of deleted files, so nothing deleted since "
            f"{moment} was brought back from it: a directory or a bucket without versioning "
            "comes back only from its own backup, to the same point as the database or later."
        )
    else:
        size = sum(deletion.size for deletion in restored.brought_back)
        lines.append(
            f"Brought back {_count(len(restored.brought_back), 'file')} ({_mb(size)} MB) "
            f"deleted from the bucket since {moment}."
        )
    if restored.advanced:
        after = ", ".join(f"{table} after {seen}" for table, seen in restored.advanced.items())
        lines.append(f"New ids continue past the storage's: {after}.")
    else:
        lines.append("New ids already continued past every id the storage holds.")
    lines.append(
        f"Ended {_count(restored.sessions, 'session')} and {_count(restored.links, 'unused link')}"
        ": everyone signs in again, and links mailed before now no longer work."
    )
    if restored.organizations:
        recorded = _count(restored.organizations, "organisation")
        lines.append(f"Recorded the restore in the audit log of {recorded}.")
    else:
        lines.append("Every organisation's audit log records this restore already.")
    if restored.purged:
        lines.append(
            f"Purged again {_count(len(restored.purged), 'account')} deleted since the restore "
            f"point: {', '.join(map(str, restored.purged))}."
        )
    if missing := restored.missing:
        shown = ", ".join(map(str, missing[:MISSING_SHOWN]))
        if len(missing) > MISSING_SHOWN:
            shown += f" and {len(missing) - MISSING_SHOWN} more"
        lines.append(
            f"{_count(len(missing), 'document')} without an original in the storage ({shown}): "
            "the storage is older than the database's restore point. Restore it to the same "
            "point or later and run this again; until then those documents can be searched and "
            "asked about, but not opened or indexed again."
        )
    if restored.unknown:
        lines.append(
            f"The storage holds the files of {_count(restored.unknown, 'document')} "
            f"({_mb(restored.unknown_bytes)} MB) that the database does not know, uploaded "
            "after the restore point; the worker's housekeeping removes them an hour after "
            "they were written, and `sweep-orphans` lists them."
        )
    lines.append("Start the app.")
    return lines


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}{'' if number == 1 else 's'}"


def _mb(size: int) -> float:
    return round(size / 1024 / 1024, 1)
