"""The worker's watch: what goes wrong in the database and in the services the worker reaches,
mailed to ALERT_EMAIL once when it begins and once when it ends.

A deployment with a scraper and Alertmanager has rules on the metrics for all of this and more
(deploy/prometheus/alerts.yml), but one without, a single machine or AWS as `deploy/aws` builds
it, would hear of nothing: a revoked key behind a fallback is a line in the worker's log, and
the members notice nothing until the fallback fails too. So the worker's alerter thread
(app/worker.py) runs `watch` every ALERT_INTERVAL beside the dead letters, the spending and the
quarantines. Each check (`CHECKS`) returns the concerns it sees now, keyed by what each is
about; the keys raised and not yet over are rows of `watch_alerts`, so that the operator is
written to when a concern begins and when it ends, not every round, whichever worker looks.

It sees what any worker sees alike: the queue, the documents and answers that settled in the
last WINDOW, the usage events, the database server's own state (app/postgres.py) and whether
the embeddings endpoint, the scanner and the storage answer. What one process alone knows (its
pool, its threads, its listener) and what needs a view from outside (a worker that is gone, an
API that is down) is the metrics' and the rules', and on AWS the host's (deploy/aws/host/watch).

A check that fails, say a database statement the role may not run, is logged and keeps the
alerts it raised before: a concern that cannot be seen is not over.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.config import AGENT, INDEX, Settings, get_settings
from app.db import SessionLocal
from app.errors import AppError, summary
from app.indexing.pipeline import Services
from app.jobs import ANSWER_QUESTION, INDEX_DOCUMENT
from app.mail import Mailer
from app.models import WatchAlert
from app.postgres import archive, database_report, findings, span
from app.spending import ALERT_LOCK
from app.usage import ANSWER, ENRICH, INFER_SECTIONS, OCR

log = logging.getLogger(__name__)

# How long the oldest due job of each kind may wait for a worker before the operator hears of
# it: a question is a member waiting at the screen; a document, one who uploaded a batch.
BACKLOG = {ANSWER_QUESTION: timedelta(minutes=5), INDEX_DOCUMENT: timedelta(hours=1)}
# The span the rates are measured over: the documents and answers that settled in it, and the
# requests a fallback answered. A concern is over once a whole span has passed without it.
WINDOW = timedelta(hours=1)
# How many documents of those indexed in WINDOW must have a stage that failed, and what share of
# them, before indexing counts as failing; a member's batch of broken files is a few, and their
# own to fix. Answers fail for the members' files far less, so a smaller share counts.
FAILED_DOCUMENTS = (5, 0.5)
FAILED_ANSWERS = (5, 0.25)
# The work each role's models do, as the usage events name it (app/usage.py).
ROLE_KINDS = {INDEX: (OCR, INFER_SECTIONS, ENRICH), AGENT: (ANSWER,)}
ROLE_WORK = {INDEX: "indexing", AGENT: "the chats"}
DATABASE_TITLES = {
    "pgvector": "the database's pgvector is too old",
    "timeouts": "the database role has no timeouts",
    "long_transaction": "a transaction has been open for over an hour",
}


@dataclass(frozen=True)
class Concern:
    """Something wrong, as the operator reads it: a few words for the subject and the lists,
    and the sentence that says what is wrong and what to do."""

    title: str
    message: str


Check = Callable[[Services, Settings], dict[str, Concern]]


def backlog(services: Services, settings: Settings) -> dict[str, Concern]:
    with SessionLocal() as session:
        rows = session.execute(
            text(
                "SELECT kind, count(*) AS due, now() - min(run_at) AS waited FROM jobs "
                "WHERE status = 'queued' AND run_at <= now() AND kind = ANY(:kinds) "
                "GROUP BY kind"
            ),
            {"kinds": list(BACKLOG)},
        ).all()
    found = {}
    for kind, due, waited in rows:
        if waited <= BACKLOG[kind]:
            continue
        if kind == ANSWER_QUESTION:
            what, threads, setting = "questions wait for a worker", "answerer", "ANSWER_CONCURRENCY"
            oldest = "The oldest question waiting for its answer"
        else:
            what, threads, setting = "documents wait for a worker", "indexer", "WORKER_CONCURRENCY"
            oldest = "The oldest document waiting to be indexed"
        found[f"backlog:{kind}"] = Concern(
            what,
            f"{oldest} has waited {span(waited)} for a worker, with {due} due in all: the "
            f"workers' {threads} threads are all busy, or no worker is claiming. Look at the "
            f"workers' logs, then add workers or raise {setting} if they are busy.",
        )
    return found


_SETTLED_DOCUMENTS = text(
    "WITH ran AS (SELECT DISTINCT (payload ->> 'document_id')::bigint AS id FROM jobs "
    "WHERE kind = 'index_document' AND status IN ('done', 'failed') "
    "AND finished_at > now() - :window) "
    "SELECT d.id, d.status, d.stages FROM ran JOIN documents d USING (id) "
    "WHERE d.status IN ('ready', 'failed') AND NOT EXISTS (SELECT 1 FROM jobs j "
    "WHERE j.kind = 'index_document' AND j.status IN ('queued', 'running') "
    "AND (j.payload ->> 'document_id')::bigint = d.id)"
)


def documents(services: Services, settings: Settings) -> dict[str, Concern]:
    """Of the documents whose indexing ended in WINDOW, those with a stage that failed. One
    waiting for a retry has a job queued and is left out until it settles, and an optional
    stage that failed (a summary, a vector) leaves the document ready, which is how a model
    that refuses every request shows when it has no fallback."""
    with SessionLocal() as session:
        rows = session.execute(_SETTLED_DOCUMENTS, {"window": WINDOW}).all()
    stages: dict[str, int] = {}
    failed = 0
    for _, status, stage_states in rows:
        names = [name for name, state in stage_states.items() if state.get("status") == "failed"]
        if not names and status != "failed":
            continue
        failed += 1
        # A document failed with no stage to blame is the pipeline's own failure.
        for name in names or ["the pipeline itself"]:
            stages[name] = stages.get(name, 0) + 1
    least, share = FAILED_DOCUMENTS
    if failed < least or failed < share * len(rows):
        return {}
    by_stage = ", ".join(f"{name} {count}" for name, count in sorted(stages.items()))
    return {
        "documents": Concern(
            "indexing fails",
            f"Of the {len(rows)} documents indexed in the last {_window()}, {failed} have a "
            f"stage that failed ({by_stage}). Each document's stages say why to its members; "
            "a model's or the embeddings endpoint's failure is worded in the worker's log, and "
            "`python -m app.maintenance models` asks every model what indexing asks of it.",
        )
    }


_SETTLED_ANSWERS = text(
    "SELECT count(*) AS answered, count(*) FILTER (WHERE m.status = 'failed') AS failed "
    "FROM jobs j JOIN chat_messages m ON m.id = (j.payload ->> 'message_id')::bigint "
    "WHERE j.kind = 'answer_question' AND j.status IN ('done', 'failed') "
    "AND j.finished_at > now() - :window"
)


def answers(services: Services, settings: Settings) -> dict[str, Concern]:
    with SessionLocal() as session:
        answered, failed = session.execute(_SETTLED_ANSWERS, {"window": WINDOW}).one()
    least, share = FAILED_ANSWERS
    if failed < least or failed < share * answered:
        return {}
    return {
        "answers": Concern(
            "answers fail",
            f"Of the {answered} answers written in the last {_window()}, {failed} failed. "
            "The worker's log says why in a line from app.llm or app.answers: a model or its "
            "provider refusing (`python -m app.maintenance models` asks every model), or a "
            "daily spending limit reached, which mails a warning of its own.",
        )
    }


def fallbacks(services: Services, settings: Settings) -> dict[str, Concern]:
    """The requests a fallback answered in WINDOW, from the usage events, which name the model
    that answered: each is a request its primary failed, and nobody but the log sees it."""
    found = {}
    for role, kinds in ROLE_KINDS.items():
        primary, *rest = settings.models_for(role)
        if not rest:
            continue
        fallback = rest[0]
        with SessionLocal() as session:
            answered = session.scalar(
                text(
                    "SELECT coalesce(sum(quantity), 0) FROM usage_events "
                    "WHERE occurred_at > now() - :window AND unit = 'calls' AND model = :model "
                    "AND kind = ANY(:kinds)"
                ),
                {"window": WINDOW, "model": fallback.name, "kinds": list(kinds)},
            )
        if not answered:
            continue
        found[f"fallback:{fallback.variable}"] = Concern(
            f"{primary.variable} fails over to its fallback",
            f"{fallback.variable} {fallback.name} answered {answered} "
            f"{'request' if answered == 1 else 'requests'} for {ROLE_WORK[role]} in the last "
            f"{_window()}, each one {primary.variable} {primary.name} failed. The members "
            "notice nothing until the fallback fails too. The worker's log says why in a line "
            f'from app.llm: "was not found, or is served no longer" is a model its provider '
            'retired, "was rejected" a key revoked or out of credit; `python -m '
            "app.maintenance models` asks every model.",
        )
    return found


def services_answer(services: Services, settings: Settings) -> dict[str, Concern]:
    """Whether the embeddings endpoint, the scanner and the storage answer the worker, asked as
    the start asks them. Their sentences are the app's own and name no document."""
    found = {}
    if reason := services.embedder.check():
        found["embeddings"] = Concern(
            "the embeddings endpoint does not answer",
            f"The embeddings endpoint at {services.embedder.address} does not answer: {reason} "
            "Searches match keywords only, and new documents wait to be embedded, tried again "
            'for a quarter of an hour (deploying.md, "The embeddings endpoint").',
        )
    if services.scanner is not None:
        try:
            services.scanner.check()
        except AppError as error:
            found["scanner"] = Concern(
                "the malware scanner does not answer",
                f"{error} Uploads wait in the queue, unscanned and unserved, and are tried "
                "again until it answers.",
            )
    try:
        services.storage.check()
    except AppError as error:
        found["storage"] = Concern(
            "the storage cannot be used",
            f"{error} Uploads are refused and the files cannot be served meanwhile.",
        )
    return found


def database(services: Services, settings: Settings) -> dict[str, Concern]:
    """What the `database` command flags (app/postgres.py), every table looked at, and the
    archiving of the write-ahead log, which the backups' recovery point rests on."""
    with SessionLocal() as session:
        connection = session.connection()
        report = database_report(connection, limit=None)
        archiving = archive(connection)
    found = {}
    for key, message in findings(report, settings).items():
        kind, _, table = key.partition(":")
        title = f"autovacuum is behind on {table}" if table else DATABASE_TITLES[kind]
        found[f"database:{key}"] = Concern(title, message)
    if archiving is None:
        return found
    if archiving.failing:
        found["archive:failing"] = Concern(
            "the database fails to archive its log",
            f"The database has failed to archive its write-ahead log since "
            f"{_at(archiving.failed_at)}, {archiving.failed} attempts in all since its "
            "statistics were reset: the backups miss every change since. Run `pgbackrest "
            '--stanza=emudoc check` and read the server\'s log (deploying.md, "Backups").',
        )
    if (behind := archiving.behind) is not None:
        found["archive:behind"] = Concern(
            "the database's archive falls behind",
            f"{archiving.pending} finished segments of the write-ahead log have waited "
            f"{span(behind)} to be archived, past the recovery point of 5 minutes: a "
            "restore now would lose the changes in them. Look at the archiver's progress in "
            'pg_stat_archiver and the repository\'s disk or bucket (deploying.md, "Backups").',
        )
    return found


# Each check with the prefix of the keys it raises, by which a failed check keeps them.
CHECKS: dict[str, Check] = {
    "backlog:": backlog,
    "documents": documents,
    "answers": answers,
    "fallback:": fallbacks,
    "embeddings": services_answer,
    "scanner": services_answer,
    "storage": services_answer,
    "database:": database,
    "archive:": database,
}


def look(services: Services, settings: Settings) -> tuple[dict[str, Concern], set[str]]:
    """Every concern the checks see now, and the key prefixes of the checks that failed."""
    found: dict[str, Concern] = {}
    failed: set[str] = set()
    for check in dict.fromkeys(CHECKS.values()):
        try:
            found |= check(services, settings)
        except Exception as error:
            prefixes = {prefix for prefix, each in CHECKS.items() if each is check}
            failed |= prefixes
            log.warning(
                "The watch could not check %s, and keeps what it raised of it: %s",
                check.__name__,
                summary(error).splitlines()[0],
            )
    return found, failed


def watch_email(
    begun: list[Concern], over: list[WatchAlert], still: list[WatchAlert], now: datetime
) -> tuple[str, str]:
    """The message: the subject names the one change, or counts them; the body says what began,
    in full, what ended and after how long, and what goes on."""
    if len(begun) + len(over) == 1:
        subject = (
            f"Emudoc alert: {begun[0].title}" if begun else f"Emudoc alert over: {over[0].title}"
        )
    else:
        parts = [f"{len(begun)} begun"] if begun else []
        parts += [f"{len(over)} over"] if over else []
        subject = f"Emudoc alerts: {', '.join(parts)}"
    sections = []
    if begun:
        sections.append("Begun:\n\n" + "\n\n".join(f"- {concern.message}" for concern in begun))
    if over:
        sections.append(
            "Over:\n\n"
            + "\n".join(
                f"- {alert.title}, after {span(now - alert.raised_at)}, since "
                f"{_at(alert.raised_at)}"
                for alert in over
            )
        )
    if still:
        sections.append(
            "Still on:\n\n"
            + "\n".join(f"- {alert.title}, since {_at(alert.raised_at)}" for alert in still)
        )
    body = (
        "\n\n".join(sections)
        + "\n\nThe worker looks again every quarter of an hour and writes when one of these "
        'begins or ends (deploying.md, "Alerts").\n'
    )
    return subject, body


def watch(services: Services, mailer: Mailer, to: str) -> int:
    """Look, then mail `to` what began and what ended since the last round, as one message, and
    record it in `watch_alerts`. Returns how many began or ended.

    The checks run outside any transaction, since they wait on the endpoint, the scanner and
    the storage; the comparison with what was raised runs under ALERT_LOCK, so that two
    workers looking at once write once. The record is committed only once the message was
    handed over, so a relay that is down leaves it for the next round. Without an address
    nothing is looked at.
    """
    if not to:
        return 0
    found, failed = look(services, get_settings())
    with SessionLocal() as session:
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": ALERT_LOCK})
        raised = {alert.key: alert for alert in session.scalars(select(WatchAlert))}
        begun = {key: concern for key, concern in found.items() if key not in raised}
        over = [
            alert
            for key, alert in raised.items()
            if key not in found and not any(key.startswith(prefix) for prefix in failed)
        ]
        if not begun and not over:
            return 0
        now = _now(session)
        still = [alert for key, alert in raised.items() if key in found]
        subject, body = watch_email(list(begun.values()), over, still, now)
        _record(session, begun, over)
        mailer.send(to, subject, body)
        session.commit()
    log.info("The watch reported %s begun and %s over", len(begun), len(over))
    return len(begun) + len(over)


def _record(session: Session, begun: dict[str, Concern], over: list[WatchAlert]) -> None:
    session.add_all(
        WatchAlert(key=key, title=concern.title, message=concern.message)
        for key, concern in begun.items()
    )
    if over:
        session.execute(delete(WatchAlert).where(WatchAlert.key.in_([a.key for a in over])))


def _window() -> str:
    return "hour" if WINDOW == timedelta(hours=1) else span(WINDOW)


def _now(session: Session) -> datetime:
    return session.scalar(text("SELECT now()"))


def _at(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
