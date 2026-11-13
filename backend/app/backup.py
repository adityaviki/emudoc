"""Backups a team takes and restores by itself (`python -m app.maintenance backup <dir>` and
`restore <dir>`; deploying.md, "Backups"), run through the compose file's `backup` service.

A backup is a folder of its own in the directory it is given, named by the moment it was taken
(`emudoc-2026-11-13T033000Z`):

- `database.dump`, the database as `pg_dump` saw it, in its custom format;
- `files/`, the storage's files under their keys, when the storage is a directory: the
  originals, the views converted from them and the records of the accounts purged (app/
  retention.py). The rendered pages and thumbnails are left out, since a page asked for is
  drawn again from its original, and so are the projects' exports, which are made again. A
  file unchanged since the previous backup in the directory is a hard link to that backup's
  copy, so a nightly backup takes the space of what changed;
- `keys.txt` instead, when the storage is a bucket: the keys the backup's database may name,
  which the bucket keeps by its own versions (deploying.md, "The files"), for the restore to
  check;
- `backup.json`, what the restore needs to know: the version of Emudoc that took it, the
  database's schema revision and the moment.

The database is dumped first and the files are copied after it, so the files hold at least
what the database names: a file written meanwhile belongs to a document the database does not
know, which the worker's housekeeping removes after a restore. A backup is written under a
hidden name and renamed once it is whole, so a backup that stopped halfway is never taken for
one; once one is whole, those older than BACKUP_DAYS are removed, so the backups a team keeps
match what the deployment tells its members about how long a deleted document lingers.

A restore goes into an empty database alone: the database a backup comes from is the whole
deployment at that moment, and one that already holds a workspace would be two. It puts the
files back first, then restores the database in one transaction, so a restore that fails
leaves the database empty and can be run again. It upgrades a database from an earlier version
to this one's schema, refuses one from a later version, and then does what `after-restore`
does after any restore to an earlier point (app/restore.py), from the backup's moment.

`pg_dump` and `pg_restore` are PostgreSQL 17's in the image, which dump and restore a server of
17 or earlier. A database reached through a pooler in transaction mode is dumped at
DATABASE_LISTEN_URL, since `pg_dump` sets its session up before its transaction.
"""

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath

from alembic import command
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url

from app.config import Settings
from app.db import alembic_config, engine
from app.restore import Restored, after_restore
from app.retention import ERASURES
from app.storage import ROOT_PREFIX, LocalStorage, Storage, is_export, is_rendered
from app.version import VERSION

FORMAT = 1
MANIFEST = "backup.json"
DUMP = "database.dump"
FILES = "files"
KEYS = "keys.txt"
# Where the storage keeps what a backup holds: every organisation's files, and the records of
# the accounts purged, which a restore needs to purge them again.
PREFIXES = (ROOT_PREFIX, ERASURES)
_NAME = re.compile(r"emudoc-(\d{4}-\d\d-\d\dT\d{6}Z)")
_PARTIAL = re.compile(r"\.emudoc-(\d{4}-\d\d-\d\dT\d{6}Z)\.partial")
_STAMP = "%Y-%m-%dT%H%M%SZ"
# Where the image runs as, which owns what a backup writes.
IMAGE_USER = 10001
# How many of the files a bucket lacks the restore names.
ABSENT_SHOWN = 10


@dataclass
class Taken:
    """What `take_backup` wrote, for the command to say."""

    folder: Path
    taken_at: datetime
    revision: str
    database_bytes: int = 0
    # Kept in the backup when the storage is a directory, listed when it is a bucket.
    in_bucket: bool = False
    files: int = 0
    file_bytes: int = 0
    # Of those copied, the files unchanged since the previous backup, linked to its copy.
    linked: int = 0
    # The earlier backups removed for being older than BACKUP_DAYS.
    removed: list[str] = field(default_factory=list)
    took: float = 0.0


@dataclass(frozen=True)
class Manifest:
    """A backup's `backup.json`: who took it, of which schema, and when."""

    folder: Path
    version: str
    revision: str
    taken_at: datetime
    in_bucket: bool


@dataclass
class Brought:
    """What `restore_backup` did, before what `after_restore` did and found."""

    manifest: Manifest
    database_bytes: int = 0
    # The files put back into the storage, and those it held already.
    put: int = 0
    put_bytes: int = 0
    there: int = 0
    # For a backup whose files a bucket keeps: how many it lists, and those the bucket lacks.
    listed: int = 0
    absent: list[str] = field(default_factory=list)
    # The schema revision the database was upgraded to, when the backup's was earlier.
    upgraded_to: str | None = None
    restored: Restored = field(default_factory=Restored)
    took: float = 0.0


def take_backup(
    settings: Settings, storage: Storage, into: Path, now: datetime | None = None
) -> Taken:
    """Write a backup of the database and the files into a new folder in `into`, then remove
    the backups there older than BACKUP_DAYS; the app keeps running meanwhile."""
    started = time.monotonic()
    if settings.backup_days == 0:
        raise RuntimeError(
            "BACKUP_DAYS is 0, which tells the members that no backup keeps their deleted "
            "documents. Set it to the days the backups are kept, then run this again."
        )
    url, environ = _libpq(settings)
    pg_dump = _tool("pg_dump")
    _writable(into)
    # Whichever revision the database is at: the restore checks that its version knows it, so a
    # backup taken by the next version's image before its migrations restores with this one.
    with engine.connect() as connection:
        revision = MigrationContext.configure(connection).get_current_revision()
    if revision is None:
        raise RuntimeError(
            "The database has no schema yet, so there is nothing to back up: DATABASE_URL names "
            "a database Emudoc has never run on."
        )
    taken_at = now or datetime.now(UTC)
    name = f"emudoc-{taken_at:{_STAMP}}"
    partial = into / f".{name}.partial"
    try:
        # Readable by this user alone: it holds every document.
        partial.mkdir(mode=0o700)
    except FileExistsError:
        raise RuntimeError(
            f"A backup named {name} is being written into {into} already; run this again in a "
            "second."
        ) from None
    taken = Taken(into / name, taken_at, revision)
    try:
        dump = partial / DUMP
        _run(
            [pg_dump, "--format=custom", "--no-password", f"--file={dump}", f"--dbname={url}"],
            environ,
        )
        taken.database_bytes = dump.stat().st_size
        if isinstance(storage, LocalStorage):
            _copy_files(storage, partial / FILES, _previous(into, name), taken)
        else:
            _list_keys(storage, partial / KEYS, taken)
        manifest = {
            "format": FORMAT,
            "emudoc": VERSION,
            "revision": taken.revision,
            "taken_at": taken_at.isoformat(),
            "database": {"file": DUMP, "bytes": taken.database_bytes},
            "files": {
                **({"kept": "bucket", "list": KEYS} if taken.in_bucket else {"kept": "backup"}),
                "count": taken.files,
                "bytes": taken.file_bytes,
            },
        }
        (partial / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n")
        partial.rename(taken.folder)
    except OSError as error:
        shutil.rmtree(partial, ignore_errors=True)
        raise RuntimeError(
            f"The backup could not be written into {into} ({error.strerror}); nothing of it was "
            "kept. Check the space and the permissions of that directory."
        ) from error
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    taken.removed = _prune(into, settings.backup_days, taken_at)
    taken.took = time.monotonic() - started
    return taken


def read_manifest(folder: Path) -> Manifest:
    """The backup in `folder`, or a refusal that says what `folder` is instead."""
    try:
        written = json.loads((folder / MANIFEST).read_text())
    except FileNotFoundError:
        held = sorted((entry.name for entry in _backups(folder)), reverse=True)
        if held:
            shown = ", ".join(held[:3])
            raise RuntimeError(
                f"{folder} holds {len(held)} backup{'' if len(held) == 1 else 's'}, not one: "
                f"name the one to restore, the newest first: {shown}."
            ) from None
        raise RuntimeError(
            f"{folder} holds no backup: a backup is a folder with {MANIFEST} in it, which "
            "`backup` writes."
        ) from None
    except (OSError, ValueError) as error:
        raise RuntimeError(f"{folder / MANIFEST} cannot be read: {error}.") from error
    if written.get("format") != FORMAT:
        raise RuntimeError(
            f"The backup in {folder} was written by Emudoc {written.get('emudoc', 'unknown')} "
            "in a form this version does not read; restore it with that version."
        )
    return Manifest(
        folder,
        written["emudoc"],
        written["revision"],
        datetime.fromisoformat(written["taken_at"]),
        written["files"]["kept"] == "bucket",
    )


def restore_backup(settings: Settings, storage: Storage, folder: Path) -> Brought:
    """Restore the backup in `folder` into an empty database and the storage, then set the
    rest right as after any restore; run with the app stopped."""
    started = time.monotonic()
    manifest = read_manifest(folder)
    script = ScriptDirectory.from_config(alembic_config())
    try:
        script.get_revision(manifest.revision)
    except CommandError:
        raise RuntimeError(
            f"The backup was taken by Emudoc {manifest.version}, whose database schema "
            f"(revision {manifest.revision}) this version does not know: restore it with "
            f"Emudoc {manifest.version} or later."
        ) from None
    url, environ = _libpq(settings)
    pg_restore = _tool("pg_restore")
    if _holds_tables():
        raise RuntimeError(
            "The database holds tables already, and a backup is restored into an empty one "
            'alone. Empty it first (deploying.md, "Restoring a backup"): with the compose '
            "file, `docker compose stop api worker`, then `docker compose exec postgres sh -c "
            "'dropdb -U emudoc --force emudoc && createdb -U emudoc emudoc'`."
        )
    storage.check()
    brought = Brought(manifest)
    if not manifest.in_bucket:
        _put_back(storage, folder / FILES, brought)
    dump = folder / DUMP
    brought.database_bytes = dump.stat().st_size
    # One transaction: a restore that fails leaves the database empty, to be run again.
    _run(
        [
            pg_restore,
            "--single-transaction",
            "--no-owner",
            "--no-privileges",
            "--no-password",
            f"--dbname={url}",
            str(dump),
        ],
        environ,
    )
    head = script.get_current_head()
    if manifest.revision != head:
        _upgrade()
        brought.upgraded_to = head
    brought.restored = after_restore(storage, manifest.taken_at)
    if manifest.in_bucket:
        # The files deleted since come back from the bucket's versions in `after_restore`.
        _check_keys(storage, folder / KEYS, brought)
    brought.took = time.monotonic() - started
    return brought


def describe_backup(taken: Taken, into: Path, backup_days: int | None) -> list[str]:
    """What `take_backup` wrote, a line each, for the command to print."""
    lines = [
        f"Wrote the backup {taken.folder}: the database as it was at {_when(taken.taken_at)} "
        f"(Emudoc {VERSION}, schema revision {taken.revision}), {_mb(taken.database_bytes)} MB "
        "dumped."
    ]
    files = f"{_count(taken.files, 'file')} ({_mb(taken.file_bytes)} MB)"
    if taken.in_bucket:
        lines.append(
            f"The files stay in the bucket, which keeps them by its own versions: {files} "
            f"listed in {KEYS}, which the restore checks the bucket against."
        )
    elif taken.linked:
        lines.append(
            f"The files: {files}, {_count(taken.linked, 'file')} unchanged since the previous "
            "backup and linked to its copy, so they take no more space. Rendered pages are "
            "left out: they are drawn again."
        )
    else:
        lines.append(f"The files: {files}. Rendered pages are left out: they are drawn again.")
    if backup_days is None:
        lines.append(f"BACKUP_DAYS is not set, so every earlier backup in {into} is kept.")
    elif taken.removed:
        lines.append(
            f"Removed {_count(len(taken.removed), 'backup')} older than BACKUP_DAYS "
            f"({backup_days} days): {', '.join(taken.removed)}."
        )
    lines.append(
        f"Took {_span(taken.took)}. Keep a copy off this machine, and .env apart from it: "
        'without its ENCRYPTION_KEYS a backup\'s secrets cannot be read (deploying.md, "Backups").'
    )
    return lines


def describe_brought(brought: Brought) -> list[str]:
    """What `restore_backup` did, a line each, before `describe_restore`'s."""
    manifest = brought.manifest
    lines = [
        f"Restored the backup taken at {_when(manifest.taken_at)} by Emudoc {manifest.version}: "
        f"the database ({_mb(brought.database_bytes)} MB dumped)"
        + (
            f", upgraded from schema revision {manifest.revision} to {brought.upgraded_to}."
            if brought.upgraded_to
            else "."
        )
    ]
    if manifest.in_bucket:
        if brought.absent:
            shown = ", ".join(brought.absent[:ABSENT_SHOWN])
            if len(brought.absent) > ABSENT_SHOWN:
                shown += f" and {len(brought.absent) - ABSENT_SHOWN} more"
            lines.append(
                f"The files are the bucket's, which lacks {len(brought.absent)} of the "
                f"{_count(brought.listed, 'file')} the backup lists: {shown}. A bucket keeps "
                "a deleted file only as long as its versions are kept."
            )
        else:
            lines.append(
                f"The files are the bucket's, which holds every one of the "
                f"{_count(brought.listed, 'file')} the backup lists."
            )
    else:
        lines.append(
            f"Put back {_count(brought.put, 'file')} ({_mb(brought.put_bytes)} MB) into the "
            f"storage; it held {brought.there} of the backup's already."
        )
    return lines


def _libpq(settings: Settings) -> tuple[str, dict[str, str]]:
    """The database's address for `pg_dump` and `pg_restore`, and their environment, which
    carries the password: an argument would show it to anyone who lists the processes."""
    address = settings.listen_url
    if address is None:
        raise RuntimeError(
            "DATABASE_URL goes through a pooler in transaction mode, which pg_dump and "
            "pg_restore cannot work through. Set DATABASE_LISTEN_URL to the database itself, "
            "or to the pooler's port in session mode."
        )
    url = make_url(address)
    password = {"PGPASSWORD": str(url.password)} if url.password else {}
    environ = {**os.environ, "PGAPPNAME": "emudoc-backup", **password}
    plain = URL.create(
        "postgresql",
        username=url.username,
        host=url.host,
        port=url.port,
        database=url.database,
        query=url.query,
    )
    return plain.render_as_string(hide_password=False), environ


def _tool(name: str) -> str:
    found = shutil.which(name)
    if found is None:
        raise RuntimeError(
            f"{name} is not installed here. Run this from the image, as the compose file's "
            '`backup` service does (deploying.md, "Backups"), or install PostgreSQL 17\'s '
            "client tools."
        )
    return found


def _run(argv: list[str], environ: dict[str, str]) -> None:
    done = subprocess.run(argv, env=environ, capture_output=True, text=True, check=False)
    if done.returncode:
        said = done.stderr.strip() or f"it exited with {done.returncode}"
        raise RuntimeError(f"{Path(argv[0]).name} failed: {said}")


def _upgrade() -> None:
    command.upgrade(alembic_config(), "head")


def _holds_tables() -> bool:
    with engine.connect() as connection:
        return bool(
            connection.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_tables "
                    "WHERE schemaname = current_schema())"
                )
            )
        )


def _writable(into: Path) -> None:
    try:
        into.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise RuntimeError(f"{into} cannot be made ({error.strerror}).") from error
    if not os.access(into, os.W_OK | os.X_OK):
        raise RuntimeError(
            f"{into} cannot be written by this process (user {os.getuid()}). Give it to that "
            f"user, as `chown {os.getuid()} <the directory>` on the host for one mounted from "
            "there, or name another."
        )


def _backed_up(key: str) -> bool:
    # A name starting with a dot is a file being written (LocalStorage.put).
    return not (is_rendered(key) or is_export(key) or PurePosixPath(key).name.startswith("."))


def _copy_files(storage: LocalStorage, target: Path, previous: Path | None, taken: Taken) -> None:
    for prefix in PREFIXES:
        for entry in storage.list(prefix):
            if not _backed_up(entry.key):
                continue
            copy = target / entry.key
            copy.parent.mkdir(parents=True, exist_ok=True)
            modified = entry.modified_at.timestamp()
            earlier = previous / FILES / entry.key if previous else None
            if earlier is not None and _same(earlier, entry.size, modified):
                try:
                    os.link(earlier, copy)
                except OSError:
                    # Another file system, or one without links: a copy then.
                    pass
                else:
                    taken.linked += 1
                    taken.files += 1
                    taken.file_bytes += entry.size
                    continue
            try:
                with storage.localize(entry.key) as path:
                    shutil.copyfile(path, copy)
            except FileNotFoundError:
                # Deleted since the listing: the database dumped before may still name it,
                # which the restore reports as a document without its original.
                continue
            os.utime(copy, (modified, modified))
            taken.files += 1
            taken.file_bytes += entry.size


def _same(earlier: Path, size: int, modified: float) -> bool:
    try:
        stat = earlier.stat()
    except FileNotFoundError:
        return False
    return stat.st_size == size and int(stat.st_mtime) == int(modified)


def _list_keys(storage: Storage, target: Path, taken: Taken) -> None:
    taken.in_bucket = True
    with target.open("w") as listing:
        for prefix in PREFIXES:
            for entry in storage.list(prefix):
                if _backed_up(entry.key):
                    listing.write(f"{entry.key}\t{entry.size}\n")
                    taken.files += 1
                    taken.file_bytes += entry.size


def _put_back(storage: Storage, source: Path, brought: Brought) -> None:
    if not source.is_dir():
        return
    held = {entry.key: entry.size for prefix in PREFIXES for entry in storage.list(prefix)}
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        key = path.relative_to(source).as_posix()
        size = path.stat().st_size
        if held.get(key) == size:
            brought.there += 1
            continue
        storage.put(key, path)
        brought.put += 1
        brought.put_bytes += size


def _check_keys(storage: Storage, listing: Path, brought: Brought) -> None:
    listed = [line.split("\t", 1)[0] for line in listing.read_text().splitlines() if line]
    held = {entry.key for prefix in PREFIXES for entry in storage.list(prefix)}
    brought.listed = len(listed)
    brought.absent = [key for key in listed if key not in held]


def _backups(into: Path) -> list[Path]:
    if not into.is_dir():
        return []
    return [
        entry
        for entry in into.iterdir()
        if entry.is_dir() and _NAME.fullmatch(entry.name) and (entry / MANIFEST).is_file()
    ]


def _previous(into: Path, name: str) -> Path | None:
    earlier = sorted(entry for entry in _backups(into) if entry.name < name)
    return earlier[-1] if earlier else None


def _prune(into: Path, backup_days: int | None, taken_at: datetime) -> list[str]:
    """Remove the backups older than BACKUP_DAYS and what earlier runs left half written; the
    newest backup is the one just taken, so a failing backup never removes the last good one."""
    removed = []
    for entry in sorted(into.iterdir()):
        partial = _PARTIAL.fullmatch(entry.name)
        if partial and _moment(partial[1]) < taken_at:
            shutil.rmtree(entry, ignore_errors=True)
            continue
        whole = _NAME.fullmatch(entry.name)
        if (
            backup_days is not None
            and whole
            and (entry / MANIFEST).is_file()
            and _moment(whole[1]) < taken_at - timedelta(days=backup_days)
        ):
            shutil.rmtree(entry)
            removed.append(entry.name)
    return removed


def _moment(stamp: str) -> datetime:
    return datetime.strptime(stamp, _STAMP).replace(tzinfo=UTC)


def _when(moment: datetime) -> str:
    return f"{moment.astimezone(UTC):%Y-%m-%d %H:%M:%S} UTC"


def _span(seconds: float) -> str:
    seconds = round(seconds)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} h {minutes} min"
    if minutes:
        return f"{minutes} min {seconds} s"
    return f"{seconds} s"


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}{'' if number == 1 else 's'}"


def _mb(size: int) -> float:
    return round(size / 1024 / 1024, 1)
