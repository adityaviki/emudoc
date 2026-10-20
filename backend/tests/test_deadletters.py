"""The dead-letter queue: the jobs the worker gave up on, the digest that reports each of them
to the operator once, and the command that lists them and puts their documents back in the
queue. How a job comes to end failed is tested with the worker, in tests/test_jobs.py.
"""

import pytest
from sqlalchemy import select, text

import app.deadletters as deadletters
import app.worker as worker_module
from app.config import get_settings
from app.deadletters import FAILED_JOBS_COMMAND, alert_failed_jobs
from app.jobs import MAX_ATTEMPTS, queue_index
from app.mail import MailError, Message
from app.maintenance import main
from app.models import Job, Project
from app.worker import Worker
from tests.conftest import FakeMailer
from tests.test_jobs import all_jobs, lose, wait_until
from tests.test_pipeline import stage_statuses

OPERATOR = "ops@example.test"


def fail(session, job_id: int, error: str) -> None:
    """Leave a job as the worker leaves one it gave up on."""
    session.execute(
        text(
            "UPDATE jobs SET status = 'failed', last_error = :error, finished_at = now(), "
            "lease_until = NULL, worker_id = 'w:1' WHERE id = :id"
        ),
        {"id": job_id, "error": error},
    )
    session.commit()


def job_of(session, document) -> Job:
    session.expire_all()
    return session.scalar(select(Job).where(Job.payload["document_id"].as_integer() == document.id))


def test_the_digest_reports_each_failure_once_and_names_what_failed(
    session, account, make_account, make_document, sample_pdf, sample_docx, monkeypatch
):
    mine = make_document(sample_pdf)
    other = make_account("x@other.test")
    theirs_project = Project(org_id=other.organization.id, name="Theirs")
    session.add(theirs_project)
    session.flush()
    theirs = make_document(sample_docx, project=theirs_project)
    fail(session, job_of(session, mine).id, f"Given up after {MAX_ATTEMPTS} lost attempts")
    fail(session, job_of(session, theirs).id, "RuntimeError: the pipeline itself broke")
    mailer = FakeMailer()

    # Without an address nothing is sent and nothing is marked: the failures wait for one.
    assert alert_failed_jobs(mailer, "") == 0
    assert mailer.sent == []
    assert {job.reported_at for job in all_jobs(session)} == {None}

    assert alert_failed_jobs(mailer, OPERATOR) == 2
    ((to, subject, body),) = mailer.sent
    assert (to, subject) == (OPERATOR, "Emudoc gave up on 2 jobs")
    slug = account.organization.slug
    mine_job, theirs_job = job_of(session, mine).id, job_of(session, theirs).id
    assert (
        f"job {mine_job} (index_document)  {slug} / Test project / {sample_pdf.name}\n"
        f"    Given up after {MAX_ATTEMPTS} lost attempts\n"
    ) in body
    assert (
        f"job {theirs_job} (index_document)  other / Theirs / {sample_docx.name}\n"
        "    RuntimeError: the pipeline itself broke\n"
    ) in body
    assert f"List them all with `{FAILED_JOBS_COMMAND}` in the backend directory" in body
    assert all(job.reported_at is not None for job in all_jobs(session))

    # Reported once: the next round has nothing to say.
    assert alert_failed_jobs(mailer, OPERATOR) == 0
    assert len(mailer.sent) == 1

    # New failures make a new digest, which lists at most DIGEST_LIMIT of them in full, oldest
    # first, and names a document that was deleted since by its id.
    second = Project(org_id=account.organization.id, name="Second")
    session.add(second)
    session.flush()
    older = make_document(sample_pdf, project=second)
    newer = make_document(sample_docx, project=second)
    fail(session, job_of(session, older).id, "Stage enrich failed 5 times over 15 minutes: down")
    older_job = job_of(session, older).id
    fail(session, job_of(session, newer).id, "RuntimeError: again")
    session.delete(older)
    session.commit()
    monkeypatch.setattr(deadletters, "DIGEST_LIMIT", 1)
    assert alert_failed_jobs(mailer, OPERATOR) == 2
    _, subject, body = mailer.sent[-1]
    assert subject == "Emudoc gave up on 2 jobs"
    assert f"job {older_job} (index_document)  {slug} / Second / document {older.id} (deleted)" in (
        body
    )
    assert "Stage enrich failed 5 times over 15 minutes: down\n\nand 1 more.\n" in body
    assert "RuntimeError: again" not in body


def test_a_relay_that_is_down_leaves_the_failures_for_the_next_round(
    session, make_document, sample_pdf
):
    document = make_document(sample_pdf)
    fail(session, job_of(session, document).id, "RuntimeError: boom")

    class DownMailer:
        def send(self, message: Message) -> None:
            raise MailError("The email could not be sent.")

    with pytest.raises(MailError):
        alert_failed_jobs(DownMailer(), OPERATOR)
    assert job_of(session, document).reported_at is None

    mailer = FakeMailer()
    assert alert_failed_jobs(mailer, OPERATOR) == 1
    assert len(mailer.sent) == 1
    assert job_of(session, document).reported_at is not None


def test_the_command_lists_and_requeues_the_failed_jobs(
    session, make_document, sample_pdf, sample_docx, capsys
):
    # Crashed in the structure stage: the requeue starts there, keeping the parse.
    crashed = make_document(sample_pdf)
    crashed.status = "failed"
    crashed.error = worker_module.CRASHED
    crashed.stages = {
        "scan": {"status": "done", "detail": "clean"},
        "parse": {"status": "done", "detail": ""},
        "structure": {"status": "running", "detail": ""},
    }
    project = session.get(Project, crashed.project_id)
    # Deleted since it failed; nothing is left to run.
    deleted = make_document(sample_docx, project=project)
    # The user pressed "Index again" meanwhile: that job carries the document now.
    other = Project(org_id=project.org_id, name="Other")
    session.add(other)
    session.flush()
    requeued = make_document(sample_pdf, "again.pdf", project=other)
    session.commit()
    fail(session, job_of(session, crashed).id, "RuntimeError: the pipeline itself broke")
    fail(session, job_of(session, deleted).id, "RuntimeError: the pipeline itself broke")
    fail(session, job_of(session, requeued).id, f"Given up after {MAX_ATTEMPTS} lost attempts")
    crashed_job, deleted_job, old_job = (job.id for job in all_jobs(session))
    session.delete(deleted)
    user_job = queue_index(session, requeued).id
    session.commit()

    main(["failed-jobs"])
    out = capsys.readouterr().out
    assert out.startswith("Found 3 failed jobs from the last 30 days:\n")
    assert (
        f"job {crashed_job} (index_document)  local / Test project / {sample_pdf.name}\n"
        "    RuntimeError: the pipeline itself broke\n"
    ) in out
    assert (
        f"job {deleted_job} (index_document)  local / Test project / document {deleted.id} "
        "(deleted)\n"
    ) in out
    assert f"job {old_job} (index_document)  local / Other / again.pdf\n" in out
    assert out.endswith(
        "Run again with --requeue to index their documents again from the stage they stopped at.\n"
    )

    main(["failed-jobs", "--requeue"])
    assert capsys.readouterr().out == (
        f"Queued local / Test project / {sample_pdf.name} again from stage structure "
        f"(job {crashed_job}).\n"
        f"Dropped job {deleted_job}: its document was deleted.\n"
        f"Dropped job {old_job}: local / Other / again.pdf is queued already.\n"
        "1 document queued again; the worker indexes them while it runs, so start the app if "
        "it is stopped.\n"
    )
    session.refresh(crashed)
    assert (crashed.status, crashed.error) == ("queued", None)
    assert stage_statuses(crashed) == {
        "scan": "done",
        "parse": "done",
        "structure": "pending",
        "enrich": "pending",
        "embed": "pending",
    }
    jobs = all_jobs(session)
    assert [(job.status, job.payload["document_id"]) for job in jobs] == [
        ("queued", requeued.id),
        ("queued", crashed.id),
    ]
    assert jobs[0].id == user_job

    main(["failed-jobs"])
    assert capsys.readouterr().out == (
        "No failed jobs: the worker has given up on nothing in the last 30 days.\n"
    )


def test_the_worker_mails_the_jobs_it_gave_up_on(
    session, services, make_document, sample_pdf, monkeypatch, caplog
):
    document = make_document(sample_pdf)
    (job,) = all_jobs(session)
    lose(session, job.id, attempts=MAX_ATTEMPTS)
    document.status = "processing"
    session.commit()
    monkeypatch.setattr(get_settings(), "alert_email", OPERATOR)
    monkeypatch.setattr(worker_module, "ALERT_INTERVAL", 0.05)
    mailer = FakeMailer()

    worker = Worker(services, concurrency=1, mailer=mailer)
    worker.start()
    try:
        assert "alerter" in {thread.name for thread in worker._threads}
        wait_until(lambda: bool(mailer.sent))
    finally:
        worker.stop()
    ((to, subject, body),) = mailer.sent
    assert (to, subject) == (OPERATOR, "Emudoc gave up on 1 job")
    assert f"Given up after {MAX_ATTEMPTS} lost attempts" in body
    assert all_jobs(session)[0].reported_at is not None

    # Without an address there is no alerter, and a deployment is told so.
    monkeypatch.setattr(get_settings(), "alert_email", "")
    monkeypatch.setattr(get_settings(), "environment", "production")
    worker = Worker(services, concurrency=1, mailer=mailer)
    with caplog.at_level("WARNING", logger="app.worker"):
        worker.start()
    worker.stop()
    assert "alerter" not in {thread.name for thread in worker._threads}
    assert "ALERT_EMAIL is not set" in caplog.text
