"""What the app needs of its Postgres server, checked as every process starts and reported on
demand.

A managed database, or a pooler in front of one, brings choices the settings do not show: which
pgvector the server has, which timeouts the app's role carries, how vacuum keeps up with the
tables that churn (deploying.md, "The database"). `check_database` runs in `prepare`
(app/startup.py): it refuses in production a pgvector that makes searches lose passages, says
what the server is, and warns in production when the role has no timeouts. `database_report`
is the `database` maintenance command's longer look, which adds how the largest tables are kept
and how long the oldest open transaction has been holding vacuum back; the worker's watch
(app/watch.py) and the metrics (app/metrics.py) read the same of every table, and `archive`,
how the server's archiving of its write-ahead log stands, which the backups rest on.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import Connection, text

from app.config import Settings
from app.db import engine

log = logging.getLogger(__name__)

# Iterative index scans arrived in pgvector 0.8. Without them a search by meaning takes the
# ef_search (40) passages nearest the question from the whole table, every project's, and the
# project's filter drops the others' afterwards, so in a database of many projects a search
# finds few of its own passages or none (app/search.py).
ITERATIVE_SCANS = (0, 8)
# The role's limits deploying.md sets, with the values it suggests, in the order they are named.
TIMEOUTS = {"statement_timeout": "30s", "idle_in_transaction_session_timeout": "5min"}
# A table with this many dead rows, and more than this share of its rows dead, is one
# autovacuum is behind on: its scans read the dead rows too, and its indexes keep entries for
# them, the vector index's among them.
DEAD_ROWS = 10_000
DEAD_SHARE = 0.2
# A transaction open for longer keeps vacuum from removing any row deleted or replaced since it
# began, in every table; the app's own work holds none that long (`unbounded` in app/db.py
# names the work that may run longer, which is the operator's to schedule).
LONG_TRANSACTION = timedelta(hours=1)
TABLES_SHOWN = 8
# The recovery point deploying.md promises ("Recovery targets"): a finished segment of the
# write-ahead log that has waited longer than this to be archived is a backup falling behind.
ARCHIVE_LAG = timedelta(minutes=5)

_SERVER = text(
    "SELECT split_part(current_setting('server_version'), ' ', 1) AS version, "
    "current_user AS role, "
    "(SELECT extversion FROM pg_extension WHERE extname = 'vector') AS vector, "
    "(SELECT default_version FROM pg_available_extensions WHERE name = 'vector') AS offered, "
    "current_setting('statement_timeout') AS statement_timeout, "
    "current_setting('idle_in_transaction_session_timeout') "
    "AS idle_in_transaction_session_timeout, "
    "ARRAY(SELECT collname FROM pg_collation WHERE collnamespace = current_schema()::regnamespace "
    "AND collversion <> pg_collation_actual_version(oid) ORDER BY collname) AS stale_collations"
)
# The index each of the schema's own collations sorts, which a new version of ICU may leave out
# of order (`natural_order` in app/models.py).
COLLATED_INDEXES = {"natural_order": "ix_documents_listing"}
_TABLES = text(
    "SELECT relname AS name, n_live_tup AS rows, n_dead_tup AS dead, "
    "pg_table_size(relid) AS size, pg_indexes_size(relid) AS index_size, "
    "greatest(last_vacuum, last_autovacuum) AS vacuumed, "
    "greatest(last_analyze, last_autoanalyze) AS analyzed "
    "FROM pg_stat_user_tables WHERE schemaname = current_schema() "
    "ORDER BY pg_total_relation_size(relid) DESC, relname LIMIT :limit"
)
# The archiver's own figures, which any role may read, and the segment being written now, which a
# server in recovery has none of; the archive's mode says whether there is an archiver at all.
_ARCHIVE = text(
    "SELECT current_setting('archive_mode') AS mode, a.archived_count AS archived, "
    "a.failed_count AS failed, a.last_archived_wal, a.last_archived_time AS archived_at, "
    "a.last_failed_time AS failed_at, CASE WHEN pg_is_in_recovery() THEN NULL "
    "ELSE pg_walfile_name(pg_current_wal_lsn()) END AS current_wal, "
    "(SELECT setting::bigint FROM pg_settings WHERE name = 'wal_segment_size') AS segment_size, "
    "now() AS taken_at FROM pg_stat_archiver a"
)
# A segment's name: its timeline, then the log and the segment within it, eight hex digits each.
# A timeline's history file (00000002.history) is archived too, and names no segment.
_SEGMENT = re.compile(r"^[0-9A-F]{8}([0-9A-F]{8})([0-9A-F]{8})")
# The sessions of other roles show no transaction start to a role without pg_read_all_stats,
# which leaves them out; the app's own are what this is for.
_OLDEST = text(
    "SELECT now() - xact_start AS age, state FROM pg_stat_activity "
    "WHERE datname = current_database() AND backend_type = 'client backend' "
    "AND pid <> pg_backend_pid() AND xact_start IS NOT NULL "
    "ORDER BY xact_start LIMIT 1"
)


@dataclass
class Server:
    """The Postgres server and the role the app connects as."""

    version: str
    role: str
    # pgvector as the database has it, and the version the server would update it to.
    vector: str | None
    offered: str | None
    # Each of `TIMEOUTS` as the role's session starts with it: "0" is none.
    timeouts: dict[str, str]
    # The schema's collations made with another version of ICU than the server has now.
    stale_collations: list[str] = field(default_factory=list)

    @property
    def iterative_scans(self) -> bool:
        return self.vector is not None and _version(self.vector) >= ITERATIVE_SCANS


@dataclass
class TableState:
    name: str
    rows: int
    dead: int
    size: int
    index_size: int
    vacuumed: datetime | None
    analyzed: datetime | None

    @property
    def behind(self) -> bool:
        """Whether autovacuum is falling behind on this table (`DEAD_ROWS`, `DEAD_SHARE`)."""
        return self.dead >= DEAD_ROWS and self.dead > DEAD_SHARE * (self.rows + self.dead)


@dataclass
class Archive:
    """How the server's archiving of its write-ahead log stands, from pg_stat_archiver: the
    segments archived and the attempts that failed since its statistics were last reset, when
    the last of each happened, and how many finished segments wait for the archiver.

    The archiver tries a segment until it goes and takes the next only then, so a failure newer
    than the last success is a segment stuck now, and every segment behind it waits."""

    archived: int
    failed: int
    archived_at: datetime | None
    failed_at: datetime | None
    # None when it cannot be told: nothing archived yet, or a history file archived last.
    pending: int | None
    taken_at: datetime

    @property
    def failing(self) -> bool:
        return self.failed_at is not None and (
            self.archived_at is None or self.failed_at > self.archived_at
        )

    @property
    def behind(self) -> timedelta | None:
        """How long the archive has stood still while a finished segment waited, once that is
        past ARCHIVE_LAG; None while it keeps up. A quiet database finishes no segment, so an old
        last archive alone is no lag."""
        if not self.pending or self.archived_at is None:
            return None
        waited = self.taken_at - self.archived_at
        return waited if waited > ARCHIVE_LAG else None


@dataclass
class Report:
    server: Server
    tables: list[TableState]
    # The oldest transaction open in the database but the report's own, and what it is doing.
    oldest: timedelta | None
    oldest_state: str | None
    taken_at: datetime


def server(connection: Connection) -> Server:
    row = connection.execute(_SERVER).mappings().one()
    return Server(
        version=row["version"],
        role=row["role"],
        vector=row["vector"],
        offered=row["offered"],
        timeouts={name: row[name] for name in TIMEOUTS},
        stale_collations=row["stale_collations"],
    )


def archive(connection: Connection) -> Archive | None:
    """How the server's archiving stands, or None when it archives nothing: archive_mode off, as
    a development database has it, or a server in recovery, whose primary archives."""
    row = connection.execute(_ARCHIVE).mappings().one()
    if row["mode"] == "off" or row["current_wal"] is None:
        return None
    pending = None
    last = _SEGMENT.match(row["last_archived_wal"] or "")
    current = _SEGMENT.match(row["current_wal"])
    if last and current:
        # Numbered across the logs, which hold 4 GB of segments each; the timeline does not
        # count, since a new one goes on from the segment the old one was at.
        per_log = 0x1_0000_0000 // row["segment_size"]
        first, now = (
            int(log, 16) * per_log + int(segment, 16)
            for log, segment in (last.groups(), current.groups())
        )
        # The segment being written is not finished; the one before it is the last that can be.
        pending = max(now - first - 1, 0)
    return Archive(
        archived=row["archived"],
        failed=row["failed"],
        archived_at=row["archived_at"],
        failed_at=row["failed_at"],
        pending=pending,
        taken_at=row["taken_at"],
    )


def vector_problem(found: Server) -> str | None:
    """Why the database's pgvector makes searches lose passages, with what to run; None for
    0.8 or later."""
    if found.iterative_scans:
        return None
    if found.offered and _version(found.offered) >= ITERATIVE_SCANS:
        step = (
            f"the server has {found.offered}: run `ALTER EXTENSION vector UPDATE;` in the "
            "database as its owner"
        )
    else:
        step = (
            "the server has nothing newer: upgrade pgvector on it (a managed database brings "
            "a newer one with a newer minor version of Postgres)"
        )
    has = f"pgvector {found.vector}" if found.vector else "no pgvector"
    return (
        f"The database has {has}, and a search by meaning "
        f"needs {'.'.join(map(str, ITERATIVE_SCANS))} or later: before it, the vector index "
        "hands back the passages nearest the question from every project, and the project's "
        f"filter drops the others' afterwards, so a project finds few of its own or none; {step}."
    )


def collation_problem(name: str) -> str:
    """What to do about a collation whose ICU moved under it: until its index is rebuilt, the
    index may hold names out of the order the collation now compares them in, and a page of
    the library that starts after a name may skip or repeat files."""
    index = COLLATED_INDEXES.get(name, "every index that sorts by it")
    return (
        f"The collation {name} was made with another version of ICU than the database server "
        f"has now, so {index} may hold names out of order; as the database's owner, run "
        f"`REINDEX INDEX CONCURRENTLY {index};` and then `ALTER COLLATION {name} REFRESH "
        'VERSION;` (deploying.md, "The database").'
    )


def timeouts_problem(found: Server) -> str | None:
    """What the role lacks of the timeouts deploying.md gives it, with the statements that set
    them; None when it has both."""
    missing = [name for name, value in found.timeouts.items() if value == "0"]
    if not missing:
        return None
    statements = " and ".join(
        f"`ALTER ROLE {found.role} SET {name} = '{TIMEOUTS[name]}'`" for name in missing
    )
    return (
        f"The database role {found.role} has no {' and no '.join(missing)}, so a query that "
        "runs away holds its connection, and a transaction left open holds back vacuum in "
        f"every table, until each ends by itself; set {statements}, which the app's new "
        'connections take, then restart the app (deploying.md, "The database").'
    )


def describe_timeouts(found: Server) -> str:
    """What the role has of the timeouts, as Postgres names them, for the operator."""
    return f"the role {found.role} has {_timeouts(found)}"


def _timeouts(found: Server) -> str:
    return " and ".join(
        f"{name} {'none' if value == '0' else value}" for name, value in found.timeouts.items()
    )


def check_database(settings: Settings) -> None:
    """Refuse in production a pgvector that would lose a project's passages to the filter, and
    say at every start what the server is and what its role allows.

    A pgvector too old is a warning in development, whose one project per test or per person
    loses little; search goes without iterative scans there (`_iterative_scan_supported` in
    app/search.py). A role without timeouts is a warning in production only, since the
    deployment may have its reasons and the compose database has none.
    """
    with engine.connect() as connection:
        found = server(connection)
    production = settings.environment == "production"
    if problem := vector_problem(found):
        if production:
            raise RuntimeError(f"{problem} Production does not start until it does.")
        log.warning(problem)
    log.info(
        "The database is Postgres %s with pgvector %s; %s.",
        found.version,
        found.vector,
        describe_timeouts(found),
    )
    if production and (problem := timeouts_problem(found)):
        log.warning(problem)
    for name in found.stale_collations:
        log.warning(collation_problem(name))


def database_report(connection: Connection, limit: int | None = TABLES_SHOWN) -> Report:
    """The server, the `limit` largest tables (None for every one) and the oldest transaction."""
    tables = [TableState(**row) for row in connection.execute(_TABLES, {"limit": limit}).mappings()]
    oldest = connection.execute(_OLDEST).one_or_none()
    return Report(
        server=server(connection),
        tables=tables,
        oldest=oldest.age if oldest else None,
        oldest_state=oldest.state if oldest else None,
        taken_at=connection.scalar(text("SELECT now()")),
    )


def concerns(report: Report, settings: Settings) -> list[str]:
    """What in the report needs the operator, each as a sentence with what to do: what the
    start refuses or warns of, an autovacuum behind, a transaction held open."""
    return list(findings(report, settings).values())


def findings(report: Report, settings: Settings) -> dict[str, str]:
    """`concerns` by what each is about (`pgvector`, `timeouts`, `collation:<name>`,
    `dead_rows:<table>`, `long_transaction`), so that the watch (app/watch.py) tells a concern
    that goes on from a new one while its figures move."""
    found = {}
    if problem := vector_problem(report.server):
        found["pgvector"] = problem
    if settings.environment == "production" and (problem := timeouts_problem(report.server)):
        found["timeouts"] = problem
    for name in report.server.stale_collations:
        found[f"collation:{name}"] = collation_problem(name)
    for table in report.tables:
        if table.behind:
            share = table.dead / (table.rows + table.dead)
            found[f"dead_rows:{table.name}"] = (
                f"{table.name} has {table.dead:,} dead rows, {share:.0%} of its rows, and was "
                f"last vacuumed {_since(table.vacuumed, report.taken_at)}: autovacuum is "
                f"behind on it; run `VACUUM (VERBOSE, ANALYZE) {table.name};` now and lower "
                'the table\'s autovacuum_vacuum_scale_factor (deploying.md, "Vacuum").'
            )
    if report.oldest is not None and report.oldest > LONG_TRANSACTION:
        found["long_transaction"] = (
            f"A transaction has been open for {span(report.oldest)} "
            f"({report.oldest_state or 'its state hidden'}), and vacuum keeps every row deleted "
            "or replaced since it began; find it in pg_stat_activity and end it with "
            "pg_terminate_backend if nothing should be running that long."
        )
    return found


def describe_database(report: Report, settings: Settings) -> list[str]:
    """The `database` command's report, as lines to print."""
    found = report.server
    lines = [
        f"Postgres {found.version} with pgvector {found.vector or 'not installed'}"
        + (", which searches with iterative index scans." if found.iterative_scans else "."),
        f"The role {found.role} has {_timeouts(found)}.",
        _describe_pooler(settings),
        "",
        "The largest tables:",
    ]
    rows = [("table", "rows", "dead", "size", "indexes", "vacuumed", "analysed")]
    for table in report.tables:
        counted = table.rows + table.dead
        rows.append(
            (
                table.name,
                f"{table.rows:,}",
                f"{table.dead / counted if counted else 0:.0%}",
                _mb(table.size),
                _mb(table.index_size),
                _since(table.vacuumed, report.taken_at),
                _since(table.analyzed, report.taken_at),
            )
        )
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
    for row in rows:
        # The name and the times read left to right, the numbers line up on the right.
        cells = [
            cell.ljust(width) if column in (0, 5, 6) else cell.rjust(width)
            for column, (cell, width) in enumerate(zip(row, widths, strict=True))
        ]
        lines.append("  " + "  ".join(cells).rstrip())
    lines.append("")
    if report.oldest is None:
        lines.append("No other transaction of this role is open.")
    else:
        lines.append(
            f"The oldest open transaction began {span(report.oldest)} ago "
            f"({report.oldest_state or 'its state hidden'})."
        )
    if problems := concerns(report, settings):
        lines.append("")
        lines.append("To look at:")
        lines.extend(f"  - {problem}" for problem in problems)
    else:
        lines.append("Nothing to look at.")
    return lines


def _describe_pooler(settings: Settings) -> str:
    if settings.database_pooler != "transaction":
        return (
            "DATABASE_URL reaches the database directly or through a pooler in session mode, "
            "and the worker listens for queued jobs on it."
        )
    listens = (
        "the worker listens for queued jobs at DATABASE_LISTEN_URL"
        if settings.listen_url
        else "the worker finds queued jobs by polling, since DATABASE_LISTEN_URL is not set"
    )
    return (
        "DATABASE_URL goes through a pooler in transaction mode, which is sent no prepared "
        f"statement, and {listens}."
    )


def _version(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split(".")[:2] if part.isdigit())


def _since(moment: datetime | None, now: datetime) -> str:
    return "never" if moment is None else f"{span(now - moment)} ago"


def span(delta: timedelta) -> str:
    seconds = max(int(delta.total_seconds()), 0)
    if seconds < 120:
        return f"{seconds} s"
    if seconds < 2 * 3600:
        return f"{seconds // 60} min"
    if seconds < 2 * 86400:
        return f"{seconds // 3600} h"
    return f"{seconds // 86400} days"


def _mb(size: int) -> str:
    return f"{size / 1024 / 1024:,.1f} MB"
