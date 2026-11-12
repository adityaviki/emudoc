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
3. Every session ends, and every unused one-time link, open invitation and share link
   expires: one signed out, used, revoked or accepted since the restore point would work again
   otherwise, a session its member ended on a lost laptop among them. Everyone signs in again,
   and an invitation or a share link still wanted is made again.
4. Every organisation's audit log records the restore and its point, since what was done
   after that moment is gone from it.
5. The accounts purged since are purged again at once (`reapply_erasures` in
   app/retention.py), rather than at the worker's first housekeeping round.

It then reports what it cannot set right by itself: documents whose original the storage
lacks, as a storage restored to an earlier point than the database leaves them, the files of
documents the database does not know, and the secrets encrypted with a key ENCRYPTION_KEYS no
longer holds (app/encrypted.py), which a key removed while a backup still held what it
encrypted leaves. It also says how far the restored database reaches, which is the recovery
point a restore achieved: the last transaction its recovery replayed, which the server knows
until it restarts, or else the newest act of a member its rows record.

A dry run (`after-restore --dry-run`) is the check of the restore drill (deploying.md, "The
drill"): it takes every step but changes nothing. It lists the files it would bring back and
counts them as the storage's, makes its changes to the database in a transaction it rolls back,
and moves no sequence, so it can check a restored copy of the database against the live
storage, which it only reads. A drill then needs no copy of the bucket and cannot harm it.
"""

import json
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import chain

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.audit import audit_event
from app.db import SessionLocal, unbounded
from app.encrypted import Standing, keys, lost_line, standing
from app.retention import purge_due_accounts, reapply_erasures
from app.storage import (
    ROOT_PREFIX,
    Deletion,
    S3Storage,
    Storage,
    StoredObject,
    files_prefix,
    is_rendered,
)

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
_END_INVITATIONS = text("UPDATE invitations SET expires_at = now() WHERE expires_at > now()")
# A share link revoked after the moment restored to would work again (app/share_links.py).
_END_SHARE_LINKS = text("UPDATE share_links SET expires_at = now() WHERE expires_at > now()")
# Every original the database names: each document's file, then its earlier versions'
# (app/versions.py), whose document is missing an original as much when one of theirs is gone.
_DOCUMENT_KEYS = text(
    "SELECT id, storage_key FROM documents "
    "UNION ALL SELECT document_id, storage_key FROM document_versions ORDER BY 1"
)
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
# How far a restored database reaches. The server knows the last transaction its recovery
# replayed until it restarts; after that, or for a database restored some other way, the
# newest act of a member that its rows record stands in: each sign-in, upload, question and
# download writes one, and after-restore's own rows have no member.
_REPLAYED = text("SELECT pg_last_xact_replay_timestamp()")
_NEWEST_ACT = text(
    """
    SELECT GREATEST(
        (SELECT max(occurred_at) FROM audit_events WHERE user_id IS NOT NULL),
        (SELECT max(occurred_at) FROM usage_events WHERE user_id IS NOT NULL),
        (SELECT max(last_seen_at) FROM sessions)
    )
    """
)
# What the database holds, for the operator to hold against the deployment as it was.
_HOLDS = text(
    """
    SELECT (SELECT count(*) FROM organizations) AS organisation,
           (SELECT count(*) FROM users) AS account,
           (SELECT count(*) FROM projects) AS project,
           (SELECT count(*) FROM documents) AS document,
           (SELECT count(*) FROM chats) AS chat
    """
)
# How many documents without their original the report names.
MISSING_SHOWN = 20


@dataclass
class Restored:
    """What `after_restore` did and found, for the command to say; after a dry run, what it
    would do."""

    dry_run: bool = False
    # How far the database reaches, and what says so; None for a database that records no act
    # of a member.
    reached: datetime | None = None
    reached_by: str = ""
    # How many organisations, accounts, projects, documents and chats it holds.
    holds: dict[str, int] = field(default_factory=dict)
    # The files brought back; None when the storage keeps no versions to bring them from.
    brought_back: list[Deletion] | None = None
    # The tables whose ids now continue after the given one, the highest the storage holds.
    advanced: dict[str, int] = field(default_factory=dict)
    sessions: int = 0
    links: int = 0
    invitations: int = 0
    share_links: int = 0
    organizations: int = 0
    # The accounts purged since the restore point, purged again.
    purged: list[int] = field(default_factory=list)
    # Documents whose original the storage does not have.
    missing: list[int] = field(default_factory=list)
    # The storage's folders of documents the database does not know, and their size.
    unknown: int = 0
    unknown_bytes: int = 0
    # How each encrypted column's values are kept; those of a key that is gone are lost.
    secrets: list[Standing] = field(default_factory=list)
    # How long the run took, in seconds: part of the time a restore takes.
    took: float = 0.0

    @property
    def whole(self) -> bool:
        """Whether the restore brings everything back usable: every original, every secret."""
        return not self.missing and not any(counts.lost for counts in self.secrets)


@dataclass
class _Survey:
    """What a walk of the storage found: the highest id of each table in its keys, the
    originals, and the size of each document's folder."""

    highest: dict[str, int] = field(default_factory=lambda: dict.fromkeys(TABLES, 0))
    originals: set[str] = field(default_factory=set)
    folders: dict[str, int] = field(default_factory=dict)


def after_restore(storage: Storage, restored_to: datetime, *, dry_run: bool = False) -> Restored:
    """Set the storage, the ids, the sign-ins and the purges right after the database was
    restored to `restored_to`, and say what is left; run with the app stopped. A dry run finds
    the same and changes neither the database nor the storage."""
    started = time.monotonic()
    restored = Restored(dry_run=dry_run)
    # A bucket that would not say whether it keeps versions is asked for them all the same:
    # one without lists none.
    if isinstance(storage, S3Storage) and (storage.versions is None or storage.versions.status):
        restored.brought_back = storage.deletions(ROOT_PREFIX, restored_to)
        if not dry_run:
            storage.undelete(restored.brought_back)
    # A dry run finds the files it would bring back where a run finds them, in the storage.
    pending = (restored.brought_back or []) if dry_run else []
    survey = _survey(storage, pending)
    with SessionLocal() as session:
        # Run with the app stopped, over every row and every key: no request's limits apply.
        unbounded(session)
        if not dry_run:
            session.execute(_HOLD_INSERTS)
        restored.reached, restored.reached_by = _reach(session)
        restored.holds = dict(session.execute(_HOLDS).mappings().one())
        restored.secrets = standing(session.connection(), keys())
        for table in TABLES:
            seen = survey.highest[table]
            if seen > (session.scalar(_LAST_ID, {"table": table}) or 0):
                # A sequence moves outside the transaction, so a dry run leaves it alone.
                if not dry_run:
                    session.execute(_CONTINUE_AFTER, {"table": table, "id": seen})
                restored.advanced[table] = seen
        restored.sessions = session.execute(_END_SESSIONS).rowcount
        restored.links = session.execute(_END_LINKS).rowcount
        restored.invitations = session.execute(_END_INVITATIONS).rowcount
        restored.share_links = session.execute(_END_SHARE_LINKS).rowcount
        detail = {"restored_to": restored_to.isoformat()}
        for org_id in session.scalars(_UNRECORDED, {"detail": json.dumps(detail)}).all():
            audit_event(session, "database_restored", org_id=org_id, detail=detail)
            restored.organizations += 1
        known: set[str] = set()
        missing: set[int] = set()
        for document_id, key in session.execute(_DOCUMENT_KEYS):
            known.add(files_prefix(key))
            if key not in survey.originals and document_id not in missing:
                missing.add(document_id)
                restored.missing.append(document_id)
        if dry_run:
            session.rollback()
        else:
            session.commit()
    unknown = survey.folders.keys() - known
    restored.unknown = len(unknown)
    restored.unknown_bytes = sum(survey.folders[folder] for folder in unknown)
    restored.purged = reapply_erasures(storage, dry_run=dry_run)
    if not dry_run:
        purge_due_accounts(storage)
    restored.took = time.monotonic() - started
    return restored


def _reach(session: Session) -> tuple[datetime | None, str]:
    if replayed := session.scalar(_REPLAYED):
        return replayed, "the last transaction its recovery replayed"
    return session.scalar(_NEWEST_ACT), "the newest act of a member it records"


def _survey(storage: Storage, pending: Iterable[Deletion]) -> _Survey:
    survey = _Survey()
    stored = (
        StoredObject(deletion.key, deletion.size, deletion.deleted_at) for deletion in pending
    )
    for entry in chain(storage.list(ROOT_PREFIX), stored):
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
    """What `after_restore` did and found, a line each, for the command to print; after a dry
    run, what it would do, and whether the restore is whole."""
    moment = _when(restored_to)
    would = restored.dry_run
    lines = []
    if would:
        lines.append(
            f"A dry run against the database restored to {moment}: nothing in the database or "
            "the storage was changed, and each line says what after-restore would do."
        )
    lines.append(_reach_line(restored, restored_to))
    holds = [_count(number, noun) for noun, number in restored.holds.items()]
    lines.append(f"It holds {', '.join(holds[:-1])} and {holds[-1]}.")
    if restored.brought_back is None:
        lines.append(
            "The storage keeps no earlier versions of deleted files, so nothing deleted since "
            f"{moment} {'can be' if would else 'was'} brought back from it: a directory or a "
            "bucket without versioning comes back only from its own backup, to the same point "
            "as the database or later."
        )
    else:
        size = sum(deletion.size for deletion in restored.brought_back)
        lines.append(
            f"{'Would bring' if would else 'Brought'} back "
            f"{_count(len(restored.brought_back), 'file')} ({_mb(size)} MB) deleted from the "
            f"bucket since {moment}."
        )
    if restored.advanced:
        after = ", ".join(f"{table} after {seen}" for table, seen in restored.advanced.items())
        lines.append(
            f"New ids {'would continue' if would else 'continue'} past the storage's: {after}."
        )
    else:
        lines.append("New ids already continued past every id the storage holds.")
    ended = (
        f"{_count(restored.sessions, 'session')}, {_count(restored.links, 'unused link')}, "
        f"{_count(restored.invitations, 'open invitation')} and "
        f"{_count(restored.share_links, 'share link')}"
    )
    if would:
        lines.append(f"Would end {ended}.")
    else:
        lines.append(
            f"Ended {ended}: everyone signs in again, links mailed before now no longer work, "
            "and the invitations still wanted must be sent again and the share links made again."
        )
    if restored.organizations:
        recorded = _count(restored.organizations, "organisation")
        lines.append(
            f"{'Would record' if would else 'Recorded'} the restore in the audit log of {recorded}."
        )
    else:
        lines.append("Every organisation's audit log records this restore already.")
    if restored.purged:
        lines.append(
            f"{'Would purge' if would else 'Purged'} again "
            f"{_count(len(restored.purged), 'account')} deleted since the restore point: "
            f"{', '.join(map(str, restored.purged))}."
        )
    if missing := restored.missing:
        shown = ", ".join(map(str, missing[:MISSING_SHOWN]))
        if len(missing) > MISSING_SHOWN:
            shown += f" and {len(missing) - MISSING_SHOWN} more"
        them, those = ("it", "that document") if len(missing) == 1 else ("them", "those documents")
        if would:
            lines.append(
                f"{_count(len(missing), 'document')} without an original in the storage, now "
                f"or among the earlier versions it keeps ({shown}): a restore to {moment} would "
                f"leave {them} unable to be opened or indexed again. A bucket keeps earlier "
                "versions only as long as its lifecycle rule says, and a directory comes back "
                "only from its own backup."
            )
        else:
            lines.append(
                f"{_count(len(missing), 'document')} without an original in the storage "
                f"({shown}): the storage is older than the database's restore point. Restore "
                "it to the same point or later and run this again; until then "
                f"{those} can be searched and asked about, but not opened or indexed again."
            )
    for counts in restored.secrets:
        if counts.lost:
            lines.append(
                lost_line(counts)
                + (
                    " A key stays in ENCRYPTION_KEYS for as long as a backup holds what it "
                    'encrypted (deploying.md, "The encryption keys").'
                    if would
                    else ""
                )
            )
    if restored.unknown:
        held = (
            f"The storage holds the files of {_count(restored.unknown, 'document')} "
            f"({_mb(restored.unknown_bytes)} MB) that the database does not know, uploaded "
            "after the restore point"
        )
        if would:
            lines.append(f"{held}, which a restore to it would leave to the worker's housekeeping.")
        else:
            lines.append(
                f"{held}; the worker's housekeeping removes them an hour after they were "
                "written, and `sweep-orphans` lists them."
            )
    lines.append(f"Took {_span(restored.took)}.")
    if not would:
        lines.append("Start the app.")
    elif missing:
        lines.append(
            "The restore is not whole: the database and the storage do not come back to the same "
            "point."
        )
    elif not restored.whole:
        lines.append(
            "The restore is not whole: ENCRYPTION_KEYS no longer holds a key the database's "
            "backups need."
        )
    else:
        lines.append(
            "The restore is whole: the storage holds every document's original as it was at "
            f"{moment}."
        )
    return lines


def _reach_line(restored: Restored, restored_to: datetime) -> str:
    if restored.reached is None:
        return "The database records no act of a member, so how far it reaches cannot be told."
    gap = (restored_to - restored.reached).total_seconds()
    where = (
        f"{_span(gap)} before the restore point"
        if gap >= 0
        else f"{_span(-gap)} after the restore point given: it was restored to a later moment"
    )
    return f"The database reaches {_when(restored.reached)} ({restored.reached_by}), {where}."


def _when(moment: datetime) -> str:
    return f"{moment.astimezone(UTC):%Y-%m-%d %H:%M:%S} UTC"


def _span(seconds: float) -> str:
    """A length of time as an operator reads it, to the second under an hour."""
    seconds = round(seconds)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours >= 48:
        return f"{hours // 24} days"
    if hours:
        return f"{hours} h {minutes} min" if minutes else f"{hours} h"
    if minutes:
        return f"{minutes} min {seconds} s" if seconds else f"{minutes} min"
    return f"{seconds} s"


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}{'' if number == 1 else 's'}"


def _mb(size: int) -> float:
    return round(size / 1024 / 1024, 1)
