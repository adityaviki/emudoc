"""Metrics: what the API and the worker count and measure, in Prometheus's text format, for the
operator's dashboards and alerts (deploying.md, "Metrics").

A process given METRICS_ADDRESS serves them there, on a port of its own (`export_metrics`),
never on the API's: the proxy opens that one to the internet, and the metrics say how large the
deployment is and which models it pays for. Anything that reads the format can ask for them:
Prometheus, Grafana's agent, the CloudWatch agent, an OpenTelemetry collector.

There are two kinds of figure:

- What a process did, counted as it does it: the requests it answered (`HttpMetrics`, by the
  route's pattern), its database connections (`Pool`), the requests it sent to the models and
  the embeddings endpoint (`model_request`: how each ended, how long it took, how soon an
  answer's first event came, the tokens, the fallbacks), the stages and jobs a worker ran, its
  threads (`Lanes`) and its parses, how long the members waited for an answer to begin and an
  upload to be ready (the service levels' measures), the uploads it stored or refused, the
  requests a rate refused, and the process's own CPU and memory (the library's). Each replica
  counts its own, and the scraper adds them up.
- What the database holds, the same whichever process reads it (`Holdings`): the documents by
  status, the queue by kind, the answers being written, the organisations and the bytes they
  keep by plan, and the day's spend; and how the server itself keeps up (app/postgres.py): the
  live and dead rows of each table, the oldest open transaction, and the archiving of the
  write-ahead log the backups rest on. Only a worker serves them, reading them when asked and
  at most once every MEASURE_SECONDS, so the queries grow with the workers and not with the
  scrapers or the API replicas. Every worker serves the same figures, which a query takes once
  (`max without (instance)`), and one whose last reading failed serves none of them.

No label names an organisation, a user, a document or anything a member typed. A series per
organisation would grow with every sign-up, and one organisation's figures are its usage
events' (app/usage.py, the `usage` command). A label holds the app's own words: a model's
configured name, a kind of work, a stage, a job's kind, a plan, a status, a route's pattern, a
rate limit's scope.

The library is prometheus_client rather than OpenTelemetry's SDK on purpose: a global meter or
tracer provider is read by more than the app (FastAPI's telemetry turns itself on for one, and
Anthropic's SDK opens a span per request with the global tracer's), so the app sets none, and
nothing begins to record what it sends; for the same reason a job's trace is a line of the log
(app/tracing.py). The registry is the library's own, one per process:
the image runs one uvicorn process per container, and uvicorn's --workers would need the
library's multiprocess mode, which this does not set up.
"""

import logging
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING
from wsgiref.simple_server import WSGIServer

from prometheus_client import REGISTRY, Counter, Gauge, Histogram, start_http_server
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, Metric
from prometheus_client.registry import Collector
from sqlalchemy import Engine, text

from app.config import Settings, parse_metrics_address
from app.db import SessionLocal, engine
from app.errors import AppError, summary
from app.jobs import (
    ANSWER_QUESTION,
    APPLY_LANGUAGE,
    DELIVER_WEBHOOK,
    EXPORT_PROJECT,
    INDEX_DOCUMENT,
    JOB_STATES,
    QUEUE_BY_STATE,
    RECEIVE_MAIL,
    RENDER_PAGES,
    RUN_AUTOMATION,
    RUN_QUESTION_SET,
    RUN_WORKFLOW,
    SEND_DIGESTS,
    SEND_EMAIL,
    SUGGEST_QUESTIONS,
    SYNC_CONNECTOR,
    WRITE_DRAFT,
    WRITE_QUESTION_REPORT,
)
from app.models import STAGES
from app.postgres import archive, database_report
from app.spending import day_of, spent_since
from app.tracing import note

if TYPE_CHECKING:
    from app.worker import Worker

log = logging.getLogger(__name__)

# How long a worker serves what it read of the database before it reads it again: scrapers
# that ask meanwhile, every 15 seconds or every minute, share one reading.
MEASURE_SECONDS = 15.0
# The kind of work an embeddings request does, beside INDEX and AGENT (app/config.py).
EMBED = "embed"
# The methods a request is counted under; any other is "other", so that a client inventing
# methods does not invent series.
METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
# The route of a request no route took: a 404 of the API, or a method nothing serves.
NO_ROUTE = "other"
# The statuses a document's row holds, and `trashed` for one in the trash, served even when no
# document has them, so that a dashboard's series start at zero rather than appear with the
# first one.
DOCUMENT_STATUSES = ("queued", "processing", "ready", "failed", "quarantined", "trashed")
# The kinds of job the worker runs (HANDLERS in app/worker.py), whose series start at zero.
JOBS = (
    INDEX_DOCUMENT,
    ANSWER_QUESTION,
    RENDER_PAGES,
    SEND_EMAIL,
    APPLY_LANGUAGE,
    EXPORT_PROJECT,
    RUN_WORKFLOW,
    RUN_AUTOMATION,
    WRITE_DRAFT,
    RECEIVE_MAIL,
    SYNC_CONNECTOR,
    DELIVER_WEBHOOK,
    SUGGEST_QUESTIONS,
    RUN_QUESTION_SET,
    WRITE_QUESTION_REPORT,
    SEND_DIGESTS,
)
# What a lane of the worker's threads runs (app/worker.py).
INDEXER = "indexer"
ANSWERER = "answerer"
MAILER = "mailer"
LANES = (INDEXER, ANSWERER, MAILER)
# How a stage, a document's run and a job may end, and why an upload may refuse a file: the
# counters start at zero under each, since `increase` and `rate` cannot see a series' first
# count, and an alert on the first failure after a start would otherwise miss it. A model's
# outcomes start with their first request: a provider's statuses are not known beforehand.
STAGE_OUTCOMES = (
    "done",
    "skipped",
    "failed",
    "transient",
    "deferred",
    "quarantined",
    "crashed",
    "database_lost",
    "lease_lost",
)
INDEX_OUTCOMES = ("ready", "failed", "quarantined")
PARSE_STATES = ("running", "waiting")
JOB_OUTCOMES = ("done", "failed", "crashed", "abandoned", "lost", "unsettled")
# How a message's try ended (app/postbox.py), and what a provider said of a delivered one
# (app/bounces.py).
MAIL_OUTCOMES = ("sent", "retried", "failed")
MAIL_EVENT_KINDS = ("bounce", "complaint")
# How a try of a webhook's delivery ended (app/webhooks.py): answered with a 2xx, tried again
# later, given up, or refused by the app itself (the address, the plan, the secret).
WEBHOOK_OUTCOMES = ("delivered", "retried", "failed", "refused")
# What became of a message sent to a project's address (app/inbound.py): kept, with nothing in it
# to keep, or turned away by its sender (not listed, not proven, unreadable), its size, the
# project's trash or the plan; for no project's address; or come while email in was off.
INBOUND_OUTCOMES = (
    "kept",
    "empty",
    "refused_sender",
    "unauthenticated",
    "unreadable",
    "too_large",
    "refused_project",
    "refused_plan",
    "unknown_address",
    "off",
)
# How a connected folder's sync went (app/connectors/sync.py): files taken or noted, nothing
# changed since the last, stopped partway or before it began (the service, the folder), paused
# (the grant, the member), held back (the project, the plan), or the flag off; and what became of
# each file it read: kept, rejected, its document to the trash, or replaced by a new version.
SYNC_OUTCOMES = ("synced", "unchanged", "failed", "paused", "held", "off")
SYNCED_FILE_OUTCOMES = ("kept", "rejected", "trashed", "replaced")
UPLOAD_REFUSALS = (
    "too_many",
    "plan",
    "unsupported",
    "empty",
    "too_large",
    "wrong_type",
    "duplicate",
    "folder",
    "unopened",
    "indexing",
)

HTTP_REQUESTS = Counter(
    "emudoc_http_requests",
    "Requests the API answered, by method, the pattern of the route that took them and status.",
    ["method", "route", "status"],
)
HTTP_SECONDS = Histogram(
    "emudoc_http_request_seconds",
    "How long the API took to answer, to the end of the response; an answer's events stream "
    "for as long as the answer is written.",
    ["method", "route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)
POOL_TIMEOUTS = Counter(
    "emudoc_db_pool_timeouts",
    "Requests and answers refused because every database connection of the process was in "
    "use for DB_POOL_TIMEOUT seconds.",
)
MODEL_REQUESTS = Counter(
    "emudoc_model_requests",
    "Requests to a model or the embeddings endpoint, by the configured model, the work (index, "
    "agent, embed) and how each ended: ok, the provider's HTTP status, timeout, unreachable, "
    "filtered, malformed, stopped or failed.",
    ["model", "role", "outcome"],
)
MODEL_SECONDS = Histogram(
    "emudoc_model_request_seconds",
    "How long a request to a model or the embeddings endpoint took, however it ended; an "
    "indexing request includes the provider's own retries, an agent turn its whole stream.",
    ["model", "role"],
    buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30, 60, 120, 300),
)
MODEL_FIRST_EVENT = Histogram(
    "emudoc_model_first_event_seconds",
    "How long an agent turn waited for the model's first event, which is when a member sees "
    "the answer begin.",
    ["model"],
    buckets=(0.25, 0.5, 1, 2, 3, 5, 10, 20, 30, 60),
)
MODEL_TOKENS = Counter(
    "emudoc_model_tokens",
    "Tokens the providers counted, by the configured model, the work and the direction (input, "
    "output).",
    ["model", "role", "direction"],
)
MODEL_FALLBACKS = Counter(
    "emudoc_model_fallbacks",
    "Requests a model failed in a way that asked its fallback instead, by the model that "
    "failed: its provider was down or refused the key, the account or the model.",
    ["model", "role"],
)
STAGE_RUNS = Counter(
    "emudoc_stage_runs",
    "Indexing stages run, by stage and how each ended: done, skipped, failed, transient (to "
    "be tried again), deferred (to the next UTC day), quarantined, crashed, database_lost or "
    "lease_lost.",
    ["stage", "outcome"],
)
STAGE_SECONDS = Histogram(
    "emudoc_stage_seconds",
    "How long an indexing stage ran, however it ended, skipped stages left out.",
    ["stage"],
    buckets=(0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600, 1800),
)
INDEX_RUNS = Counter(
    "emudoc_index_runs",
    "Indexing runs of a document, by how the document stood at the end: ready, failed or "
    "quarantined. A failed run may be tried again.",
    ["status"],
)
JOB_WAIT = Histogram(
    "emudoc_job_wait_seconds",
    "How long a job waited from the moment it was due to the moment a worker claimed it.",
    ["kind"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 300, 900, 3600),
)
JOB_SECONDS = Histogram(
    "emudoc_job_seconds",
    "How long a worker ran a job, its settling included.",
    ["kind"],
    buckets=(0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600, 1800),
)
JOB_RUNS = Counter(
    "emudoc_job_runs",
    "Jobs a worker ran, by kind and how each ended: done; failed, given up by its handler; "
    "crashed, its handler raised; abandoned after its lost attempts; lost, to another worker or "
    "to the database, for a later claim to run again; unsettled, its end not recorded.",
    ["kind", "outcome"],
)
# What the members waited for, as the service levels measure it (deploying.md, "Service
# levels"): the buckets hold each objective's bound, 30 seconds and 10 minutes, so that the
# share within it is a bucket's count over the whole.
ANSWER_FIRST_TEXT = Histogram(
    "emudoc_answer_first_text_seconds",
    "How long a member waited from asking a question to the first text of its answer: the "
    "job's wait for a worker and its run up to the first text. A run that starts an answer "
    "over counts again, from the question.",
    buckets=(1, 2, 5, 10, 15, 20, 30, 45, 60, 120, 300),
)
DOCUMENT_READY = Histogram(
    "emudoc_document_ready_seconds",
    "How long an upload took to be ready to search and ask about, from the upload to the end "
    "of its first indexing, retries and waits included; a later run is not counted.",
    buckets=(10, 30, 60, 120, 300, 600, 1200, 1800, 3600, 7200, 21600, 86400),
)
UPLOAD_BYTES = Histogram(
    "emudoc_upload_bytes",
    "The sizes of the uploaded files the API stored.",
    buckets=(1e4, 1e5, 1e6, 5e6, 1e7, 2.5e7, 5e7, 1e8, 2.5e8, 5e8),
)
UPLOAD_REJECTIONS = Counter(
    "emudoc_upload_rejections",
    "Uploaded files the API refused, by why: too_many (a request past MAX_UPLOAD_FILES or "
    "MAX_UPLOAD_REQUEST_MB, counted once), plan (the plan has no room), unsupported, empty, "
    "too_large (past MAX_UPLOAD_MB), wrong_type (the bytes are not what the name says), "
    "duplicate, folder (a folder of its path that cannot be made), unopened (a message or an "
    "archive the page uploaded that cannot be opened, or one inside another, or a file of an "
    "archive that does not unpack), indexing (a document's new version while it is being indexed, "
    "app/versions.py).",
    ["reason"],
)
MAIL_SENT = Counter(
    "emudoc_mail_sent",
    "Tries of the mail the app posted, by how each ended: sent, handed to the relay or the "
    "provider; retried, refused for now and queued again; failed, refused for good or for the "
    "last time. The operator's own reports are not counted.",
    ["outcome"],
)
WEBHOOK_DELIVERIES = Counter(
    "emudoc_webhook_deliveries",
    "Tries of the events posted to the organisations' webhooks, by how each ended: delivered, "
    "answered with a 2xx; retried, answered otherwise or not at all and queued again; failed, "
    "the last try; refused, not sent, for an address on the deployment's own network or over "
    "plain HTTP, a plan without the API, or a secret no key decrypts.",
    ["outcome"],
)
MAIL_EVENTS = Counter(
    "emudoc_mail_events",
    "Addresses the mail provider said not to write to, by why: bounce, mail to it bounced for "
    "good; complaint, its reader marked a message as spam.",
    ["kind"],
)
INBOUND_MESSAGES = Counter(
    "emudoc_inbound_messages",
    "Messages sent to projects' addresses, by what became of each: "
    + ", ".join(INBOUND_OUTCOMES)
    + ".",
    ["outcome"],
)
CONNECTOR_SYNCS = Counter(
    "emudoc_connector_syncs",
    "Syncs of connected folders, by how each went: " + ", ".join(SYNC_OUTCOMES) + ".",
    ["outcome"],
)
CONNECTOR_FILES = Counter(
    "emudoc_connector_files",
    "Files of connected folders the syncs read, by what became of each: "
    + ", ".join(SYNCED_FILE_OUTCOMES)
    + ".",
    ["outcome"],
)
RATE_LIMITED = Counter(
    "emudoc_rate_limited",
    "Requests refused for a rate (the RATE_* settings), by the limit's scope.",
    ["scope"],
)
# Started at zero by app/rendering.py, which knows the purposes and outcomes.
RENDERS = Counter(
    "emudoc_renders",
    "PDF pages drawn as images, by what for (pane, the document pane's; thumbnail; ocr; "
    "ahead, a first page drawn once the PDF was parsed) and how each ended: drawn, refused (a "
    "page that cannot be drawn), timeout (past RENDER_TIMEOUT), out_of_memory (past "
    "RENDER_MEMORY_MB), crashed or unstarted (no process could draw it).",
    ["purpose", "outcome"],
)
RENDER_SECONDS = Histogram(
    "emudoc_render_seconds",
    "How long drawing a page took, however it ended, the wait for a free render process included.",
    ["purpose"],
    buckets=(0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60),
)
# Set by app/indexing/pipeline.py (`ParseSlots`) as its parses start and end.
PARSES = Gauge(
    "emudoc_parses",
    "The worker's parses by state: running, each in a child process of its own, and waiting "
    "for one of the PARSE_CONCURRENCY running to end.",
    ["state"],
)


def _start_at_zero() -> None:
    # Naming a child of a metric is what makes its series, at zero.
    for stage in STAGES:
        STAGE_SECONDS.labels(stage)
        for outcome in STAGE_OUTCOMES:
            STAGE_RUNS.labels(stage, outcome)
    for outcome in INDEX_OUTCOMES:
        INDEX_RUNS.labels(outcome)
    for kind in JOBS:
        JOB_WAIT.labels(kind)
        JOB_SECONDS.labels(kind)
        for outcome in JOB_OUTCOMES:
            JOB_RUNS.labels(kind, outcome)
    for reason in UPLOAD_REFUSALS:
        UPLOAD_REJECTIONS.labels(reason)
    for state in PARSE_STATES:
        PARSES.labels(state)
    for outcome in MAIL_OUTCOMES:
        MAIL_SENT.labels(outcome)
    for kind in MAIL_EVENT_KINDS:
        MAIL_EVENTS.labels(kind)
    for outcome in WEBHOOK_OUTCOMES:
        WEBHOOK_DELIVERIES.labels(outcome)
    for outcome in INBOUND_OUTCOMES:
        INBOUND_MESSAGES.labels(outcome)
    for outcome in SYNC_OUTCOMES:
        CONNECTOR_SYNCS.labels(outcome)
    for outcome in SYNCED_FILE_OUTCOMES:
        CONNECTOR_FILES.labels(outcome)


_start_at_zero()


class ModelRequest:
    """One request to a model or the embeddings endpoint as `model_request` measures it, for
    the metrics and as a span of the job's run that sent it, if one is timed (app/tracing.py)."""

    def __init__(self, model: str, role: str):
        self.model = model
        self.role = role
        self._started = time.monotonic()
        self._first_event: float | None = None
        self._tokens: tuple[int | None, int | None] = (None, None)

    def first_event(self) -> None:
        """Note that the model sent its first event, once a turn."""
        if self._first_event is None:
            self._first_event = time.monotonic() - self._started
            MODEL_FIRST_EVENT.labels(self.model).observe(self._first_event)

    def used(self, input_tokens: int | None, output_tokens: int | None = None) -> None:
        """Count the tokens the provider said the request took."""
        self._tokens = (input_tokens, output_tokens)
        for direction, tokens in (("input", input_tokens), ("output", output_tokens)):
            if tokens:
                MODEL_TOKENS.labels(self.model, self.role, direction).inc(tokens)

    def end(self, outcome: str) -> None:
        MODEL_REQUESTS.labels(self.model, self.role, outcome).inc()
        MODEL_SECONDS.labels(self.model, self.role).observe(time.monotonic() - self._started)
        input_tokens, output_tokens = self._tokens
        note(
            self.role,
            self._started,
            model=self.model,
            outcome=outcome,
            first_event=None if self._first_event is None else round(self._first_event, 3),
            input_tokens=input_tokens or None,
            output_tokens=output_tokens or None,
        )


@contextmanager
def model_request(
    model: str, role: str, outcome: Callable[[BaseException], str]
) -> Iterator[ModelRequest]:
    """Measure the request made in the block: ok when it ends, else what `outcome` calls the
    exception that ended it, which goes on as it came. A stop that cancels a stream ends it
    with a `BaseException`, which is measured too."""
    request = ModelRequest(model, role)
    try:
        yield request
    except BaseException as error:
        request.end(outcome(error))
        raise
    request.end("ok")


class HttpMetrics:
    """Counts and times the requests the API answers, by method, the pattern of the route that
    took each (`/api/projects/{project_id}/documents`, never the path with its ids) and status.

    A pure ASGI middleware, so that an answer's event stream is timed to its end, and inside
    Starlette's handler of the errors nobody planned for, so that a request whose exception
    reached it is counted as the 500 that handler sends."""

    def __init__(self, app: Callable):
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.monotonic()
        status = 500

        async def sending(message: dict) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, sending)
        finally:
            route = _route_of(scope)
            method = scope["method"] if scope["method"] in METHODS else "other"
            HTTP_REQUESTS.labels(method, route, str(status)).inc()
            HTTP_SECONDS.labels(method, route).observe(time.monotonic() - started)


def _route_of(scope: dict) -> str:
    """The pattern of the route that took a request, from the scope every middleware shares.

    FastAPI keeps an included router's routes under the router rather than copying them into
    the app's, so the route the scope names has its own path, without the router's /api; the
    whole pattern is on the route FastAPI records it chose, which is what its own telemetry
    reports. tests/test_metrics.py pins what comes out, should a release move it."""
    chosen = scope.get("fastapi", {}).get("effective_route_context")
    pattern = getattr(chosen, "path_format", None) or getattr(scope.get("route"), "path", None)
    return pattern or NO_ROUTE


class Pool:
    """The process's database connections, as the pool stands when the metrics are asked for:
    in use, idle, and how many it may hold at most (DB_POOL_SIZE + DB_MAX_OVERFLOW)."""

    def __init__(self, engine: Engine, limit: int):
        self._engine = engine
        self._limit = limit

    def collect(self) -> Iterable[Metric]:
        pool = self._engine.pool
        connections = GaugeMetricFamily(
            "emudoc_db_connections",
            "The process's database connections, by state: in_use, idle.",
            labels=["state"],
        )
        connections.add_metric(["in_use"], pool.checkedout())
        connections.add_metric(["idle"], pool.checkedin())
        yield connections
        yield GaugeMetricFamily(
            "emudoc_db_connections_limit",
            "How many database connections the process may hold (DB_POOL_SIZE + DB_MAX_OVERFLOW).",
            value=self._limit,
        )


class Lanes:
    """A worker's job threads by lane (indexer, answerer, mailer): those alive, and those running a
    job now; and whether it hears of queued jobs as they are queued. A worker whose thread
    ended exits to be restarted (app/worker.py), so the live workers are the processes that
    answer, and the busy share says whether they keep up."""

    def __init__(self, worker: "Worker"):
        self._worker = worker

    def collect(self) -> Iterable[Metric]:
        threads = GaugeMetricFamily(
            "emudoc_worker_threads",
            "The worker's job threads that are alive, by lane (indexer, answerer, mailer).",
            labels=["lane"],
        )
        busy = GaugeMetricFamily(
            "emudoc_worker_busy",
            "The worker's job threads running a job now, by lane.",
            labels=["lane"],
        )
        for lane, (alive, running) in self._worker.lanes().items():
            threads.add_metric([lane], alive)
            busy.add_metric([lane], running)
        yield threads
        yield busy
        yield GaugeMetricFamily(
            "emudoc_worker_listening",
            "1 while the worker hears of queued jobs as they are queued, 0 while it finds them "
            "by polling, which makes a question wait up to two seconds for its answer to start.",
            value=int(self._worker.listening()),
        )


# A document in the trash (app/trash.py), by itself or in a project there, counts as `trashed`
# whatever its status: it waits there unindexed, and its bytes are still held.
_DOCUMENTS = text(
    "SELECT o.plan, CASE WHEN d.deleted_at IS NOT NULL OR p.deleted_at IS NOT NULL "
    "THEN 'trashed' ELSE d.status END AS status, "
    "count(*) AS documents, coalesce(sum(d.size_bytes), 0) AS bytes "
    "FROM documents d JOIN projects p ON p.id = d.project_id "
    "JOIN organizations o ON o.id = p.org_id GROUP BY 1, 2"
)
_ORGANIZATIONS = text("SELECT plan, count(*) FROM organizations GROUP BY plan")
# Served by the partial index on the messages being written.
_ANSWERING = text("SELECT count(*) FROM chat_messages WHERE status = 'streaming'")


class Holdings:
    """What the database holds, read by a worker when the metrics are asked for and kept for
    MEASURE_SECONDS: the scrapers that ask meanwhile, and every replica's, share one reading.

    A reading that fails is logged and serves nothing of the database's until the next one, so
    the series go stale rather than hold the last value that was read."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._lock = threading.Lock()
        self._read: tuple[float, list[Metric]] | None = None

    def describe(self) -> Iterable[Metric]:
        # The registry asks what a collector serves when it is registered, which would read the
        # database at start without this.
        return self._families().values()

    def collect(self) -> Iterable[Metric]:
        with self._lock:
            if self._read is None or time.monotonic() - self._read[0] >= MEASURE_SECONDS:
                try:
                    families = self._measure()
                except Exception as error:
                    log.warning(
                        "The metrics could not read the database: %s",
                        summary(error).splitlines()[0],
                    )
                    families = []
                self._read = (time.monotonic(), families)
            return list(self._read[1])

    def _families(self) -> dict[str, GaugeMetricFamily]:
        currency = ["currency"]
        return {
            "documents": GaugeMetricFamily(
                "emudoc_documents",
                "Documents by status: queued, processing, ready, failed, quarantined, and "
                "trashed for those in the trash, whatever their status.",
                labels=["status"],
            ),
            "stored": GaugeMetricFamily(
                "emudoc_stored_bytes",
                "The bytes of the uploaded originals the organisations keep, by plan.",
                labels=["plan"],
            ),
            "organizations": GaugeMetricFamily(
                "emudoc_organizations", "Organisations by plan.", labels=["plan"]
            ),
            "jobs": GaugeMetricFamily(
                "emudoc_jobs",
                "Jobs by kind and state: due (to be claimed now), waiting (for a retry or the "
                "next UTC day), running, failed (kept for the operator).",
                labels=["kind", "state"],
            ),
            "oldest": GaugeMetricFamily(
                "emudoc_job_oldest_due_seconds",
                "How long the oldest due job of each kind has waited for a worker; 0 when none "
                "waits.",
                labels=["kind"],
            ),
            "answering": GaugeMetricFamily(
                "emudoc_answers_in_flight", "Answers queued or being written."
            ),
            "spent": GaugeMetricFamily(
                "emudoc_spent_today",
                "The known cost of the models' work since midnight UTC, in PRICE_CURRENCY; "
                "nothing is known without MODEL_PRICES.",
                labels=currency,
            ),
            "cap": GaugeMetricFamily(
                "emudoc_daily_spend_cap",
                "DAILY_SPEND_CAP in PRICE_CURRENCY, 0 for none.",
                labels=currency,
            ),
            "rows": GaugeMetricFamily(
                "emudoc_db_table_rows",
                "The rows of each table of the app's, by state: live, or dead and waiting for "
                "vacuum, as the server's statistics estimate them.",
                labels=["table", "state"],
            ),
            "transaction": GaugeMetricFamily(
                "emudoc_db_oldest_transaction_seconds",
                "How long the oldest transaction of the app's role but the reading's own has "
                "been open, holding back vacuum in every table; 0 when none is.",
            ),
            "archived": CounterMetricFamily(
                "emudoc_wal_archived",
                "Segments of the write-ahead log the server archived since its statistics were "
                "reset; served only by a server that archives.",
            ),
            "archive_failures": CounterMetricFamily(
                "emudoc_wal_archive_failures",
                "Attempts to archive a segment of the write-ahead log that failed.",
            ),
            "archive_age": GaugeMetricFamily(
                "emudoc_wal_last_archived_seconds",
                "How long ago the server last archived a segment of the write-ahead log.",
            ),
            "archive_pending": GaugeMetricFamily(
                "emudoc_wal_segments_pending",
                "Finished segments of the write-ahead log waiting to be archived; with the last "
                "archive older than the recovery point, the backups are falling behind.",
            ),
        }

    def _measure(self) -> list[Metric]:
        families = self._families()
        documents = dict.fromkeys(DOCUMENT_STATUSES, 0)
        stored: dict[str, int] = {}
        jobs = {(kind, state): 0 for kind in JOBS for state in JOB_STATES}
        oldest = dict.fromkeys(JOBS, 0.0)
        with SessionLocal() as session:
            for plan, status, count, size in session.execute(_DOCUMENTS):
                documents[status] = documents.get(status, 0) + count
                stored[plan] = stored.get(plan, 0) + size
            organizations = dict(session.execute(_ORGANIZATIONS).all())
            for kind, state, count, waited in session.execute(QUEUE_BY_STATE):
                jobs[kind, state] = count
                if state == "due":
                    oldest[kind] = max(float(waited), 0.0)
            answering = session.scalar(_ANSWERING)
            spent = spent_since(session, day_of()[0])
            connection = session.connection()
            report = database_report(connection, limit=None)
            archiving = archive(connection)
        for status, count in documents.items():
            families["documents"].add_metric([status], count)
        for plan in organizations.keys() | stored.keys():
            families["stored"].add_metric([plan], stored.get(plan, 0))
            families["organizations"].add_metric([plan], organizations.get(plan, 0))
        for (kind, state), count in jobs.items():
            families["jobs"].add_metric([kind, state], count)
        for kind, waited in oldest.items():
            families["oldest"].add_metric([kind], waited)
        families["answering"].add_metric([], answering)
        currency = [self._settings.price_currency]
        families["spent"].add_metric(currency, spent / 1_000_000)
        families["cap"].add_metric(currency, self._settings.daily_spend_cap_micros / 1_000_000)
        for table in report.tables:
            families["rows"].add_metric([table.name, "live"], table.rows)
            families["rows"].add_metric([table.name, "dead"], table.dead)
        oldest = report.oldest.total_seconds() if report.oldest is not None else 0.0
        families["transaction"].add_metric([], max(oldest, 0.0))
        if archiving is None:
            # No archiver to speak of: its figures are left out rather than say all is well.
            for name in ("archived", "archive_failures", "archive_age", "archive_pending"):
                del families[name]
        else:
            families["archived"].add_metric([], archiving.archived)
            families["archive_failures"].add_metric([], archiving.failed)
            if archiving.archived_at is None:
                del families["archive_age"]
            else:
                age = (archiving.taken_at - archiving.archived_at).total_seconds()
                families["archive_age"].add_metric([], max(age, 0.0))
            if archiving.pending is None:
                del families["archive_pending"]
            else:
                families["archive_pending"].add_metric([], archiving.pending)
        return list(families.values())


class MetricsRefused(AppError):
    """The metrics' server could not listen where METRICS_ADDRESS says."""


@dataclass
class Exporter:
    """The metrics' server of a process, and the collectors it registered for it."""

    server: WSGIServer
    collectors: list[Collector]

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        for collector in self.collectors:
            REGISTRY.unregister(collector)


def export_metrics(settings: Settings, worker: "Worker | None" = None) -> Exporter | None:
    """Serve the process's metrics at METRICS_ADDRESS, from a thread of their own, or nothing
    without it; a process that runs `worker` serves its lanes and the database's figures too.
    Raises `MetricsRefused` when the address cannot be listened on, so that a process starts
    with its metrics or not at all."""
    address = parse_metrics_address(settings.metrics_address)
    if address is None:
        return None
    host, port = address
    collectors: list[Collector] = [Pool(engine, settings.db_pool_size + settings.db_max_overflow)]
    if worker is not None:
        collectors += [Lanes(worker), Holdings(settings)]
    for collector in collectors:
        REGISTRY.register(collector)
    try:
        server, _ = start_http_server(port, host)
    except OSError as error:
        for collector in collectors:
            REGISTRY.unregister(collector)
        raise MetricsRefused(
            f"The metrics cannot be served at METRICS_ADDRESS {settings.metrics_address}: "
            f"{error.strerror}. Name a free port on an address of this machine, or leave "
            "METRICS_ADDRESS empty for none."
        ) from error
    log.info(
        "Metrics are served at http://%s/metrics%s",
        settings.metrics_address,
        ", with the database's figures" if worker is not None else "",
    )
    return Exporter(server, collectors)


def lane_of(kind: str) -> str:
    """The lane of the worker's threads that runs a job of `kind` (app/worker.py)."""
    if kind == SEND_EMAIL:
        return MAILER
    return ANSWERER if kind in (ANSWER_QUESTION, RUN_WORKFLOW, RUN_AUTOMATION) else INDEXER
