"""What the app asks of its Postgres server and lets a managed one ask of it (app/postgres.py,
app/db.py): a pgvector that keeps a project's passages, the role's timeouts, which no stage
and no answer outlasts while the work that is the data's length lifts them, a pooler in
transaction mode, which is sent no prepared statement and no LISTEN, and a failover, which a
document being indexed outlasts."""

import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from alembic import command
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError, OperationalError

from app import postgres
from app.config import Settings, get_settings
from app.db import (
    MIGRATION_LOCK,
    TOO_SLOW,
    alembic_config,
    canceled,
    connection_lost,
    engine,
    make_engine,
    unbounded,
)
from app.indexing.pipeline import LOST_DATABASE, process_document
from app.jobs import INDEX_DOCUMENT, claim, listen
from app.main import create_app
from app.maintenance import housekeeping, main, rebuild_embeddings, requeue_stale_embeddings
from app.models import Document, Job, Organization, Project
from app.postgres import (
    ARCHIVE_LAG,
    Archive,
    Report,
    Server,
    TableState,
    archive,
    check_database,
    collation_problem,
    concerns,
    database_report,
    describe_database,
    findings,
)
from app.restore import after_restore
from app.retention import purge_due_accounts
from app.startup import prepare
from app.storage import LocalStorage
from app.worker import HANDLERS, MAX_RETRIES, Worker
from tests.test_retention import make_due

# The role's timeouts in these tests, and how long the work under them waits: past them, so
# that the work fails unless it holds no transaction or lifted them, and well under a second.
LIMIT = "200ms"
LONGER = 0.5


@pytest.fixture
def role_timeouts():
    """The timeouts deploying.md gives the app's role, as every connection the app opens from
    here on starts with them. A listener on the engine stands for `ALTER ROLE … SET`, which
    would outlive a failed run and reach the development database the role shares. Its `SET`
    is the session's, which a pooler in transaction mode would leave on the server connection
    for whichever client it serves next, so the suite run through one skips these tests."""
    if get_settings().database_pooler == "transaction":
        pytest.skip("the role's timeouts are simulated per session, which a pooler would spread")

    def limit(connection, _record):
        with connection.cursor() as cursor:
            cursor.execute(f"SET statement_timeout = '{LIMIT}'")
            cursor.execute(f"SET idle_in_transaction_session_timeout = '{LIMIT}'")
        connection.commit()

    engine.dispose()
    event.listen(engine, "connect", limit)
    yield
    event.remove(engine, "connect", limit)
    engine.dispose()


@contextmanager
def held(statement: str, seconds: float | None = LONGER):
    """Another client that takes a lock with `statement` and lets it go after `seconds`, or
    with None when the block ends, as its connection closes."""
    url = engine.url.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(url) as other:
        other.execute(statement)
        if seconds is None:
            yield
            return
        release = threading.Timer(seconds, other.rollback)
        release.start()
        try:
            yield
        finally:
            release.join()


def took(work) -> float:
    started = time.monotonic()
    work()
    return time.monotonic() - started


@pytest.fixture
def production(monkeypatch):
    monkeypatch.setattr(get_settings(), "environment", "production")


@pytest.fixture
def direct(monkeypatch):
    """No pooler, whatever the suite's environment says: the suite also runs through one."""
    monkeypatch.setattr(get_settings(), "database_pooler", "")
    monkeypatch.setattr(get_settings(), "database_listen_url", "")


def found() -> Server:
    """The test database's server, as the start reads it."""
    with engine.connect() as connection:
        return postgres.server(connection)


def test_the_role_s_timeouts_stop_a_statement_and_an_idle_transaction(role_timeouts):
    with engine.connect() as connection:
        with pytest.raises(OperationalError) as stopped:
            connection.execute(text("SELECT pg_sleep(:s)"), {"s": LONGER})
        assert canceled(stopped.value)
        connection.rollback()

        connection.execute(text("SELECT 1"))
        time.sleep(LONGER)
        with pytest.raises(DBAPIError, match="terminating connection due to idle-in-transaction"):
            connection.execute(text("SELECT 1"))


def test_unbounded_lifts_them_for_its_transaction_only(role_timeouts):
    with engine.connect() as connection:
        # Postgres 17's limit on a whole transaction, which a role may carry too.
        if connection.scalar(text("SELECT current_setting('transaction_timeout', true)")):
            connection.execute(text(f"SET transaction_timeout = '{LIMIT}'"))
            connection.commit()
        unbounded(connection)
        connection.execute(text("SELECT pg_sleep(:s)"), {"s": LONGER})
        time.sleep(LONGER)
        assert connection.scalar(text("SHOW statement_timeout")) == "0"
        connection.commit()
        assert connection.scalar(text("SHOW statement_timeout")) == LIMIT
        assert connection.scalar(text("SHOW idle_in_transaction_session_timeout")) == LIMIT
        connection.rollback()


def test_no_stage_holds_a_transaction_while_it_waits(
    session, services, make_document, tmp_path, role_timeouts, monkeypatch
):
    # The waits on the model and the embeddings endpoint run to minutes in production; the
    # stages read first, so a stage that kept the reading transaction open across one would
    # be ended by the role's idle timeout and fail at its next statement.
    def slowly(work):
        def slow(*args, **kwargs):
            time.sleep(LONGER)
            return work(*args, **kwargs)

        return slow

    monkeypatch.setattr(services.llm, "structured", slowly(services.llm.structured))
    monkeypatch.setattr(
        services.embedder, "embed_documents", slowly(services.embedder.embed_documents)
    )
    scan = tmp_path / "scan.png"
    Image.new("RGB", (400, 300), "white").save(scan)
    flat = tmp_path / "notes.txt"
    flat.write_text("\n\n".join(f"Paragraph number {n} of the notes." for n in range(16)))
    # Read by the model (parse), divided by it (structure), summarised (enrich), embedded.
    documents = [make_document(scan), make_document(flat)]
    for document in documents:
        assert process_document(document.id, services) == []
    session.expire_all()
    calls = [name for name, _ in services.llm.calls]
    assert {"OcrPage", "InferredSections"} <= set(calls)
    for document in documents:
        assert document.status == "ready"
        assert {stage["status"] for stage in document.stages.values()} == {"done"}


def test_the_housekeeping_round_outlasts_the_role_s_timeouts(
    session, services, role_timeouts, monkeypatch, capsys
):
    # The round holds one transaction across the walk of the storage, idle while a bucket
    # lists, and so does the command that lists what a delete left behind.
    listed = LocalStorage.list

    def slow_list(storage, prefix=""):
        time.sleep(LONGER)
        yield from listed(storage, prefix)

    monkeypatch.setattr(LocalStorage, "list", slow_list)
    housekeeping(services.storage)
    main(["sweep-orphans"])
    assert capsys.readouterr().out.startswith("Found nothing left behind")


def test_the_migrations_wait_for_their_lock_past_the_role_s_timeouts(role_timeouts):
    # Replicas of a release upgrade one at a time; the second waits as long as the first runs.
    with held(f"SELECT pg_advisory_xact_lock({MIGRATION_LOCK})"):
        assert took(lambda: command.upgrade(alembic_config(), "head")) >= LONGER - 0.05
    with held(f"SELECT pg_advisory_xact_lock({MIGRATION_LOCK})"):
        assert took(rebuild_embeddings) >= LONGER - 0.05


def test_the_maintenance_work_outlasts_the_role_s_timeouts(
    session, services, account, make_account, role_timeouts
):
    # Each waits here on a lock another client holds, as it would on a long scan of its own.
    with held("LOCK TABLE documents IN EXCLUSIVE MODE"):
        requeue_stale_embeddings()
    with held("LOCK TABLE documents IN EXCLUSIVE MODE"):
        after_restore(services.storage, datetime.now(UTC))
    leaving = make_account("leaving@solo.example")
    make_due(session, leaving.user)
    org_id = leaving.organization.id
    with held(f"SELECT 1 FROM organizations WHERE id = {org_id} FOR UPDATE"):
        assert purge_due_accounts(services.storage) == 1
    assert session.scalar(select(Organization.id).where(Organization.id == org_id)) is None


def test_a_project_s_deletion_outlasts_the_role_s_timeouts(client, session, role_timeouts):
    project = client.post("/api/projects", json={"name": "Archive"}).json()
    assert client.delete(f"/api/projects/{project['id']}").status_code == 204
    # Deleted for good from the trash, it goes in one statement that may take as long as the
    # project is large.
    with held(f"SELECT 1 FROM projects WHERE id = {project['id']} FOR UPDATE"):
        assert client.delete(f"/api/trash/projects/{project['id']}").status_code == 204
    assert session.scalar(select(Project.id).where(Project.id == project["id"])) is None


def test_a_request_the_database_stopped_gets_a_sentence(client, session, role_timeouts, caplog):
    # Any other request is held to the role's statement_timeout, and one that runs into it is
    # told so in a sentence, with one line in the log rather than a traceback. The lock lasts
    # until the answer, however long a slow machine takes to bring the request to its query.
    with held("LOCK TABLE projects IN ACCESS EXCLUSIVE MODE", seconds=None):
        with caplog.at_level("WARNING", logger="app.main"):
            response = client.get("/api/projects")
    assert response.status_code == 503
    assert response.json() == {"detail": TOO_SLOW}
    assert response.headers["retry-after"]
    assert "GET /api/projects was stopped by the database: " in caplog.text
    assert "canceling statement due to statement timeout" in caplog.text
    assert "Traceback" not in caplog.text
    assert client.get("/api/projects").status_code == 200


def test_production_refuses_a_pgvector_that_loses_passages(
    services, production, monkeypatch, caplog
):
    real = found()
    monkeypatch.setattr(
        postgres, "server", lambda _: replace(real, vector="0.7.4", offered="0.8.1")
    )
    with pytest.raises(RuntimeError) as refusal:
        prepare(get_settings(), services)
    assert str(refusal.value).startswith("The database has pgvector 0.7.4, and a search by")
    assert "a project finds few of its own or none" in str(refusal.value)
    assert "the server has 0.8.1: run `ALTER EXTENSION vector UPDATE;`" in str(refusal.value)
    assert str(refusal.value).endswith("Production does not start until it does.")

    monkeypatch.setattr(
        postgres, "server", lambda _: replace(real, vector="0.7.4", offered="0.7.4")
    )
    with pytest.raises(RuntimeError, match="the server has nothing newer: upgrade pgvector"):
        check_database(get_settings())

    # Development warns and searches without iterative scans.
    monkeypatch.setattr(get_settings(), "environment", "development")
    with caplog.at_level("WARNING", logger="app.postgres"):
        check_database(get_settings())
    assert "The database has pgvector 0.7.4" in caplog.text


def test_the_start_says_what_the_server_is_and_production_asks_for_timeouts(production, caplog):
    with caplog.at_level("INFO", logger="app.postgres"):
        check_database(get_settings())
    server = found()
    assert f"The database is Postgres {server.version} with pgvector {server.vector}" in (
        caplog.text
    )
    assert (
        f"the role {server.role} has statement_timeout none and "
        "idle_in_transaction_session_timeout none."
    ) in caplog.text
    assert (
        f"The database role {server.role} has no statement_timeout and no "
        "idle_in_transaction_session_timeout, so a query that runs away holds its connection"
    ) in caplog.text
    assert f"`ALTER ROLE {server.role} SET statement_timeout = '30s'`" in caplog.text
    assert (
        f"`ALTER ROLE {server.role} SET idle_in_transaction_session_timeout = '5min'`"
        in caplog.text
    )


def test_a_role_with_timeouts_is_not_warned_about(production, role_timeouts, caplog):
    with caplog.at_level("INFO", logger="app.postgres"):
        check_database(get_settings())
    assert f"statement_timeout {LIMIT} and idle_in_transaction_session_timeout {LIMIT}" in (
        caplog.text
    )
    assert "WARNING" not in caplog.text


def test_behind_a_transaction_pooler_nothing_is_prepared():
    # psycopg prepares a statement after its fifth run on a connection, and a pooler in
    # transaction mode may run the sixth on a server connection that never saw it.
    def prepared(pooler: str) -> int:
        url = engine.url.render_as_string(hide_password=False)
        pooled = make_engine(Settings(_env_file=None, database_url=url, database_pooler=pooler))
        try:
            with pooled.connect() as connection:
                for number in range(8):
                    connection.execute(text("SELECT CAST(:n AS int) + 1"), {"n": number})
                return connection.scalar(text("SELECT count(*) FROM pg_prepared_statements"))
        finally:
            pooled.dispose()

    assert prepared("") >= 1
    assert prepared("transaction") == 0


def test_the_listener_connects_where_it_is_told(direct, monkeypatch):
    seen: list[str] = []

    def connect(conninfo: str, **_: object):
        seen.append(conninfo)
        raise psycopg.OperationalError("not here")

    monkeypatch.setattr(psycopg, "connect", connect)
    monkeypatch.setattr(get_settings(), "database_pooler", "transaction")
    with pytest.raises(RuntimeError, match="set DATABASE_LISTEN_URL"):
        listen(threading.Event(), lambda: None, 0.1)
    assert seen == []
    direct = "postgresql+psycopg://app:secret@db-direct.internal:5432/emudoc"
    monkeypatch.setattr(get_settings(), "database_listen_url", direct)
    with pytest.raises(psycopg.OperationalError):
        listen(threading.Event(), lambda: None, 0.1)
    assert seen == ["postgresql://app:secret@db-direct.internal:5432/emudoc"]


def test_a_worker_with_nowhere_to_listen_polls_and_says_so(services, direct, monkeypatch, caplog):
    monkeypatch.setattr(get_settings(), "database_pooler", "transaction")
    worker = Worker(services, concurrency=0, answer_concurrency=0)
    with caplog.at_level("WARNING", logger="app.worker"):
        worker.start()
    try:
        assert "listener" not in {thread.name for thread in worker._threads}
        assert "DATABASE_LISTEN_URL is not set: the threads find queued jobs by polling" in (
            caplog.text
        )
        assert "a question waits up to 2 seconds before its answer starts" in caplog.text
    finally:
        worker.stop()


def test_the_database_report_lists_the_tables_and_what_to_look_at(session, direct, capsys):
    # Development's server is fit, so the command prints and exits 0.
    main(["database"])
    printed = capsys.readouterr().out.splitlines()
    server = found()
    assert printed[0] == (
        f"Postgres {server.version} with pgvector {server.vector}, which searches with "
        "iterative index scans."
    )
    assert printed[1] == (
        f"The role {server.role} has statement_timeout none and "
        "idle_in_transaction_session_timeout none."
    )
    assert printed[2].startswith("DATABASE_URL reaches the database directly")
    header = printed[printed.index("The largest tables:") + 1].split()
    assert header == ["table", "rows", "dead", "size", "indexes", "vacuumed", "analysed"]
    assert printed[-1] == "Nothing to look at."


def test_the_report_names_a_table_vacuum_is_behind_on_and_a_long_transaction(session):
    with engine.connect() as connection:
        report = database_report(connection)
    passages = TableState("passages", 40_000, 60_000, 1 << 30, 1 << 31, None, None)
    behind = Report(
        report.server,
        [passages],
        timedelta(hours=3),
        "idle in transaction",
        report.taken_at,
    )
    problems = concerns(behind, get_settings())
    # The watch (app/watch.py) tells one concern from another by what it is about.
    assert list(findings(behind, get_settings())) == ["dead_rows:passages", "long_transaction"]
    assert problems[0].startswith(
        "passages has 60,000 dead rows, 60% of its rows, and was last vacuumed never: "
        "autovacuum is behind on it; run `VACUUM (VERBOSE, ANALYZE) passages;` now"
    )
    assert problems[1].startswith(
        "A transaction has been open for 3 h (idle in transaction), and vacuum keeps every row"
    )
    lines = describe_database(behind, get_settings())
    assert "  passages  40,000   60%  1,024.0 MB  2,048.0 MB  never     never" in lines
    assert "The oldest open transaction began 3 h ago (idle in transaction)." in lines
    assert lines[-3:-2] == ["To look at:"]

    # Few dead rows are vacuum's ordinary lag, however large their share.
    small = TableState("jobs", 7, 40, 0, 0, None, None)
    assert (
        concerns(Report(report.server, [small], None, None, report.taken_at), get_settings()) == []
    )


def test_a_collation_whose_icu_moved_is_named_with_the_index_to_rebuild(
    session, monkeypatch, caplog
):
    with engine.connect() as connection:
        report = database_report(connection)
    # The server's own ICU made the schema's collation.
    assert report.server.stale_collations == []

    # After an upgrade that brings another ICU, the library's index may be out of order.
    moved = replace(report.server, stale_collations=["natural_order"])
    stale = Report(moved, [], None, None, report.taken_at)
    assert list(findings(stale, get_settings())) == ["collation:natural_order"]
    assert concerns(stale, get_settings()) == [
        "The collation natural_order was made with another version of ICU than the database "
        "server has now, so ix_documents_listing may hold names out of order; as the database's "
        "owner, run `REINDEX INDEX CONCURRENTLY ix_documents_listing;` and then `ALTER "
        'COLLATION natural_order REFRESH VERSION;` (deploying.md, "The database").'
    ]
    # Every start says so too.
    monkeypatch.setattr(postgres, "server", lambda connection: moved)
    check_database(get_settings())
    assert collation_problem("natural_order") in caplog.text


def test_a_server_that_archives_nothing_has_no_archive_to_report(session):
    # The compose database runs with archive_mode off, as development has it.
    with engine.connect() as connection:
        assert archive(connection) is None


def test_the_archive_is_failing_once_a_failure_is_newer_than_the_last_success():
    taken = datetime(2026, 10, 18, 9, 30, tzinfo=UTC)
    keeping_up = Archive(
        archived=40,
        failed=2,
        archived_at=taken - timedelta(minutes=20),
        failed_at=taken - timedelta(hours=3),
        pending=0,
        taken_at=taken,
    )
    # A quiet database finishes no segment: an old last archive alone is no lag.
    assert not keeping_up.failing and keeping_up.behind is None
    stuck = replace(keeping_up, failed=9, failed_at=taken - timedelta(seconds=40), pending=4)
    assert stuck.failing and stuck.behind == timedelta(minutes=20)
    # A segment waits a moment for the archiver at every switch, within the recovery point.
    switching = replace(keeping_up, archived_at=taken - ARCHIVE_LAG, pending=1)
    assert switching.behind is None
    never = replace(stuck, archived=0, archived_at=None, pending=None)
    assert never.failing and never.behind is None


def test_the_report_sees_a_transaction_another_client_holds(session):
    with held("SELECT 1", seconds=LONGER):
        time.sleep(0.05)
        with engine.connect() as connection:
            report = database_report(connection)
    assert report.oldest is not None and report.oldest >= timedelta(seconds=0.05)
    assert report.oldest_state == "idle in transaction"


def test_production_s_report_asks_for_timeouts_and_exits_1(session, production, direct, capsys):
    with pytest.raises(SystemExit) as stop:
        main(["database"])
    assert stop.value.code == 1
    printed = capsys.readouterr().out
    assert f"To look at:\n  - The database role {found().role} has no statement_timeout" in printed


def test_the_app_starts_against_a_role_with_timeouts(services, role_timeouts):
    # The start's own checks, the requeue's look at every passage among them, and a request.
    with TestClient(create_app(services=services)) as client:
        assert client.get("/api/health").json()["status"] == "ok"


@contextmanager
def connection_dropped_at(*prefixes: str):
    """Kill the database session that runs the first statement starting with each prefix in
    turn, as a failover does, just before the statement reaches it; between them, and after,
    the database answers again at once.

    The session is asked its own process id rather than read from the connection, since a
    pooler reports one of its own: the question opens the transaction the statement then runs
    in, which a pooler in transaction mode keeps on the server connection that answered."""
    url = engine.url.set(drivername="postgresql").render_as_string(hide_password=False)
    dropped: list[str] = []

    def drop(conn, cursor, statement, parameters, context, executemany):
        if len(dropped) == len(prefixes) or not statement.lstrip().startswith(
            prefixes[len(dropped)]
        ):
            return
        dropped.append(statement)
        (pid,) = cursor.connection.execute("SELECT pg_backend_pid()").fetchone()
        with psycopg.connect(url, autocommit=True) as other:
            other.execute("SELECT pg_terminate_backend(%s, 5000)", (pid,))

    event.listen(engine, "before_cursor_execute", drop)
    try:
        yield dropped
    finally:
        event.remove(engine, "before_cursor_execute", drop)


def test_a_stage_that_loses_the_database_runs_again(session, services, make_document, sample_pdf):
    # A failover drops the stage's connection mid-write: the stage is tried again with the
    # usual backoff, as for a provider that is down, and the document says so; it does not
    # end failed for an "Unexpected error" that a moment cured.
    document = make_document(sample_pdf)
    job = claim("test-worker", [INDEX_DOCUMENT])
    with connection_dropped_at("UPDATE passages SET embedding") as dropped:
        assert HANDLERS[INDEX_DOCUMENT].run(job, services) is None
    assert dropped
    session.expire_all()
    embed = document.stages["embed"]
    assert embed["status"] == "failed"
    assert embed["detail"].startswith(LOST_DATABASE)
    assert f"Retrying in 1 minute (1 of {MAX_RETRIES})." in embed["detail"]
    # The stages before it stand; the retry is queued.
    assert document.status == "ready"
    queued = session.scalars(select(Job).where(Job.status == "queued")).all()
    assert [queued_job.payload.get("retry") for queued_job in queued] == [1]


def test_a_job_whose_database_stays_away_waits_for_its_lease(
    session, services, make_document, sample_pdf, monkeypatch, caplog
):
    # Still away when the stage's failure would be written: nothing can be marked, so the job
    # is left as a process that died leaves it, for the next claim once its lease runs out,
    # rather than settled as failed and its document stuck in processing.
    document = make_document(sample_pdf)
    worker = Worker(services, concurrency=0)
    job = claim(worker.id, [INDEX_DOCUMENT])
    away = OperationalError("SELECT 1", {}, psycopg.OperationalError("connection refused"))

    def unreachable(*_args, **_kwargs):
        raise away

    monkeypatch.setattr("app.worker.process_document", unreachable)
    with caplog.at_level("WARNING", logger="app.worker"):
        worker._run(job)
    assert f"Job {job.id} (index_document) stopped: the database could not be reached" in (
        caplog.text
    )
    session.expire_all()
    assert session.get(Job, job.id).status == "running"
    assert session.get(Document, document.id).status == "queued"

    # Any other error in the pipeline still fails the document and the job.
    monkeypatch.setattr("app.worker.process_document", lambda *_: 1 / 0)
    worker._run(job)
    session.expire_all()
    assert session.get(Job, job.id).status == "failed"
    assert session.get(Document, document.id).status == "failed"


def test_a_stage_whose_failure_cannot_be_marked_is_left_for_the_next_claim(
    session, services, make_document, sample_pdf
):
    # The database goes away again as the stage's failure is written: the run ends with the
    # lost connection, for the worker to leave to the lease, with the document neither failed
    # nor marked, and what the stage used recorded all the same.
    document = make_document(sample_pdf)
    job = claim("test-worker", [INDEX_DOCUMENT])
    with connection_dropped_at("UPDATE passages SET embedding", "UPDATE jobs") as dropped:
        with pytest.raises(OperationalError) as lost:
            HANDLERS[INDEX_DOCUMENT].run(job, services)
    assert len(dropped) == 2
    assert connection_lost(lost.value)
    session.expire_all()
    assert document.status == "processing"
    assert document.stages["embed"]["status"] == "running"
    assert document.usage["embed"]["inputs"] > 0
