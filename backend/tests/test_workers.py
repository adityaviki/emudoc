"""Whether anyone takes the queue's work: a worker says it runs at its start and with every
heartbeat, and that it stopped at its stop (app/jobs.py), and the members' status line, the
Administration page and the `jobs` command read it. The suite's apps run no worker (RUN_WORKER=false
in tests/conftest.py), so a status a test reads says none runs until the test starts one.
"""

import logging
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select, text

import app.worker as worker_module
from app.config import get_settings
from app.jobs import SEEN_WITHIN, beat, record_stop, work_running, workers_seen
from app.main import WORK_STOPPED, create_app
from app.maintenance import housekeeping, main
from app.models import WorkerSeen
from app.worker import HEARTBEAT_SECONDS, Worker
from tests.test_jobs import wait_until

STARTED = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)
SHA = "3f2c1ab" + "0" * 33


def seen(session, worker_id: str) -> WorkerSeen:
    session.expire_all()
    return session.get(WorkerSeen, worker_id)


def backdate(session, worker_id: str, minutes: float, stopped: bool = False) -> None:
    """Make the worker last seen, and stopped if asked, `minutes` ago."""
    session.execute(
        text(
            "UPDATE workers SET last_seen_at = now() - :ago, "
            "stopped_at = CASE WHEN :stopped THEN now() - :ago END WHERE id = :id"
        ),
        {"id": worker_id, "ago": timedelta(minutes=minutes), "stopped": stopped},
    )
    session.commit()


def workers_status(client) -> dict:
    return client.get("/api/status").json()["workers"]


def test_three_heartbeats_go_by_before_a_worker_counts_as_gone():
    # One round missed, or two, is no worker gone.
    assert SEEN_WITHIN.total_seconds() >= 3 * HEARTBEAT_SECONDS


def test_a_worker_says_it_runs_from_its_start_every_round_and_that_it_stopped(
    session, services, monkeypatch
):
    monkeypatch.setattr(worker_module, "HEARTBEAT_SECONDS", 0.05)
    worker = Worker(services, concurrency=1)
    worker.start()
    try:
        wait_until(lambda: seen(session, worker.id) is not None)
        first = seen(session, worker.id)
        assert first.stopped_at is None and first.version == "development"
        # It holds no job, and says it runs all the same.
        once = first.last_seen_at
        wait_until(lambda: seen(session, worker.id).last_seen_at > once)
        (shown,) = workers_seen(session)
        assert (shown.id, shown.running) == (worker.id, True)
    finally:
        worker.stop()
    stopped = seen(session, worker.id)
    stopped_at = stopped.stopped_at
    assert stopped_at is not None and stopped.last_seen_at == stopped_at
    # The operator reads it stopped; the members' status counts it a while longer, so that a
    # release, which starts the next worker a few seconds after, never reads as none.
    assert [worker.running for worker in workers_seen(session)] == [False]
    assert work_running(session).running

    # Nothing written after the stop undoes it.
    worker._beat()
    assert seen(session, worker.id).stopped_at == stopped_at


def test_a_beat_that_fails_leaves_the_worker_running(session, services, monkeypatch, caplog):
    monkeypatch.setattr(worker_module, "HEARTBEAT_SECONDS", 0.05)
    calls = []

    def flaky(worker_id, version, started):
        calls.append(worker_id)
        if len(calls) == 1:
            raise ConnectionError("the database went away")
        beat(worker_id, version, started)

    monkeypatch.setattr(worker_module, "beat", flaky)
    worker = Worker(services, concurrency=1)
    with caplog.at_level(logging.ERROR, logger="app.worker"):
        worker.start()
        try:
            wait_until(lambda: seen(session, worker.id) is not None)
            assert worker.stopped_threads() == []
        finally:
            worker.stop()
    assert f"Could not record that worker {worker.id} is running" in caplog.text


def test_the_same_host_and_pid_again_is_a_new_start(session):
    # As a container restarted in place starts its process under the same name.
    beat("host:7", "old", STARTED)
    record_stop("host:7")
    beat("host:7", SHA, STARTED + timedelta(hours=1))
    row = seen(session, "host:7")
    assert (row.version, row.started_at, row.stopped_at) == (
        SHA,
        STARTED + timedelta(hours=1),
        None,
    )


def test_the_status_says_when_no_worker_runs(client, session):
    # The suite's app runs none, and none ever ran against this database.
    status = workers_status(client)
    assert status["running"] is False
    assert status["error"] == (
        "No worker has run against this database yet, so uploads stay queued and questions go "
        "unanswered. Run `uv run python -m app.worker` in emudoc/backend, or leave RUN_WORKER "
        "unset for the backend to run one itself, and restart it."
    )

    beat("host:1", SHA, STARTED)
    assert workers_status(client) == {"running": True, "error": None}

    # Lost: its heartbeat stopped three rounds ago with no stop.
    backdate(session, "host:1", minutes=4)
    last = seen(session, "host:1").last_seen_at.astimezone(UTC)
    status = workers_status(client)
    assert status["running"] is False
    assert status["error"].startswith(
        f"No worker has been seen since {last:%Y-%m-%d %H:%M} UTC, so uploads stay queued"
    )

    # Stopped a minute ago, as in a release before the next one starts: still running.
    backdate(session, "host:1", minutes=1, stopped=True)
    assert workers_status(client)["running"] is True
    backdate(session, "host:1", minutes=4, stopped=True)
    assert workers_status(client)["running"] is False


def test_a_deployments_status_says_the_work_waits_naming_nothing_of_it(
    client, session, monkeypatch
):
    monkeypatch.setattr(get_settings(), "environment", "production")
    response = client.get("/api/status")
    assert response.json()["workers"] == {"running": False, "error": WORK_STOPPED}
    for setup in ("app.worker", "RUN_WORKER", "backend", "restart"):
        assert setup not in response.text

    beat("host:1", SHA, STARTED)
    assert workers_status(client) == {"running": True, "error": None}


def test_an_api_that_runs_the_worker_itself_says_one_runs(session, services, monkeypatch):
    monkeypatch.setattr(get_settings(), "run_worker", True)
    with TestClient(create_app(services=services)) as client:
        worker = client.app.state.worker
        wait_until(lambda: workers_status(client)["running"])
    assert seen(session, worker.id).stopped_at is not None


def test_housekeeping_forgets_the_workers_not_seen_for_a_day(session, services):
    for worker_id in ("host:1", "host:2", "host:3"):
        beat(worker_id, SHA, STARTED)
    backdate(session, "host:2", minutes=23 * 60)
    backdate(session, "host:3", minutes=25 * 60, stopped=True)
    housekeeping(services.storage)
    assert session.scalars(select(WorkerSeen.id).order_by(WorkerSeen.id)).all() == [
        "host:1",
        "host:2",
    ]


def test_the_jobs_command_and_the_admins_read_the_workers(client, session, capsys):
    main(["jobs"])
    assert capsys.readouterr().out.splitlines()[1:] == [
        "No worker has been seen running in the last 3 min: nothing is indexed, answered, "
        "mailed or exported until one runs (`python -m app.worker`).",
    ]

    beat("host:1", SHA, STARTED)
    beat("host:2", "development", STARTED)
    beat("host:3", SHA, STARTED)
    # Two and a half minutes reads "2 min" however long the command takes to start on a busy
    # machine, where 90 s could read 91.
    backdate(session, "host:1", minutes=2.5)
    backdate(session, "host:2", minutes=10, stopped=True)
    backdate(session, "host:3", minutes=20)
    main(["jobs"])
    out = capsys.readouterr().out.splitlines()
    assert out[1:] == [
        "The workers seen in the last 24 h:",
        "  worker  release      started               stands",
        "  host:1  3f2c1ab      2026-10-23 09:00 UTC  running, seen 2 min ago",
        "  host:2  development  2026-10-23 09:00 UTC  stopped 10 min ago",
        "  host:3  3f2c1ab      2026-10-23 09:00 UTC  not seen for 20 min",
    ]

    shown = client.get("/api/organization/jobs").json()["workers"]
    assert [(worker["id"], worker["running"]) for worker in shown] == [
        ("host:1", True),
        ("host:2", False),
        ("host:3", False),
    ]
    assert shown[0]["version"] == SHA and shown[0]["stopped_at"] is None
    assert shown[1]["stopped_at"] is not None and shown[2]["stopped_at"] is None

    # None running is said, whoever was seen before.
    record_stop("host:1")
    backdate(session, "host:1", minutes=5, stopped=True)
    main(["jobs"])
    assert capsys.readouterr().out.splitlines()[-1].startswith("No worker has been seen running")
