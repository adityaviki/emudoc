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

import app.indexing.pipeline as pipeline_module
import app.worker as worker_module
from app import jobs
from app.config import BACKEND_DIR, get_settings
from app.db import engine
from app.indexing.pipeline import process_document
from app.jobs import (
    BULK,
    LEASE,
    MAX_ATTEMPTS,
    SHARE_WINDOW,
    Lease,
    LeaseLost,
    claim,
    claim_params,
    enqueue,
    extend_leases,
    finish,
    release,
    renew,
)
from app.llm import LLMError
from app.main import create_app
from app.maintenance import housekeeping
from app.models import Block, Document, Job, Project, Section
from app.worker import HANDLERS, MAX_RETRIES, Handler, Worker, serve
from tests.test_pipeline import rows, stage_statuses

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
    first, second = (job.id for job in queued)

    # A job runs every stage its document has not finished, so one queued for a document
    # replaces any still queued for it: a reprocess before the worker got to the upload leaves
    # one job, not two runs. The whole project is bulk work behind uploads, but a job that
    # replaces an upload's keeps the upload's place in the queue.
    assert client.post(f"/api/documents/{ids[0]}/reprocess").status_code == 202
    kept, replaced = all_jobs(session)
    assert (replaced.payload, replaced.priority) == ({"document_id": ids[0]}, 0)
    assert replaced.id > second and kept.id == second
    assert client.post(f"/api/projects/{project['id']}/reprocess").json() == {"queued": 2}
    assert {(job.payload["document_id"], job.priority) for job in all_jobs(session)} == {
        (ids[0], 0),
        (ids[1], 0),
    }
    # Once the upload's job has run, a reprocess of the project queues bulk work for it.
    session.execute(
        text("UPDATE jobs SET status = 'done' WHERE payload->>'document_id' = :id"),
        {"id": str(ids[0])},
    )
    session.commit()
    assert client.post(f"/api/projects/{project['id']}/reprocess").json() == {"queued": 2}
    assert {
        (job.payload["document_id"], job.priority, job.status) for job in all_jobs(session)
    } == {
        (ids[0], 0, "done"),
        (ids[1], 0, "queued"),
        (ids[0], BULK, "queued"),
    }


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


def test_the_claim_shares_the_workers_between_organisations(session, account, make_account):
    other = make_account("x@other.test")
    busy = Project(org_id=account.organization.id, name="Busy")
    quiet = Project(org_id=other.organization.id, name="Quiet")
    session.add_all([busy, quiet])
    session.flush()

    def queue(project: Project, priority: int = 0) -> int:
        job = enqueue(
            session,
            "index_document",
            org_id=project.org_id,
            project_id=project.id,
            priority=priority,
        )
        session.commit()
        return job.id

    def take(worker: str) -> Job:
        job = claim(worker, INDEX)
        assert job is not None
        return job

    # Several threads: the busy organisation's first job is still running when the next thread
    # claims, so the quiet organisation's single upload goes second, not after the backlog.
    backlog = [queue(busy) for _ in range(3)]
    single = queue(quiet)
    assert [take(f"a:{n}").id for n in range(4)] == [backlog[0], single, backlog[1], backlog[2]]

    # One thread, finishing each job before it claims the next: nothing is running when it
    # claims, and what each organisation finished lately counts instead. What ran so far is
    # finished longer ago than the claim remembers.
    session.execute(
        text("UPDATE jobs SET status = 'done', finished_at = now() - :age"), {"age": SHARE_WINDOW}
    )
    session.commit()
    backlog = [queue(busy) for _ in range(3)]
    single = queue(quiet)
    order = []
    for _ in range(4):
        job = take("b:1")
        order.append(job.id)
        assert finish(job, "b:1", "done")
    assert order == [backlog[0], single, backlog[1], backlog[2]]

    # Priority comes first: the busy organisation's upload goes before the quiet one's bulk
    # reprocess, however much of the workers the busy one has had.
    reprocess = queue(quiet, priority=BULK)
    upload = queue(busy)
    assert [take("c:1").id, take("c:2").id] == [upload, reprocess]
    assert claim("c:3", INDEX) is None


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
        held = holder.execute(jobs._CLAIM, claim_params("a:1", INDEX)).scalar_one()
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
    assert renew(session, Lease(job.id, "alive:1")) is False
    assert finish(job, "alive:1", "done") is False
    session.refresh(job)
    before = job.lease_until
    assert extend_leases([job.id], "dead:1") == {job.id}
    session.refresh(job)
    assert job.lease_until > before
    # A renewal is the holder's too, and lands with the caller's transaction.
    set_lease(session, job.id, hours_from_now=-1)
    assert renew(session, Lease(job.id, "dead:1")) is True
    session.commit()
    session.refresh(job)
    assert job.lease_until > datetime.now(UTC)

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

    def crash(document_id, services_, lease):
        raise RuntimeError("the pipeline itself broke")

    monkeypatch.setattr(worker_module, "process_document", crash)
    worker = Worker(services, concurrency=1)
    worker.start()
    try:
        wait_until(lambda: all_jobs(session)[0].status == "failed")
    finally:
        worker.stop()
    # The operator reads where it broke; the message, which may quote a document, stays out.
    last_error = all_jobs(session)[0].last_error
    assert last_error.startswith("RuntimeError at tests/test_jobs.py:")
    assert last_error.endswith(" in crash") and "broke" not in last_error
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


# The lease check: a run renews its job's lease with every commit, and stops at the first one
# that finds the job another worker's, so two workers never write the same document.


def test_a_run_under_a_lease_renews_it_at_every_commit(
    session, services, make_document, sample_pdf
):
    document = make_document(sample_pdf)
    job = claim("w:1", INDEX)
    # The heartbeat thread is stuck and the lease ran out, but nobody has claimed the job: it
    # is still this worker's, and the run's own commits keep it so.
    set_lease(session, job.id, hours_from_now=-1)
    assert process_document(document.id, services, Lease(job.id, "w:1")) == []
    session.refresh(document)
    (job,) = all_jobs(session)
    assert document.status == "ready"
    assert job.lease_until > datetime.now(UTC)
    assert claim("other:1", INDEX) is None


def test_a_run_whose_job_was_taken_over_before_it_started_writes_nothing(
    session, services, make_document, sample_pdf
):
    document = make_document(sample_pdf)
    job = claim("w:1", INDEX)
    lose(session, job.id)
    assert claim("other:1", INDEX).id == job.id

    with pytest.raises(LeaseLost, match=f"Job {job.id} is no longer worker w:1's"):
        process_document(document.id, services, Lease(job.id, "w:1"))
    session.refresh(document)
    assert (document.status, document.stages) == ("queued", {})


def test_a_run_whose_job_is_taken_over_mid_way_stops_at_its_next_commit(
    session, services, make_document, sample_pdf, monkeypatch, caplog
):
    document = make_document(sample_pdf)
    worker = Worker(services, concurrency=1)
    job = claim(worker.id, INDEX)
    structure = pipeline_module._STAGES["structure"]

    def structure_while_taken_over(session_, document_, services_, meter_):
        detail = structure(session_, document_, services_, meter_)
        # The heartbeat could not reach the database for LEASE: the next claim takes the job
        # while this stage is still running.
        lose(session, job.id)
        assert claim("other:1", INDEX).id == job.id
        return detail

    monkeypatch.setitem(pipeline_module._STAGES, "structure", structure_while_taken_over)
    with caplog.at_level("WARNING", logger="app.worker"):
        worker._run(job)

    # Parse's output was committed before the takeover and stays. Structure's rows and its
    # "done" mark were rolled back together, so the other worker runs it from a clean row
    # set; enrich and embed never ran. The document is not failed: it is the other worker's.
    session.refresh(document)
    assert (document.status, document.error) == ("processing", None)
    assert stage_statuses(document) == {"scan": "done", "parse": "done", "structure": "running"}
    assert rows(session, Block, document)
    assert rows(session, Section, document) == []
    # And the job is the other worker's to finish: not failed, not a dead letter.
    (job,) = all_jobs(session)
    assert (job.status, job.worker_id, job.attempts, job.last_error) == (
        "running",
        "other:1",
        2,
        None,
    )
    assert f"Job {job.id} (index_document) was taken over or handed back" in caplog.text
    # A lost lease is not a stage that crashed, and is not logged as one.
    assert "Stage structure failed" not in caplog.text
    assert finish(job, "other:1", "done") is True


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
        # It reports the jobs it gives up on through the app's mailer.
        assert worker._mailer is client.app.state.mailer

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
    document = make_document(sample_pdf)
    let_go = threading.Event()
    outcome: list[Exception] = []

    def hold(job, services_):
        let_go.wait(10)
        # In a deployment the process has exited by now; a thread that gets further finds the
        # job handed back at its first commit.
        try:
            process_document(job.payload["document_id"], services_, Lease(job.id, job.worker_id))
        except LeaseLost as lost:
            outcome.append(lost)

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
    wait_until(lambda: bool(outcome))
    session.refresh(document)
    assert (document.status, document.stages) == ("queued", {})
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


def test_serve_touches_the_alive_file_while_the_worker_runs(
    session, services, tmp_path, monkeypatch
):
    # The worker serves nothing a health check could ask, so the check reads the file's age.
    monkeypatch.setattr(worker_module, "ALIVE_SECONDS", 0.05)
    alive = tmp_path / "worker.alive"
    stopping = threading.Event()
    thread = threading.Thread(
        target=serve,
        args=(services, get_settings(), stopping),
        kwargs={"grace": 1, "alive": alive},
        daemon=True,
    )
    thread.start()
    try:
        wait_until(alive.exists)
        touched = alive.stat().st_mtime_ns
        wait_until(lambda: alive.stat().st_mtime_ns > touched)
    finally:
        stopping.set()
    thread.join(timeout=10)
    assert not thread.is_alive()
    # Gone with the worker, so that the next one's check waits for it to run.
    assert not alive.exists()


def test_a_worker_whose_thread_ended_stops_to_be_started_again(
    session, services, tmp_path, monkeypatch, caplog
):
    # No thread ends before a stop unless something went wrong that the worker cannot see; a
    # worker a thread short would claim less or keep no leases, so it stops, and the command
    # exits with an error for whatever runs it to start a whole one.
    monkeypatch.setattr(worker_module, "ALIVE_SECONDS", 0.05)
    monkeypatch.setattr(Worker, "_housekeep", lambda self: None)
    alive = tmp_path / "worker.alive"
    stopping = threading.Event()
    # Should the worker not notice, the test ends instead of waiting for ever.
    timer = threading.Timer(10, stopping.set)
    timer.start()
    try:
        with caplog.at_level("ERROR", logger="app.worker"):
            assert serve(services, get_settings(), stopping, grace=1, alive=alive) is False
    finally:
        timer.cancel()
    assert "The worker's housekeeper thread stopped unexpectedly" in caplog.text
    assert not alive.exists()


def test_the_worker_command_refuses_an_alive_file_it_cannot_write(tmp_path):
    # At once, before it starts anything: a health check that fails while the worker runs
    # would send the operator looking in the wrong place.
    alive = tmp_path / "missing" / "worker.alive"
    with pytest.raises(SystemExit) as refusal:
        worker_module.main(["--alive-file", str(alive)])
    assert str(refusal.value) == (
        f"The --alive-file {alive} cannot be written: No such file or directory."
    )


def read_until(stream, marker: str, timeout: float) -> str:
    """What a process wrote to `stream` up to and including the line that holds `marker`."""
    deadline = time.monotonic() + timeout
    output = b""
    while marker.encode() not in output:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"The process did not report {marker!r} in time:\n{output.decode()}")
        ready, _, _ = wait_readable([stream], [], [], remaining)
        if ready:
            chunk = os.read(stream.fileno(), 4096)
            if not chunk:
                raise AssertionError(f"The process exited before it ran:\n{output.decode()}")
            output += chunk
    return output.decode()


def test_the_worker_command_starts_and_stops_on_sigterm(session, tmp_path):
    # The process a deployment runs: it starts with the same checks as the API (the refusals
    # are in test_config.py), says when it is running, keeps its alive file while it runs, and
    # SIGTERM stops it cleanly. The queue is empty here, so the real services it builds are
    # never called.
    alive = tmp_path / "worker.alive"
    process = subprocess.Popen(
        [sys.executable, "-m", "app.worker", "--grace", "1", "--alive-file", str(alive)],
        cwd=BACKEND_DIR,
        env=dict(os.environ),
        stderr=subprocess.PIPE,
    )
    try:
        output = read_until(process.stderr, "is running", timeout=60)
        wait_until(alive.exists)
        process.send_signal(signal.SIGTERM)
        _, rest = process.communicate(timeout=30)
        output += rest.decode()
    finally:
        process.kill()
    assert process.returncode == 0
    assert "Worker" in output and "stopped" in output
    assert "Traceback" not in output
    assert not alive.exists()


# Retries: a stage that failed because a provider was out of reach runs again after a backoff.

RATE_LIMITED = LLMError("The rate limit for m was reached. Try again shortly.", transient=True)
DECLINED = LLMError("The model declined to process this content.")


def run_index(job: Job, services) -> None:
    HANDLERS["index_document"].run(job, services)


def test_a_passing_failure_queues_the_stage_again_with_a_growing_backoff(
    client, session, services, make_document, sample_pdf
):
    document = make_document(sample_pdf)
    services.llm.failure = RATE_LIMITED
    run_index(claim("w:1", INDEX), services)

    # The document stays ready without summaries, the stage says why and when it runs again,
    # and the stage behind it runs again with it, as it does for "Index again".
    session.refresh(document)
    assert document.status == "ready" and document.error is None
    assert stage_statuses(document) == {
        "scan": "done",
        "parse": "done",
        "structure": "done",
        "enrich": "failed",
        "embed": "pending",
    }
    assert document.stages["enrich"]["detail"] == (
        f"{RATE_LIMITED} Retrying in 1 minute (1 of {MAX_RETRIES})."
    )
    first, retry = all_jobs(session)
    assert (first.status, retry.status) == ("running", "queued")
    assert retry.payload == {"document_id": document.id, "retry": 1}
    assert retry.priority == 0 and retry.attempts == 0
    assert retry.run_at - datetime.now(UTC) > timedelta(seconds=50)
    assert claim("w:1", INDEX) is None

    # The second retry waits twice as long.
    session.execute(text("UPDATE jobs SET run_at = now() WHERE id = :id"), {"id": retry.id})
    session.commit()
    taken = claim("w:1", INDEX)
    assert taken.id == retry.id
    run_index(taken, services)
    session.refresh(document)
    assert document.stages["enrich"]["detail"].endswith(
        f"Retrying in 2 minutes (2 of {MAX_RETRIES})."
    )
    second = all_jobs(session)[-1]
    assert second.payload == {"document_id": document.id, "retry": 2}
    assert second.run_at - datetime.now(UTC) > timedelta(seconds=110)

    # "Index again" meanwhile overtakes the waiting retry: one job, now, from the start.
    assert client.post(f"/api/documents/{document.id}/reprocess").status_code == 202
    assert [job.status for job in all_jobs(session)] == ["running", "running", "queued"]
    manual = all_jobs(session)[-1]
    assert manual.payload == {"document_id": document.id}
    assert manual.run_at <= datetime.now(UTC)
    session.refresh(document)
    assert document.status == "queued"
    # The scan passed and is not run again; every stage from parse on is.
    statuses = stage_statuses(document)
    assert statuses.pop("scan") == "done" and set(statuses.values()) == {"pending"}


def test_a_failure_that_will_not_pass_is_not_retried(session, services, make_document, sample_pdf):
    document = make_document(sample_pdf)
    services.llm.failure = DECLINED
    run_index(claim("w:1", INDEX), services)

    session.refresh(document)
    assert document.status == "ready"
    assert stage_statuses(document) == {
        "scan": "done",
        "parse": "done",
        "structure": "done",
        "enrich": "failed",
        "embed": "done",
    }
    assert document.stages["enrich"]["detail"] == str(DECLINED)
    assert len(all_jobs(session)) == 1


def test_a_required_stage_waits_for_its_retry_as_queued_and_gives_up_as_failed(
    session, services, make_document, tmp_path, caplog
):
    from PIL import Image

    path = tmp_path / "scan.png"
    Image.new("RGB", (400, 300), "white").save(path)
    document = make_document(path)
    services.llm.failure = RATE_LIMITED
    run_index(claim("w:1", INDEX), services)

    # Reading the scan needs the model, so nothing is indexed yet: the document is queued, as
    # a fresh upload is, until the retry.
    session.refresh(document)
    assert (document.status, document.error) == ("queued", None)
    assert stage_statuses(document) == {
        "scan": "done",
        "parse": "failed",
        "structure": "pending",
        "enrich": "pending",
        "embed": "pending",
    }
    assert document.stages["parse"]["detail"].endswith(
        f"Retrying in 1 minute (1 of {MAX_RETRIES})."
    )

    # The last retry fails too: the document is failed, and its error says it was tried. The
    # job that gave up ends failed with the reason, where the operator's dead-letter listing
    # and digest find it (tests/test_deadletters.py); the ones before it did their work.
    session.execute(
        text("UPDATE jobs SET run_at = now(), payload = payload || :retry WHERE status = 'queued'"),
        {"retry": f'{{"retry": {MAX_RETRIES}}}'},
    )
    session.commit()
    worker = Worker(services, concurrency=1)
    with caplog.at_level("ERROR", logger="app.worker"):
        worker._run(claim(worker.id, INDEX))
    session.refresh(document)
    assert document.status == "failed"
    assert document.error == (
        f'{RATE_LIMITED} Tried {MAX_RETRIES + 1} times over 15 minutes; use "Index again" '
        "once the service is back."
    )
    assert document.stages["parse"]["detail"] == document.error
    first, last = all_jobs(session)
    assert (first.status, last.status) == ("running", "failed")
    assert last.last_error == (
        f"Stage parse failed {MAX_RETRIES + 1} times over 15 minutes: {RATE_LIMITED}"
    )
    assert last.reported_at is None
    assert f"Document {document.id} is not tried again" in caplog.text


def test_the_worker_retries_until_the_outage_passes(
    session, services, make_document, sample_pdf, monkeypatch
):
    document = make_document(sample_pdf)
    services.llm.failure = RATE_LIMITED
    monkeypatch.setattr(worker_module, "FIRST_BACKOFF", timedelta(milliseconds=50))
    monkeypatch.setattr(worker_module, "POLL_SECONDS", 0.05)
    worker = Worker(services, concurrency=1)
    worker.start()
    try:
        wait_until(lambda: len(all_jobs(session)) == 2 and all_jobs(session)[0].status == "done")
        # The provider is back before the retry runs.
        services.llm.failure = None

        def enriched() -> bool:
            session.expire_all()
            return document.stages.get("enrich", {}).get("status") == "done"

        wait_until(enriched)
        wait_until(lambda: all_jobs(session)[1].status == "done")
    finally:
        worker.stop()
    session.refresh(document)
    assert document.status == "ready"
    assert set(stage_statuses(document).values()) == {"done"}
    assert "Retrying" not in document.stages["enrich"]["detail"]
    assert [job.payload.get("retry", 0) for job in all_jobs(session)] == [0, 1]
