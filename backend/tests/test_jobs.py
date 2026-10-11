"""The job queue: jobs are queued with the change that needs them, claimed in order with a
lease, kept alive while they run, finished, given up when every attempt is lost, and pruned once
they are old; and the worker threads that do all that.

The worker tests run real threads against the test database and wait for what they expect,
with a deadline, so a regression shows up as a timeout rather than a hang. The last one runs
the worker command as the process a deployment runs, and stops it the way a deployment does.
"""

import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from select import select as wait_readable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

import app.worker as worker_module
from app import jobs
from app.config import BACKEND_DIR, get_settings
from app.db import engine
from app.jobs import (
    BULK,
    LEASE,
    MAX_ATTEMPTS,
    claim,
    enqueue,
    extend_leases,
    finish,
    release,
)
from app.main import create_app
from app.maintenance import housekeeping
from app.models import Document, Job, Project
from app.worker import HANDLERS, Handler, Worker, serve

INDEX = ["index_document"]


def wait_until(condition: Callable[[], bool], timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.05)
    raise TimeoutError("The worker did not do what the test waited for")


def all_jobs(session) -> list[Job]:
    session.expire_all()
    return session.scalars(select(Job).order_by(Job.id)).all()


def set_lease(session, job_id: int, hours_from_now: float) -> None:
    session.execute(
        text("UPDATE jobs SET lease_until = now() + :delta WHERE id = :id"),
        {"id": job_id, "delta": timedelta(hours=hours_from_now)},
    )
    session.commit()


def lose(session, job_id: int, attempts: int = 1) -> None:
    """Leave the job as a worker that died mid-run does: running, with its lease run out."""
    session.execute(
        text(
            "UPDATE jobs SET status = 'running', attempts = :attempts, worker_id = 'dead:1', "
            "lease_until = now() - interval '1 hour' WHERE id = :id"
        ),
        {"id": job_id, "attempts": attempts},
    )
    session.commit()


def test_uploads_and_reprocesses_queue_index_jobs(client, session, sample_pdf, sample_docx):
    project = client.post("/api/projects", json={"name": "P", "description": ""}).json()
    with sample_pdf.open("rb") as pdf, sample_docx.open("rb") as docx:
        uploaded = client.post(
            f"/api/projects/{project['id']}/documents",
            files=[("files", ("msa.pdf", pdf)), ("files", ("policy.docx", docx))],
        ).json()["documents"]
    ids = [document["id"] for document in uploaded]

    queued = all_jobs(session)
    assert [(job.kind, job.payload) for job in queued] == [
        ("index_document", {"document_id": ids[0]}),
        ("index_document", {"document_id": ids[1]}),
    ]
    assert {(job.org_id, job.project_id, job.status, job.priority) for job in queued} == {
        (1, project["id"], "queued", 0)
    }
    assert {(job.attempts, job.max_attempts) for job in queued} == {(0, MAX_ATTEMPTS)}
    assert all(job.run_at <= datetime.now(UTC) for job in queued)

    # One document again is a user waiting; the whole project is bulk work behind uploads.
    assert client.post(f"/api/documents/{ids[0]}/reprocess").status_code == 202
    assert client.post(f"/api/projects/{project['id']}/reprocess").json() == {"queued": 2}
    later = all_jobs(session)[2:]
    assert [(job.payload["document_id"], job.priority) for job in later] == [
        (ids[0], 0),
        (ids[0], BULK),
        (ids[1], BULK),
    ]


def test_the_claim_takes_jobs_in_order_and_leases_them(session, account):
    project = Project(org_id=account.organization.id, name="P")
    session.add(project)
    session.flush()
    org = account.organization.id

    def queue(priority: int = 0, kind: str = "index_document", **extra) -> int:
        job = enqueue(
            session, kind, org_id=org, project_id=project.id, payload={}, priority=priority, **extra
        )
        session.commit()
        return job.id

    later = queue(priority=5)
    first = queue(priority=0)
    second = queue(priority=0)
    not_yet = queue(run_at=datetime.now(UTC) + timedelta(hours=1))
    not_ours = queue(kind="send_email")

    taken = claim("a:1", INDEX)
    assert taken.id == first
    assert (taken.status, taken.attempts, taken.worker_id) == ("running", 1, "a:1")
    assert taken.lease_until > datetime.now(UTC) + LEASE - timedelta(minutes=1)
    assert claim("a:1", INDEX).id == second
    assert claim("a:1", INDEX).id == later
    # What is left is not due yet, or not a kind this worker runs.
    assert claim("a:1", INDEX) is None
    assert claim("mailer:1", ["send_email"]).id == not_ours
    session.expire_all()
    assert session.get(Job, not_yet).status == "queued"

    with pytest.raises(ValueError, match="not a job kind"):
        enqueue(session, "mine_bitcoin", org_id=org)


def test_two_workers_never_take_the_same_job(session, account):
    project = Project(org_id=account.organization.id, name="P")
    session.add(project)
    session.flush()
    for _ in range(2):
        enqueue(session, "index_document", org_id=project.org_id, project_id=project.id)
    session.commit()

    # One worker's claim is still in its transaction when another claims: the second skips the
    # locked row instead of waiting for it and then taking the same job.
    with engine.connect() as holder:
        held = holder.execute(
            jobs._CLAIM, {"worker": "a:1", "lease": LEASE, "kinds": INDEX}
        ).scalar_one()
        other = claim("b:1", INDEX)
        assert other is not None and other.id != held
        assert claim("c:1", INDEX) is None
        holder.rollback()
    # Rolled back, the first job is queued again and goes to the next claim.
    assert claim("c:1", INDEX).id == held


def test_a_lost_lease_is_claimed_again_and_only_its_holder_extends_or_finishes_it(session, account):
    project = Project(org_id=account.organization.id, name="P")
    session.add(project)
    session.flush()
    job = enqueue(session, "index_document", org_id=project.org_id, project_id=project.id)
    session.commit()

    assert claim("dead:1", INDEX).id == job.id
    # While the lease holds, the job is that worker's.
    assert claim("alive:1", INDEX) is None
    assert extend_leases([job.id], "alive:1") == set()
    assert finish(job, "alive:1", "done") is False
    session.refresh(job)
    before = job.lease_until
    assert extend_leases([job.id], "dead:1") == {job.id}
    session.refresh(job)
    assert job.lease_until > before

    # The worker died: once the lease runs out, the next claim takes the job over.
    set_lease(session, job.id, hours_from_now=-1)
    taken = claim("alive:1", INDEX)
    assert (taken.id, taken.attempts, taken.worker_id) == (job.id, 2, "alive:1")
    assert finish(job, "dead:1", "done") is False
    assert finish(job, "alive:1", "failed", "ValueError: no") is True
    session.refresh(job)
    assert (job.status, job.last_error, job.lease_until) == ("failed", "ValueError: no", None)
    assert job.finished_at is not None
    assert claim("alive:1", INDEX) is None


def test_housekeeping_prunes_old_finished_jobs(session, services, account):
    project = Project(org_id=account.organization.id, name="P")
    session.add(project)
    session.flush()
    rows = {}
    for name, status, days in (
        ("old_done", "done", 31),
        ("old_failed", "failed", 31),
        ("recent", "done", 29),
        ("queued", "queued", 31),
    ):
        job = enqueue(session, "index_document", org_id=project.org_id, project_id=project.id)
        session.flush()
        job.status = status
        if status != "queued":
            job.finished_at = datetime.now(UTC) - timedelta(days=days)
        rows[name] = job.id
    session.commit()

    housekeeping(services.storage)
    kept = {job.id for job in all_jobs(session)}
    assert kept == {rows["recent"], rows["queued"]}


def test_worker_indexes_queued_documents_and_recovers_interrupted_ones(
    session, services, make_document, sample_pdf, sample_docx
):
    queued = make_document(sample_pdf)
    # A document another process was working on when it died: its job's lease has run out.
    interrupted = make_document(sample_docx, project=session.get(Project, queued.project_id))
    interrupted.status = "processing"
    session.commit()
    (lost,) = [job for job in all_jobs(session) if job.payload["document_id"] == interrupted.id]
    lose(session, lost.id)

    worker = Worker(services, concurrency=2)
    worker.start()
    try:
        worker.notify()

        def both_ready() -> bool:
            session.expire_all()
            return queued.status == "ready" and interrupted.status == "ready"

        wait_until(both_ready)
    finally:
        worker.stop()
    assert interrupted.stages["embed"]["status"] == "done"
    assert {(job.status, job.attempts) for job in all_jobs(session)} == {("done", 1), ("done", 2)}
    assert all(job.lease_until is None and job.finished_at for job in all_jobs(session))


def test_a_job_lost_on_every_attempt_is_given_up(session, services, make_document, sample_pdf):
    document = make_document(sample_pdf)
    (job,) = all_jobs(session)
    lose(session, job.id, attempts=MAX_ATTEMPTS)
    document.status = "processing"
    session.commit()

    worker = Worker(services, concurrency=1)
    worker.start()
    try:
        wait_until(lambda: all_jobs(session)[0].status == "failed")
    finally:
        worker.stop()
    (job,) = all_jobs(session)
    assert job.attempts == MAX_ATTEMPTS + 1
    assert job.last_error == f"Given up after {MAX_ATTEMPTS} lost attempts"
    session.refresh(document)
    assert document.status == "failed"
    assert document.error.startswith(f"Indexing stopped unexpectedly {MAX_ATTEMPTS} times")
    # Nothing ran: the parse never happened.
    assert document.stages == {}


def test_a_crash_outside_the_stages_fails_the_job_and_the_document(
    session, services, make_document, sample_pdf, monkeypatch
):
    document = make_document(sample_pdf)

    def crash(document_id, services_):
        raise RuntimeError("the pipeline itself broke")

    monkeypatch.setattr(worker_module, "process_document", crash)
    worker = Worker(services, concurrency=1)
    worker.start()
    try:
        wait_until(lambda: all_jobs(session)[0].status == "failed")
    finally:
        worker.stop()
    assert all_jobs(session)[0].last_error == "RuntimeError: the pipeline itself broke"
    session.refresh(document)
    assert (document.status, document.error) == ("failed", worker_module.CRASHED)


def test_a_job_for_a_document_deleted_meanwhile_just_finishes(
    session, services, make_document, sample_pdf
):
    document = make_document(sample_pdf)
    session.delete(document)
    session.commit()

    worker = Worker(services, concurrency=1)
    worker.start()
    try:
        wait_until(lambda: all_jobs(session)[0].status == "done")
    finally:
        worker.stop()
    assert session.scalar(select(Document.id)) is None


def test_the_worker_keeps_claiming_after_a_database_error(
    session, services, make_document, sample_pdf, monkeypatch
):
    document = make_document(sample_pdf)
    calls = []

    def flaky_claim(worker_id, kinds):
        calls.append(worker_id)
        if len(calls) == 1:
            raise ConnectionError("the database went away")
        return claim(worker_id, kinds)

    monkeypatch.setattr(worker_module, "claim", flaky_claim)
    monkeypatch.setattr(worker_module, "RETRY_SECONDS", 0.05)
    worker = Worker(services, concurrency=1)
    worker.start()
    try:
        wait_until(lambda: all_jobs(session)[0].status == "done")
    finally:
        worker.stop()
    session.refresh(document)
    assert document.status == "ready"
    assert len(calls) >= 2


def test_the_heartbeat_keeps_a_running_jobs_lease_alive(
    session, services, make_document, sample_pdf, monkeypatch
):
    make_document(sample_pdf)
    release = threading.Event()

    def hold(job, services_):
        release.wait(10)

    monkeypatch.setitem(HANDLERS, "index_document", Handler(hold, lambda *args: None))
    monkeypatch.setattr(worker_module, "HEARTBEAT_SECONDS", 0.05)
    worker = Worker(services, concurrency=1)
    worker.start()
    try:
        wait_until(lambda: all_jobs(session)[0].status == "running")
        (job,) = all_jobs(session)
        # Were the lease not extended, this one would run out at once.
        set_lease(session, job.id, hours_from_now=-1)
        wait_until(lambda: all_jobs(session)[0].lease_until > datetime.now(UTC))
        release.set()
        wait_until(lambda: all_jobs(session)[0].status == "done")
    finally:
        release.set()
        worker.stop()


def test_the_worker_keeps_house_after_a_delay(session, services, indexed, monkeypatch):
    from tests.test_maintenance import age

    page = indexed.page_image_key(1)
    services.storage.put(page, b"old page", "image/png")
    age(services.storage, page, days=31)
    monkeypatch.setattr(worker_module, "HOUSEKEEPING_DELAY", 0.05)

    worker = Worker(services, concurrency=1)
    worker.start()
    try:
        wait_until(lambda: not services.storage.exists(page))
    finally:
        worker.stop()
    assert services.storage.exists(indexed.storage_key)


def test_the_api_runs_the_worker_itself_only_when_the_settings_say_so(
    session, services, make_document, sample_pdf, monkeypatch
):
    # The suite starts the API without a worker (RUN_WORKER=false in tests/conftest.py), as a
    # deployment does; with the setting on, as in development, the API indexes what it serves
    # and stops its threads at shutdown.
    with TestClient(create_app(services=services)) as client:
        assert client.app.state.worker is None

    monkeypatch.setattr(get_settings(), "run_worker", True)
    document = make_document(sample_pdf)
    with TestClient(create_app(services=services)) as client:
        worker = client.app.state.worker
        assert isinstance(worker, Worker)

        def ready() -> bool:
            session.expire_all()
            return document.status == "ready"

        wait_until(ready)
    assert not any(thread.is_alive() for thread in worker._threads)


def test_release_hands_back_only_this_workers_running_jobs(session, account):
    project = Project(org_id=account.organization.id, name="P")
    session.add(project)
    session.flush()
    for _ in range(2):
        enqueue(session, "index_document", org_id=project.org_id, project_id=project.id)
    session.commit()
    mine = claim("a:1", INDEX)
    theirs = claim("b:1", INDEX)

    assert release([mine.id, theirs.id], "a:1") == {mine.id}
    assert release([], "a:1") == set()
    session.expire_all()
    mine, theirs = all_jobs(session)
    # Queued again at its old place in the order, with the cut-short attempt not counted.
    assert (mine.status, mine.attempts, mine.worker_id, mine.lease_until) == (
        "queued",
        0,
        None,
        None,
    )
    assert (theirs.status, theirs.attempts, theirs.worker_id) == ("running", 1, "b:1")
    assert claim("c:1", INDEX).id == mine.id


def test_a_stopping_worker_lets_a_quick_job_finish(
    session, services, make_document, sample_pdf, monkeypatch
):
    make_document(sample_pdf)
    monkeypatch.setitem(
        HANDLERS, "index_document", Handler(lambda job, services_: time.sleep(0.3), lambda *a: None)
    )
    worker = Worker(services, concurrency=1)
    worker.start()
    wait_until(lambda: all_jobs(session)[0].status == "running")
    worker.stop(grace=5)
    assert all_jobs(session)[0].status == "done"


def test_a_stopping_worker_hands_back_the_job_it_could_not_finish(
    session, services, make_document, sample_pdf, monkeypatch, caplog
):
    make_document(sample_pdf)
    let_go = threading.Event()

    def hold(job, services_):
        let_go.wait(10)

    monkeypatch.setitem(HANDLERS, "index_document", Handler(hold, lambda *args: None))
    worker = Worker(services, concurrency=1)
    worker.start()
    try:
        wait_until(lambda: all_jobs(session)[0].status == "running")
        with caplog.at_level("WARNING", logger="app.worker"):
            worker.stop(grace=0.2)
    finally:
        let_go.set()
    # Queued again with its lease cleared, so the next worker takes it at once rather than in
    # LEASE; and the attempt uncounted, so a job restarted mid-run three times is not given up.
    (job,) = all_jobs(session)
    assert (job.status, job.attempts, job.worker_id, job.lease_until) == ("queued", 0, None, None)
    assert "were queued again" in caplog.text
    taken = claim("next:1", INDEX)
    assert (taken.id, taken.attempts) == (job.id, 1)


def test_serve_runs_the_worker_until_told_to_stop(session, services, make_document, sample_pdf):
    document = make_document(sample_pdf)
    stopping = threading.Event()
    thread = threading.Thread(
        target=serve, args=(services, get_settings(), stopping), kwargs={"grace": 1}, daemon=True
    )
    thread.start()
    try:

        def ready() -> bool:
            session.expire_all()
            return document.status == "ready"

        wait_until(ready)
    finally:
        stopping.set()
    thread.join(timeout=10)
    assert not thread.is_alive()


def read_until(stream, marker: str, timeout: float) -> str:
    """What a process wrote to `stream` up to and including the line that holds `marker`."""
    deadline = time.monotonic() + timeout
    output = b""
    while marker.encode() not in output:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"The worker did not report {marker!r} in time:\n{output.decode()}")
        ready, _, _ = wait_readable([stream], [], [], remaining)
        if ready:
            chunk = os.read(stream.fileno(), 4096)
            if not chunk:
                raise AssertionError(f"The worker exited before it was running:\n{output.decode()}")
            output += chunk
    return output.decode()


def test_the_worker_command_starts_and_stops_on_sigterm(session):
    # The process a deployment runs: it starts with the same checks as the API (the refusals
    # are in test_config.py), says when it is running, and SIGTERM stops it cleanly. The queue
    # is empty here, so the real services it builds are never called.
    process = subprocess.Popen(
        [sys.executable, "-m", "app.worker", "--grace", "1"],
        cwd=BACKEND_DIR,
        env=dict(os.environ),
        stderr=subprocess.PIPE,
    )
    try:
        output = read_until(process.stderr, "is running", timeout=60)
        process.send_signal(signal.SIGTERM)
        _, rest = process.communicate(timeout=30)
        output += rest.decode()
    finally:
        process.kill()
    assert process.returncode == 0
    assert "Worker" in output and "stopped" in output
    assert "Traceback" not in output
