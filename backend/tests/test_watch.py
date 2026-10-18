"""The worker's watch (app/watch.py): what it sees go wrong, written to the operator once when it
begins and once when it ends, whichever worker looks, and kept while it cannot be seen."""

import threading
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

import app.watch as watch_module
import app.worker as worker_module
from app.config import get_settings
from app.db import SessionLocal, engine
from app.jobs import ANSWER_QUESTION
from app.mail import MailError
from app.models import Chat, ChatMessage, Job, Project, UsageEvent, WatchAlert
from app.postgres import Archive
from app.scanning import ScanError
from app.spending import ALERT_LOCK
from app.storage import StorageError
from app.watch import watch
from app.worker import Worker
from tests.conftest import FakeEmbedder, FakeMailer
from tests.test_jobs import wait_until

OPERATOR = "ops@example.test"


@pytest.fixture
def mailer() -> FakeMailer:
    return FakeMailer()


def raised(session) -> dict[str, WatchAlert]:
    session.expire_all()
    return {alert.key: alert for alert in session.scalars(select(WatchAlert))}


def backdate(session, job_id: int, delta: timedelta) -> None:
    session.execute(
        text("UPDATE jobs SET run_at = now() - :delta WHERE id = :id"),
        {"id": job_id, "delta": delta},
    )
    session.commit()


def settle(session, document, status: str, stages: dict[str, str]) -> None:
    """Leave a document as an indexing run that ended a moment ago left it."""
    document.status = status
    document.stages = {name: {"status": state, "detail": ""} for name, state in stages.items()}
    session.execute(
        text(
            "UPDATE jobs SET status = 'done', finished_at = now(), lease_until = NULL "
            "WHERE payload->>'document_id' = :id"
        ),
        {"id": str(document.id)},
    )
    session.commit()


def test_a_backlog_is_written_when_it_begins_and_when_it_ends(
    session, services, mailer, make_document, sample_pdf
):
    document = make_document(sample_pdf)
    job = session.scalar(select(Job))

    # Without an address nothing is looked at, and nothing is raised.
    backdate(session, job.id, timedelta(hours=2))
    assert watch(services, mailer, "") == 0
    assert mailer.sent == [] and raised(session) == {}

    assert watch(services, mailer, OPERATOR) == 1
    ((to, subject, body),) = mailer.sent
    assert (to, subject) == (OPERATOR, "Emudoc alert: documents wait for a worker")
    assert body.startswith(
        "Begun:\n\n- The oldest document waiting to be indexed has waited 2 h for a worker, "
        "with 1 due in all: the workers' indexer threads are all busy, or no worker is "
        "claiming. Look at the workers' logs, then add workers or raise WORKER_CONCURRENCY"
    )
    assert body.endswith('begins or ends (deploying.md, "Alerts").\n')
    assert list(raised(session)) == ["backlog:index_document"]

    # It goes on: nobody is written to again.
    assert watch(services, mailer, OPERATOR) == 0
    assert len(mailer.sent) == 1

    settle(session, document, "ready", {"parse": "done"})
    assert watch(services, mailer, OPERATOR) == 1
    _, subject, body = mailer.sent[-1]
    assert subject == "Emudoc alert over: documents wait for a worker"
    assert body.startswith("Over:\n\n- documents wait for a worker, after 0 s, since ")
    assert raised(session) == {}


def test_a_question_waits_less_than_a_document_before_it_counts(session, services, mailer, account):
    project = Project(org_id=account.organization.id, name="Questions")
    session.add(project)
    session.flush()
    question = Job(
        org_id=account.organization.id,
        project_id=project.id,
        kind=ANSWER_QUESTION,
        payload={"message_id": 1},
    )
    session.add(question)
    session.commit()
    backdate(session, question.id, timedelta(minutes=4))
    assert watch(services, mailer, OPERATOR) == 0
    backdate(session, question.id, timedelta(minutes=6))
    assert watch(services, mailer, OPERATOR) == 1
    assert mailer.sent[0][1] == "Emudoc alert: questions wait for a worker"
    assert "raise ANSWER_CONCURRENCY" in mailer.sent[0][2]


def test_failing_indexing_is_written_but_not_a_member_s_few_broken_files(
    session, services, mailer, make_document, tmp_path
):
    files = [tmp_path / f"note-{number}.txt" for number in range(10)]
    for number, path in enumerate(files):
        path.write_text(f"Delivery note {number}.")
    first = make_document(files[0])
    project = session.get(Project, first.project_id)
    documents = [first] + [make_document(path, project=project) for path in files[1:]]
    for document in documents[:4]:
        settle(session, document, "failed", {"scan": "done", "parse": "failed"})
    for document in documents[4:]:
        settle(session, document, "ready", {"parse": "done", "enrich": "done"})
    assert watch(services, mailer, OPERATOR) == 0

    # A model that refuses every request leaves the documents ready without their summaries.
    for document in documents[4:6]:
        settle(session, document, "ready", {"parse": "done", "enrich": "failed"})
    # One waiting for a retry has not settled, whatever its stages say meanwhile.
    waiting = documents[6]
    settle(session, waiting, "ready", {"parse": "done", "embed": "failed"})
    session.add(
        Job(
            org_id=project.org_id,
            project_id=project.id,
            kind="index_document",
            payload={"document_id": waiting.id},
            run_at=datetime.now(UTC) + timedelta(minutes=2),
        )
    )
    session.commit()
    assert watch(services, mailer, OPERATOR) == 1
    _, subject, body = mailer.sent[0]
    assert subject == "Emudoc alert: indexing fails"
    assert (
        "Of the 9 documents indexed in the last hour, 6 have a stage that failed (enrich 2, "
        "parse 4)."
    ) in body

    # An hour on, those runs are out of the window, and the concern is over.
    session.execute(text("UPDATE jobs SET finished_at = now() - interval '61 minutes'"))
    session.commit()
    assert watch(services, mailer, OPERATOR) == 1
    assert mailer.sent[-1][1] == "Emudoc alert over: indexing fails"


def answer(session, chat: Chat, status: str) -> None:
    message = ChatMessage(chat_id=chat.id, role="assistant", content="", status=status)
    session.add(message)
    session.flush()
    session.add(
        Job(
            org_id=session.get(Project, chat.project_id).org_id,
            project_id=chat.project_id,
            kind=ANSWER_QUESTION,
            payload={"message_id": message.id},
            status="done",
            finished_at=datetime.now(UTC),
        )
    )
    session.commit()


def test_failing_answers_are_written(session, services, mailer, account):
    project = Project(org_id=account.organization.id, name="Answers")
    session.add(project)
    session.flush()
    chat = Chat(project_id=project.id, user_id=account.user.id, title="Notice")
    session.add(chat)
    session.commit()
    for status in ["done"] * 14 + ["failed"] * 4 + ["stopped"]:
        answer(session, chat, status)
    assert watch(services, mailer, OPERATOR) == 0
    answer(session, chat, "failed")
    assert watch(services, mailer, OPERATOR) == 1
    _, subject, body = mailer.sent[0]
    assert subject == "Emudoc alert: answers fail"
    assert "Of the 20 answers written in the last hour, 5 failed." in body


def test_a_fallback_that_answers_for_its_primary_is_written_with_where_to_look(
    session, services, mailer, account, monkeypatch
):
    settings = get_settings()
    monkeypatch.setattr(settings, "agent_fallback_model", "anthropic:claude-haiku-5-5")
    monkeypatch.setattr(settings, "index_fallback_model", "openai:gpt-5.4-mini")

    def used(model: str, kind: str, calls: int, minutes_ago: int = 1) -> None:
        session.add(
            UsageEvent(
                org_id=account.organization.id,
                kind=kind,
                unit="calls",
                quantity=calls,
                model=model,
                occurred_at=datetime.now(UTC) - timedelta(minutes=minutes_ago),
            )
        )
        session.commit()

    # The primaries answering, and the fallback an hour and more ago, are nothing to say.
    used(settings.agent_model, "answer", 30)
    used(settings.index_model, "enrich", 30)
    used("anthropic:claude-haiku-5-5", "answer", 2, minutes_ago=61)
    assert watch(services, mailer, OPERATOR) == 0

    used("anthropic:claude-haiku-5-5", "answer", 3)
    used("anthropic:claude-haiku-5-5", "answer", 4)
    # The agent's fallback doing indexing work would be INDEX_MODEL's business, not its own.
    used("anthropic:claude-haiku-5-5", "ocr", 9)
    assert watch(services, mailer, OPERATOR) == 1
    _, subject, body = mailer.sent[0]
    assert subject == "Emudoc alert: AGENT_MODEL fails over to its fallback"
    assert (
        "AGENT_FALLBACK_MODEL anthropic:claude-haiku-5-5 answered 7 requests for the chats in "
        f"the last hour, each one AGENT_MODEL {settings.agent_model} failed. The members "
        "notice nothing until the fallback fails too."
    ) in body
    assert '"was not found, or is served no longer" is a model its provider retired' in body

    used("openai:gpt-5.4-mini", "infer_sections", 1)
    assert watch(services, mailer, OPERATOR) == 1
    assert mailer.sent[-1][1] == "Emudoc alert: INDEX_MODEL fails over to its fallback"
    assert "Still on:\n\n- AGENT_MODEL fails over to its fallback, since " in mailer.sent[-1][2]


def test_services_that_do_not_answer_are_written_together(session, services, mailer, monkeypatch):
    services.embedder.fail = True
    services.scanner.outage = ScanError(
        "The malware scanner at clamd://clamav:3310 cannot be reached (Connection refused).",
        transient=True,
    )

    def refused(*, writing: bool = True) -> None:
        raise StorageError("The bucket 'docs' cannot be used (AccessDenied).")

    monkeypatch.setattr(services.storage, "check", refused)
    assert watch(services, mailer, OPERATOR) == 3
    _, subject, body = mailer.sent[0]
    assert subject == "Emudoc alerts: 3 begun"
    assert (
        "- The embeddings endpoint at http://embeddings.test/v1 does not answer: "
        f"{FakeEmbedder.UNREACHABLE} Searches match keywords only"
    ) in body
    assert "- The malware scanner at clamd://clamav:3310 cannot be reached" in body
    assert "- The bucket 'docs' cannot be used (AccessDenied). Uploads are refused" in body

    services.embedder.fail = False
    services.scanner.outage = None
    monkeypatch.undo()
    assert watch(services, mailer, OPERATOR) == 3
    _, subject, body = mailer.sent[-1]
    assert subject == "Emudoc alerts: 3 over"
    assert "- the storage cannot be used, after " in body


def test_the_database_server_is_watched(session, services, mailer, monkeypatch):
    # A transaction left open past LONG_TRANSACTION, here at once.
    monkeypatch.setattr("app.postgres.LONG_TRANSACTION", timedelta(0))
    taken = datetime(2026, 10, 18, 9, 30, tzinfo=UTC)
    stuck = Archive(
        archived=120,
        failed=7,
        archived_at=taken - timedelta(minutes=12),
        failed_at=taken - timedelta(seconds=30),
        pending=3,
        taken_at=taken,
    )
    monkeypatch.setattr(watch_module, "archive", lambda connection: stuck)
    with engine.connect() as other:
        other.execute(text("SELECT 1"))
        assert watch(services, mailer, OPERATOR) == 3
        other.rollback()
    _, subject, body = mailer.sent[0]
    assert subject == "Emudoc alerts: 3 begun"
    assert "- A transaction has been open for 0 s (idle in transaction)" in body
    assert (
        "- The database has failed to archive its write-ahead log since 2026-10-18 09:29 UTC, "
        "7 attempts in all since its statistics were reset"
    ) in body
    assert (
        "- 3 finished segments of the write-ahead log have waited 12 min to be archived, past "
        "the recovery point of 5 minutes"
    ) in body
    assert set(raised(session)) == {
        "database:long_transaction",
        "archive:failing",
        "archive:behind",
    }
    assert raised(session)["archive:failing"].title == "the database fails to archive its log"
    # The test's own reading was a transaction, which would be the next long one.
    session.rollback()

    # A server that archives nothing has nothing of the archive to be wrong.
    monkeypatch.setattr(watch_module, "archive", lambda connection: None)
    assert watch(services, mailer, OPERATOR) == 3
    assert mailer.sent[-1][1] == "Emudoc alerts: 3 over"


def test_a_check_that_fails_keeps_what_it_raised(
    session, services, mailer, make_document, sample_pdf, monkeypatch, caplog
):
    make_document(sample_pdf)
    backdate(session, session.scalar(select(Job.id)), timedelta(hours=2))
    assert watch(services, mailer, OPERATOR) == 1

    def broken(services, settings):
        raise RuntimeError("the statement was refused")

    monkeypatch.setitem(watch_module.CHECKS, "backlog:", broken)
    session.execute(text("DELETE FROM jobs"))
    session.commit()
    with caplog.at_level("WARNING", logger="app.watch"):
        assert watch(services, mailer, OPERATOR) == 0
    assert list(raised(session)) == ["backlog:index_document"]
    assert "The watch could not check broken, and keeps what it raised of it" in caplog.text
    # The message is the exception's, which is withheld like the log's.
    assert "the statement was refused" not in caplog.text


def test_a_relay_that_is_down_leaves_the_alert_for_the_next_round(session, services):
    services.embedder.fail = True

    class Down(FakeMailer):
        def send(self, to, subject, body):
            raise MailError("The mail relay could not be reached.")

    with pytest.raises(MailError):
        watch(services, Down(), OPERATOR)
    assert raised(session) == {}
    mailer = FakeMailer()
    assert watch(services, mailer, OPERATOR) == 1


def test_two_workers_looking_at_once_write_once(session, services):
    services.embedder.fail = True
    mailers = [FakeMailer(), FakeMailer()]
    with SessionLocal() as holder:
        holder.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": ALERT_LOCK})
        threads = [
            threading.Thread(target=watch, args=(services, mailer, OPERATOR)) for mailer in mailers
        ]
        for thread in threads:
            thread.start()
        holder.rollback()
    for thread in threads:
        thread.join(timeout=10)
    assert sum(len(mailer.sent) for mailer in mailers) == 1


def test_the_worker_watches_from_its_alerter_thread(session, services, mailer, monkeypatch):
    services.embedder.fail = True
    monkeypatch.setattr(get_settings(), "alert_email", OPERATOR)
    monkeypatch.setattr(worker_module, "ALERT_INTERVAL", 0.05)
    worker = Worker(services, concurrency=1, mailer=mailer)
    worker.start()
    try:
        wait_until(lambda: bool(mailer.sent))
    finally:
        worker.stop()
    assert mailer.sent[0][1] == "Emudoc alert: the embeddings endpoint does not answer"
