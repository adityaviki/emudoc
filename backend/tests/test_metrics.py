"""The metrics (app/metrics.py): what the API and the worker count as they work, the database's
figures a worker serves, the port of their own they are served on, and that no label holds
what a member typed or who they are."""

import asyncio
import re
import socket
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY, CollectorRegistry, generate_latest
from pydantic import ValidationError
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.models.function import AgentInfo, FunctionModel
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

import app.main as main
from app.config import ROOT_DIR, get_settings, load_settings, parse_metrics_address
from app.db import SessionLocal, engine
from app.embeddings import EmbeddingError
from app.indexing.enrich import DocumentAnalysis
from app.indexing.pipeline import process_document
from app.jobs import ANSWER_QUESTION, INDEX_DOCUMENT
from app.llm import LLMError
from app.main import create_app
from app.metrics import MEASURE_SECONDS, Holdings, Lanes, MetricsRefused, Pool, export_metrics
from app.models import STAGES, Chat, ChatMessage, Job, Project
from app.postgres import Archive
from app.worker import Worker, serve
from tests.conftest import APP_HEADERS, FakeEmbedder, FakeScanner
from tests.test_api import create_project
from tests.test_auth import signup
from tests.test_embeddings import embedder, refuse
from tests.test_jobs import wait_until
from tests.test_llm import (
    ANALYSIS,
    BACKUP,
    QUESTION,
    SCRIPTED,
    Script,
    fallback_llm,
    scripted_llm,
    text_reply,
)
from tests.test_pool import one_connection  # noqa: F401 (a fixture)
from tests.test_spending import spend


def sample(name: str, **labels: str) -> float:
    """A sample's value in the process's registry, 0 before anything was counted under it."""
    return REGISTRY.get_sample_value(name, labels) or 0.0


class Counted:
    """The samples' values now, to compare with what they are after the test acted."""

    def __init__(self, *samples: tuple[str, dict[str, str]]):
        self._before = {
            (name, tuple(sorted(labels.items()))): sample(name, **labels)
            for name, labels in samples
        }

    def __call__(self, name: str, **labels: str) -> float:
        return sample(name, **labels) - self._before[name, tuple(sorted(labels.items()))]


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def scrape(port: int) -> str:
    response = httpx.get(f"http://127.0.0.1:{port}/metrics", timeout=5)
    assert response.status_code == 200
    return response.text


def figures(collector) -> dict[tuple[str, tuple[str, ...]], float]:
    """What a collector serves, by sample name and label values."""
    return {
        (point.name, tuple(point.labels.values())): point.value
        for family in collector.collect()
        for point in family.samples
    }


@pytest.fixture
def metrics_port(monkeypatch) -> int:
    """A free port the processes under test serve their metrics on."""
    port = free_port()
    monkeypatch.setattr(get_settings(), "metrics_address", f"127.0.0.1:{port}")
    return port


def test_what_can_be_counted_is_served_from_zero():
    # `increase` cannot see a series' first count, so an alert on the first crash of a stage
    # after a start needs the series there before it.
    for name, labels in (
        ("emudoc_stage_runs_total", {"stage": "embed", "outcome": "crashed"}),
        ("emudoc_index_runs_total", {"status": "failed"}),
        ("emudoc_job_runs_total", {"kind": ANSWER_QUESTION, "outcome": "abandoned"}),
        ("emudoc_upload_rejections_total", {"reason": "wrong_type"}),
        ("emudoc_rate_limited_total", {"scope": "sign_in"}),
        ("emudoc_job_wait_seconds_count", {"kind": INDEX_DOCUMENT}),
    ):
        assert REGISTRY.get_sample_value(name, labels) is not None, (name, labels)


# The server


def test_the_api_serves_its_metrics_on_a_port_of_its_own(metrics_port, services, session, mailer):
    with TestClient(create_app(services=services, mailer=mailer), headers=APP_HEADERS) as client:
        project = create_project(client)
        client.get(f"/api/projects/{project['id']}/documents")
        served = scrape(metrics_port)
        # Not on the API's own port, which the proxy opens to the internet.
        assert "emudoc_" not in client.get("/metrics").text

    assert (
        'emudoc_http_requests_total{method="GET",route="/api/projects/{project_id}/documents",'
        'status="200"}'
    ) in served
    assert 'emudoc_db_connections{state="in_use"}' in served
    limit = get_settings().db_pool_size + get_settings().db_max_overflow
    assert f"emudoc_db_connections_limit {limit:.1f}" in served
    assert "process_cpu_seconds_total" in served
    # An API that runs no worker leaves the database's figures and the lanes to the workers.
    assert "emudoc_documents" not in served and "emudoc_worker_threads" not in served
    # The stop closed the port and gave the collectors back: the next start registers them anew.
    with pytest.raises(httpx.ConnectError):
        httpx.get(f"http://127.0.0.1:{metrics_port}/metrics")
    export_metrics(get_settings()).close()


def test_without_an_address_nothing_is_served(services, session, mailer, caplog):
    assert get_settings().metrics_address == ""
    with caplog.at_level("INFO", logger="app.metrics"):
        assert export_metrics(get_settings()) is None
        with TestClient(create_app(services=services, mailer=mailer)):
            pass
    assert "Metrics are served" not in caplog.text


def test_an_address_that_cannot_be_listened_on_stops_the_start(metrics_port, services, session):
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", metrics_port))
        taken.listen()
        with pytest.raises(MetricsRefused) as refused:
            export_metrics(get_settings())
        assert str(refused.value) == (
            f"The metrics cannot be served at METRICS_ADDRESS 127.0.0.1:{metrics_port}: Address "
            "already in use. Name a free port on an address of this machine, or leave "
            "METRICS_ADDRESS empty for none."
        )
        # The worker refuses it before any of its threads starts.
        running = set(threading.enumerate())
        with pytest.raises(MetricsRefused):
            serve(services, get_settings(), threading.Event(), grace=1)
        assert set(threading.enumerate()) <= running
    # What it registered went with the refusal, so the next try starts clean.
    export_metrics(get_settings()).close()


@pytest.mark.parametrize(
    ("value", "address"),
    [
        ("", None),
        ("  ", None),
        ("127.0.0.1:9464", ("127.0.0.1", 9464)),
        ("0.0.0.0:9464", ("0.0.0.0", 9464)),
        ("[::]:9464", ("::", 9464)),
        ("metrics.internal:9100", ("metrics.internal", 9100)),
    ],
)
def test_the_address_is_a_host_and_a_port(value, address):
    assert parse_metrics_address(value) == address


@pytest.mark.parametrize("value", ["9464", ":9464", "127.0.0.1", "127.0.0.1:0", "::1:9464", "h:x"])
def test_an_address_of_another_form_is_refused_at_start(value):
    with pytest.raises(ValidationError, match="METRICS_ADDRESS is a host and port"):
        load_settings(_env_file=None, database_url="postgresql://x/y", metrics_address=value)


def test_a_worker_serves_its_lanes_and_the_databases_figures(
    metrics_port, session, services, make_document, sample_pdf
):
    document = make_document(sample_pdf)
    stopping = threading.Event()
    worker = threading.Thread(
        target=serve, args=(services, get_settings(), stopping), kwargs={"grace": 1}, daemon=True
    )
    worker.start()
    try:

        def ready() -> bool:
            session.expire_all()
            return document.status == "ready"

        wait_until(ready)
        served = scrape(metrics_port)
    finally:
        stopping.set()
    worker.join(timeout=10)
    settings = get_settings()
    assert f'emudoc_worker_threads{{lane="indexer"}} {settings.worker_concurrency:.1f}' in served
    assert f'emudoc_worker_threads{{lane="answerer"}} {settings.answer_concurrency:.1f}' in served
    assert 'emudoc_documents{status="ready"}' in served
    assert 'emudoc_stage_runs_total{outcome="done",stage="parse"}' in served
    with pytest.raises(httpx.ConnectError):
        httpx.get(f"http://127.0.0.1:{metrics_port}/metrics")


# What the API counts


def test_a_request_is_counted_under_the_pattern_of_its_route(client, monkeypatch):
    project = create_project(client)
    route = "/api/projects/{project_id}/documents"
    counted = Counted(
        ("emudoc_http_requests_total", {"method": "GET", "route": route, "status": "200"}),
        ("emudoc_http_request_seconds_count", {"method": "GET", "route": route}),
        ("emudoc_http_requests_total", {"method": "GET", "route": "other", "status": "404"}),
        (
            "emudoc_http_requests_total",
            {"method": "GET", "route": "/api/subprocessors", "status": "500"},
        ),
    )
    client.get(f"/api/projects/{project['id']}/documents")
    client.get("/api/no-such-thing")

    def boom(*args, **kwargs):
        raise RuntimeError("an error nobody planned for")

    monkeypatch.setattr(main, "subprocessors", boom)
    failing = TestClient(client.app, headers=APP_HEADERS, raise_server_exceptions=False)
    assert failing.get("/api/subprocessors").status_code == 500

    assert counted("emudoc_http_requests_total", method="GET", route=route, status="200") == 1
    assert counted("emudoc_http_request_seconds_count", method="GET", route=route) == 1
    assert counted("emudoc_http_requests_total", method="GET", route="other", status="404") == 1
    assert (
        counted(
            "emudoc_http_requests_total", method="GET", route="/api/subprocessors", status="500"
        )
        == 1
    )


def test_uploads_are_measured_and_the_refused_files_counted_by_why(client, monkeypatch):
    project = create_project(client)
    url = f"/api/projects/{project['id']}/documents"
    reasons = ("empty", "unsupported", "duplicate", "too_many")
    counted = Counted(
        ("emudoc_upload_bytes_count", {}),
        ("emudoc_upload_bytes_sum", {}),
        ("emudoc_rate_limited_total", {"scope": "upload"}),
        *(("emudoc_upload_rejections_total", {"reason": reason}) for reason in reasons),
    )
    contract = b"# Service contract\n\nThe notice period is ninety days.\n"
    files = [("contract.md", contract), ("empty.txt", b""), ("tool.exe", b"MZ")]
    response = client.post(url, files=[("files", file) for file in files])
    assert response.status_code == 201
    client.post(url, files=[("files", ("again.md", contract))])
    monkeypatch.setattr(get_settings(), "max_upload_files", 1)
    assert (
        client.post(url, files=[("files", ("a.md", b"a")), ("files", ("b.md", b"b"))]).status_code
        == 413
    )
    monkeypatch.setattr(get_settings(), "rate_uploads", 1)
    assert client.post(url, files=[("files", ("c.md", b"c"))]).status_code == 429

    assert counted("emudoc_upload_bytes_count") == 1
    assert counted("emudoc_upload_bytes_sum") == len(contract)
    for reason in reasons:
        assert counted("emudoc_upload_rejections_total", reason=reason) == 1, reason
    assert counted("emudoc_rate_limited_total", scope="upload") == 1


def test_a_request_the_pool_had_no_connection_for_is_counted(one_connection, client):  # noqa: F811
    counted = Counted(("emudoc_db_pool_timeouts_total", {}))
    with SessionLocal() as holder:
        holder.execute(text("SELECT 1"))
        assert client.get("/api/health").status_code == 503
    assert counted("emudoc_db_pool_timeouts_total") == 1


# The models and the embeddings endpoint


def test_an_indexing_request_is_counted_with_its_outcome_its_time_and_its_tokens():
    counted = Counted(
        ("emudoc_model_requests_total", {"model": SCRIPTED, "role": "index", "outcome": "503"}),
        ("emudoc_model_requests_total", {"model": BACKUP, "role": "index", "outcome": "ok"}),
        ("emudoc_model_fallbacks_total", {"model": SCRIPTED, "role": "index"}),
        ("emudoc_model_request_seconds_count", {"model": BACKUP, "role": "index"}),
        ("emudoc_model_tokens_total", {"model": BACKUP, "role": "index", "direction": "input"}),
        ("emudoc_model_tokens_total", {"model": BACKUP, "role": "index", "direction": "output"}),
    )
    overloaded = Script(ModelHTTPError(503, "m"))
    llm = fallback_llm(FunctionModel(overloaded), FunctionModel(Script(text_reply(ANALYSIS))))
    llm.structured("the document", DocumentAnalysis)

    assert counted("emudoc_model_requests_total", model=SCRIPTED, role="index", outcome="503") == 1
    assert counted("emudoc_model_requests_total", model=BACKUP, role="index", outcome="ok") == 1
    assert counted("emudoc_model_fallbacks_total", model=SCRIPTED, role="index") == 1
    assert counted("emudoc_model_request_seconds_count", model=BACKUP, role="index") == 1
    for direction in ("input", "output"):
        tokens = counted(
            "emudoc_model_tokens_total", model=BACKUP, role="index", direction=direction
        )
        assert tokens > 0


def test_an_agent_turn_is_timed_to_its_first_event():
    counted = Counted(
        ("emudoc_model_requests_total", {"model": SCRIPTED, "role": "agent", "outcome": "ok"}),
        ("emudoc_model_first_event_seconds_count", {"model": SCRIPTED}),
        ("emudoc_model_tokens_total", {"model": SCRIPTED, "role": "agent", "direction": "output"}),
    )

    async def answer(messages: list, info: AgentInfo):
        for words in ("The notice ", "period is ", "90 days."):
            yield words

    llm = scripted_llm(FunctionModel(stream_function=answer))
    with llm.stream_agent("Be brief.", QUESTION, []) as stream:
        assert stream.response().text == "The notice period is 90 days."
    assert counted("emudoc_model_requests_total", model=SCRIPTED, role="agent", outcome="ok") == 1
    # Once a turn, not once an event.
    assert counted("emudoc_model_first_event_seconds_count", model=SCRIPTED) == 1
    assert (
        counted("emudoc_model_tokens_total", model=SCRIPTED, role="agent", direction="output") > 0
    )


async def quiet(messages: list, info: AgentInfo):
    yield "The notice "
    await asyncio.sleep(60)


def test_a_turn_the_reader_left_is_counted_as_stopped():
    labels = {"model": SCRIPTED, "role": "agent", "outcome": "stopped"}
    counted = Counted(("emudoc_model_requests_total", labels))
    llm = scripted_llm(FunctionModel(stream_function=quiet))
    with llm.stream_agent("Be brief.", QUESTION, []) as stream:
        next(iter(stream))
    # The request ends on the model loop, a moment after the reader let it go.
    wait_until(lambda: counted("emudoc_model_requests_total", **labels) == 1)


def test_a_turn_the_reader_gave_up_waiting_for_is_counted_as_a_timeout(monkeypatch):
    labels = {"model": SCRIPTED, "role": "agent", "outcome": "timeout"}
    counted = Counted(("emudoc_model_requests_total", labels))
    monkeypatch.setattr("app.llm.STREAM_STALL", 0.2)
    llm = scripted_llm(FunctionModel(stream_function=quiet))
    with pytest.raises(LLMError), llm.stream_agent("Be brief.", QUESTION, []) as stream:
        list(stream)
    wait_until(lambda: counted("emudoc_model_requests_total", **labels) == 1)


def test_embedding_requests_are_counted_with_their_tokens():
    model = get_settings().embedding_model
    dim = get_settings().embedding_dim
    outcomes = ("ok", "503", "unreachable")
    counted = Counted(
        *(
            ("emudoc_model_requests_total", {"model": model, "role": "embed", "outcome": outcome})
            for outcome in outcomes
        ),
        ("emudoc_model_tokens_total", {"model": model, "role": "embed", "direction": "input"}),
    )

    def answer(request: httpx.Request) -> httpx.Response:
        vector = {"index": 0, "embedding": [0.1] * dim}
        return httpx.Response(200, json={"data": [vector], "usage": {"prompt_tokens": 7}})

    embedder(answer).embed_documents(["The notice period is ninety days."])
    with pytest.raises(EmbeddingError):
        embedder(lambda request: httpx.Response(503)).embed_query("notice")
    with pytest.raises(EmbeddingError):
        embedder(refuse).embed_query("notice")

    for outcome in outcomes:
        assert (
            counted("emudoc_model_requests_total", model=model, role="embed", outcome=outcome) == 1
        )
    assert counted("emudoc_model_tokens_total", model=model, role="embed", direction="input") == 7


# What the worker counts


def test_the_stages_and_the_runs_are_counted_by_how_they_ended(
    session, services, make_document, sample_pdf
):
    counted = Counted(
        *(("emudoc_stage_runs_total", {"stage": stage, "outcome": "done"}) for stage in STAGES),
        *(("emudoc_stage_seconds_count", {"stage": stage}) for stage in STAGES),
        ("emudoc_stage_runs_total", {"stage": "scan", "outcome": "skipped"}),
        ("emudoc_stage_runs_total", {"stage": "embed", "outcome": "transient"}),
        ("emudoc_stage_runs_total", {"stage": "scan", "outcome": "quarantined"}),
        *(("emudoc_index_runs_total", {"status": status}) for status in ("ready", "quarantined")),
    )
    process_document(make_document(sample_pdf).id, services)
    for stage in STAGES:
        assert counted("emudoc_stage_runs_total", stage=stage, outcome="done") == 1
        assert counted("emudoc_stage_seconds_count", stage=stage) == 1
    assert counted("emudoc_index_runs_total", status="ready") == 1

    # Without a scanner the scan is skipped, and a skipped stage has no time to count; an
    # embeddings endpoint that is down fails its stage for a passing reason.
    services.scanner, services.embedder.fail = None, True
    process_document(make_document(sample_pdf, "second.pdf").id, services)
    assert counted("emudoc_stage_runs_total", stage="scan", outcome="skipped") == 1
    assert counted("emudoc_stage_seconds_count", stage="scan") == 1
    assert counted("emudoc_stage_runs_total", stage="embed", outcome="transient") == 1
    assert counted("emudoc_index_runs_total", status="ready") == 2

    services.scanner = FakeScanner()
    services.scanner.flagging = True
    process_document(make_document(sample_pdf, "third.pdf").id, services)
    assert counted("emudoc_stage_runs_total", stage="scan", outcome="quarantined") == 1
    assert counted("emudoc_index_runs_total", status="quarantined") == 1


class HeldEmbedder(FakeEmbedder):
    """Embeds once let go, so that a test can look at a worker while it runs a job."""

    def __init__(self):
        super().__init__()
        self.embedding = threading.Event()
        self.go = threading.Event()

    def embed_documents(self, texts, usage=None):
        self.embedding.set()
        assert self.go.wait(timeout=10)
        return super().embed_documents(texts, usage)


def test_a_worker_counts_its_jobs_and_shows_its_lanes(session, services, make_document, sample_pdf):
    counted = Counted(
        ("emudoc_job_runs_total", {"kind": INDEX_DOCUMENT, "outcome": "done"}),
        ("emudoc_job_wait_seconds_count", {"kind": INDEX_DOCUMENT}),
        ("emudoc_job_seconds_count", {"kind": INDEX_DOCUMENT}),
    )
    services.embedder = HeldEmbedder()
    document = make_document(sample_pdf)
    worker = Worker(services, concurrency=2, answer_concurrency=1)
    lanes = Lanes(worker)
    worker.start()
    try:
        assert services.embedder.embedding.wait(timeout=10)
        wait_until(worker.listening)
        assert figures(lanes) == {
            ("emudoc_worker_threads", ("indexer",)): 2,
            ("emudoc_worker_threads", ("answerer",)): 1,
            ("emudoc_worker_busy", ("indexer",)): 1,
            ("emudoc_worker_busy", ("answerer",)): 0,
            ("emudoc_worker_listening", ()): 1,
        }
        services.embedder.go.set()

        def ready() -> bool:
            session.expire_all()
            return document.status == "ready"

        wait_until(ready)
        wait_until(
            lambda: counted("emudoc_job_runs_total", kind=INDEX_DOCUMENT, outcome="done") == 1
        )
    finally:
        services.embedder.go.set()
        worker.stop()
    assert counted("emudoc_job_wait_seconds_count", kind=INDEX_DOCUMENT) == 1
    assert counted("emudoc_job_seconds_count", kind=INDEX_DOCUMENT) == 1
    # A stopped worker's threads have ended, its listener's with them.
    assert figures(lanes)[("emudoc_worker_threads", ("indexer",))] == 0
    assert figures(lanes)[("emudoc_worker_listening", ())] == 0


def test_a_worker_that_cannot_listen_says_so(services, monkeypatch):
    # Behind a pooler in transaction mode without DATABASE_LISTEN_URL the threads poll.
    monkeypatch.setattr(get_settings(), "database_pooler", "transaction")
    monkeypatch.setattr(get_settings(), "database_listen_url", "")
    worker = Worker(services, concurrency=1)
    worker.start()
    try:
        assert figures(Lanes(worker))[("emudoc_worker_listening", ())] == 0
    finally:
        worker.stop()


# What the database holds


def test_a_worker_serves_what_the_database_holds(
    session, account, make_account, make_document, sample_pdf, sample_docx, monkeypatch
):
    monkeypatch.setattr(get_settings(), "daily_spend_cap", Decimal("10"))
    queued = make_document(sample_pdf)
    project = session.get(Project, queued.project_id)
    failed = make_document(sample_docx, project=project)
    failed.status = "failed"
    # The queued document's job has waited a minute and a half for a worker.
    session.execute(
        text("UPDATE jobs SET run_at = now() - interval '90 seconds' WHERE id = :id"),
        {"id": session.scalar(text("SELECT min(id) FROM jobs"))},
    )
    session.execute(
        text(
            "UPDATE jobs SET status = 'failed', finished_at = now() "
            "WHERE payload->>'document_id' = :id"
        ),
        {"id": str(failed.id)},
    )
    session.add_all(
        [
            Job(
                org_id=project.org_id,
                project_id=project.id,
                kind=INDEX_DOCUMENT,
                payload={"document_id": failed.id},
                run_at=datetime.now(UTC) + timedelta(hours=1),
            ),
            Job(
                org_id=project.org_id,
                project_id=project.id,
                kind=ANSWER_QUESTION,
                payload={"message_id": 1},
                status="running",
                worker_id="elsewhere:1",
                lease_until=datetime.now(UTC) + timedelta(minutes=5),
            ),
        ]
    )
    chat = Chat(project_id=project.id, user_id=account.user.id, title="Notice")
    session.add(chat)
    session.flush()
    session.add(ChatMessage(chat_id=chat.id, role="assistant", content="", status="streaming"))
    session.commit()
    other = make_account("ada@example.com")
    spend(session, other.organization.id, "2.50")
    spend(session, other.organization.id, "4", at=datetime.now(UTC) - timedelta(days=1))

    read = figures(Holdings(get_settings()))
    size = queued.size_bytes + failed.size_bytes
    currency = get_settings().price_currency
    assert {key: value for key, value in read.items() if key[0] == "emudoc_documents"} == {
        ("emudoc_documents", ("queued",)): 1,
        ("emudoc_documents", ("processing",)): 0,
        ("emudoc_documents", ("ready",)): 0,
        ("emudoc_documents", ("failed",)): 1,
        ("emudoc_documents", ("quarantined",)): 0,
    }
    assert read["emudoc_stored_bytes", ("enterprise",)] == size
    assert read["emudoc_stored_bytes", ("free",)] == 0
    assert read["emudoc_organizations", ("enterprise",)] == 1
    assert read["emudoc_organizations", ("free",)] == 1
    assert read["emudoc_jobs", (INDEX_DOCUMENT, "due")] == 1
    assert read["emudoc_jobs", (INDEX_DOCUMENT, "waiting")] == 1
    assert read["emudoc_jobs", (INDEX_DOCUMENT, "failed")] == 1
    assert read["emudoc_jobs", (INDEX_DOCUMENT, "running")] == 0
    assert read["emudoc_jobs", (ANSWER_QUESTION, "running")] == 1
    assert read["emudoc_jobs", (ANSWER_QUESTION, "due")] == 0
    assert 89 < read["emudoc_job_oldest_due_seconds", (INDEX_DOCUMENT,)] < 300
    assert read["emudoc_job_oldest_due_seconds", (ANSWER_QUESTION,)] == 0
    assert read["emudoc_answers_in_flight", ()] == 1
    assert read["emudoc_spent_today", (currency,)] == 2.5
    assert read["emudoc_daily_spend_cap", (currency,)] == 10
    # The server's own state: every table's rows, as its statistics estimate them, and no
    # transaction but the reading's, which does not count. The test database archives nothing,
    # so the archive's figures are left out rather than served as though all were well.
    assert {key[1] for key in read if key[0] == "emudoc_db_table_rows"} >= {
        ("documents", "live"),
        ("documents", "dead"),
        ("passages", "dead"),
        ("watch_alerts", "live"),
    }
    assert read["emudoc_db_oldest_transaction_seconds", ()] == 0
    assert not [key for key in read if key[0].startswith("emudoc_wal_")]


def test_a_worker_serves_how_the_database_keeps_up(session, monkeypatch):
    monkeypatch.setattr("app.metrics.MEASURE_SECONDS", 0)
    with engine.connect() as other:
        other.execute(text("SELECT 1"))
        # A transaction open in another session, as a forgotten psql would hold one.
        wait_until(
            lambda: (
                figures(Holdings(get_settings()))["emudoc_db_oldest_transaction_seconds", ()] > 0
            )
        )
        other.rollback()

    taken = datetime(2026, 10, 18, 9, 30, tzinfo=UTC)
    stuck = Archive(
        archived=120,
        failed=7,
        archived_at=taken - timedelta(minutes=12),
        failed_at=taken - timedelta(seconds=30),
        pending=3,
        taken_at=taken,
    )
    monkeypatch.setattr("app.metrics.archive", lambda connection: stuck)
    read = figures(Holdings(get_settings()))
    assert read["emudoc_wal_archived_total", ()] == 120
    assert read["emudoc_wal_archive_failures_total", ()] == 7
    assert read["emudoc_wal_last_archived_seconds", ()] == 720
    assert read["emudoc_wal_segments_pending", ()] == 3
    # Nothing archived yet, and so nothing to measure the wait from.
    monkeypatch.setattr(
        "app.metrics.archive",
        lambda connection: replace(stuck, archived=0, archived_at=None, pending=None),
    )
    read = figures(Holdings(get_settings()))
    assert read["emudoc_wal_archive_failures_total", ()] == 7
    assert ("emudoc_wal_last_archived_seconds", ()) not in read
    assert ("emudoc_wal_segments_pending", ()) not in read


def test_the_database_is_read_once_for_the_scrapers_that_ask_meanwhile(
    session, make_document, sample_pdf, monkeypatch
):
    holdings = Holdings(get_settings())
    assert MEASURE_SECONDS >= 15
    first = figures(holdings)
    make_document(sample_pdf)
    assert figures(holdings) == first
    monkeypatch.setattr("app.metrics.MEASURE_SECONDS", 0)
    assert figures(holdings)["emudoc_documents", ("queued",)] == 1


def test_a_reading_that_failed_serves_nothing_of_the_database(session, caplog):
    holdings = Holdings(get_settings())
    down = create_engine(make_url(get_settings().database_url).set(host="127.0.0.1", port=1))
    SessionLocal.configure(bind=down)
    try:
        registry = CollectorRegistry(auto_describe=True)
        # Registering asks what the collector serves, which must not read the database.
        with caplog.at_level("WARNING", logger="app.metrics"):
            registry.register(holdings)
            assert "could not read" not in caplog.text
            assert figures(holdings) == {}
    finally:
        SessionLocal.configure(bind=engine)
        down.dispose()
    assert "The metrics could not read the database:" in caplog.text
    assert "OperationalError" in caplog.text


# What no label holds


def test_no_label_holds_what_a_member_typed_or_who_they_are(session, client, monkeypatch):
    monkeypatch.setattr("app.metrics.MEASURE_SECONDS", 0)
    signup(client, email="grace.hopper@example.com")
    project = client.post("/api/projects", json={"name": "Merger with Initech"}).json()
    files = [("files", ("initech-term-sheet.md", b"# Term sheet\n\nThe price is confidential."))]
    client.post(f"/api/projects/{project['id']}/documents", files=files)
    client.get(f"/api/projects/{project['id']}/search", params={"q": "confidential price"})

    registry = CollectorRegistry()
    registry.register(Holdings(get_settings()))
    served = (generate_latest(REGISTRY) + generate_latest(registry)).decode()
    assert "emudoc_documents" in served
    for typed in ("grace", "hopper", "example.com", "Initech", "term-sheet", "confidential"):
        assert typed.lower() not in served.lower(), typed


# The alerting rules (deploy/prometheus/alerts.yml)


def test_every_metric_the_alerting_rules_name_is_served(session, services, monkeypatch):
    # promtool tests the rules against series the tests name, so a name both got wrong would
    # pass there: here the names are the ones a worker's exposition holds.
    taken = datetime(2026, 10, 18, 9, 30, tzinfo=UTC)
    monkeypatch.setattr(
        "app.metrics.archive",
        lambda connection: Archive(120, 7, taken, taken, 3, taken),
    )
    registry = CollectorRegistry()
    for collector in (
        Holdings(get_settings()),
        Lanes(Worker(services, concurrency=1)),
        Pool(engine, 15),
    ):
        registry.register(collector)
    # By family, since a counter whose labels come with its first count serves no sample yet.
    served = {"up"}  # Prometheus's own, from scraping.
    for family in [*REGISTRY.collect(), *registry.collect()]:
        served |= {family.name, f"{family.name}_total"} | {each.name for each in family.samples}
    rules = (ROOT_DIR / "deploy" / "prometheus" / "alerts.yml").read_text()
    named = set(re.findall(r"\b(emudoc_\w+|process_\w+|up(?=\{))", rules))
    assert len(named) >= 20
    assert named <= served, named - served
