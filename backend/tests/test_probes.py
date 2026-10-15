"""What an orchestrator, a load balancer and an uptime service ask the API: whether the process
is alive (`/api/livez`, touching nothing), whether it can serve (`/api/health`, the database),
both with HEAD as well as GET, and what the status line asks of the embedding endpoint, which
every open tab polls."""

import threading
from dataclasses import replace

import anyio.to_thread
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

import app.embeddings as embeddings
from app.config import get_settings
from app.db import SessionLocal, engine
from app.embeddings import Embedder
from app.main import NOT_READY, create_app
from tests.conftest import APP_HEADERS


@pytest.fixture
def database_down(client):
    """The app's sessions over an engine whose database refuses every connection, once the
    client has started."""
    url = make_url(get_settings().database_url).set(host="127.0.0.1", port=1)
    down = create_engine(url)
    SessionLocal.configure(bind=down)
    try:
        yield
    finally:
        SessionLocal.configure(bind=engine)
        down.dispose()


def test_liveness_touches_nothing_and_readiness_the_database(client, database_down, caplog):
    # An outage of the database must not have every replica restarted, which cures nothing;
    # it takes them out of the load balancer's rotation instead.
    assert client.get("/api/livez").json() == {"status": "ok"}
    with caplog.at_level("WARNING", logger="app.main"):
        response = client.get("/api/health")
    assert (response.status_code, response.json()) == (503, {"detail": NOT_READY})
    # One line per probe for the operator, and nothing in the answer about the setup.
    (line,) = [record.getMessage() for record in caplog.records if record.name == "app.main"]
    assert line.startswith(
        "Not ready: the database cannot be reached (psycopg.OperationalError: connection failed: "
    )
    assert "\n" not in line and "Traceback" not in caplog.text
    assert "127.0.0.1" not in response.text


def test_liveness_answers_while_every_thread_is_busy(client):
    # A burst of slow requests can hold every thread of the pool for a while without the
    # process being stuck, and an orchestrator must not restart it for that. Readiness waits
    # for a thread, as every other route does.
    holder = object()
    limiter = client.portal.call(anyio.to_thread.current_default_thread_limiter)
    tokens = limiter.total_tokens

    async def fill() -> None:
        limiter.total_tokens = 1
        await limiter.acquire_on_behalf_of(holder)

    async def empty() -> None:
        limiter.release_on_behalf_of(holder)
        limiter.total_tokens = tokens

    answers: dict[str, httpx.Response] = {}

    def ask(path: str) -> None:
        answers[path] = client.get(path)

    client.portal.call(fill)
    try:
        for path in ("/api/livez", "/api/health"):
            thread = threading.Thread(target=ask, args=(path,), daemon=True)
            thread.start()
            thread.join(2)
        assert answers["/api/livez"].status_code == 200
        assert "/api/health" not in answers
    finally:
        client.portal.call(empty)


def test_uptime_services_may_ask_with_head(client, services, tmp_path, monkeypatch):
    for path in ("/api/livez", "/api/health"):
        assert client.head(path).status_code == 200, path

    # The page too, which an uptime service probes as often as the API.
    (tmp_path / "index.html").write_text("<!doctype html><title>Emudoc</title>")
    (tmp_path / "assets").mkdir()
    monkeypatch.setattr("app.main.FRONTEND_DIST", tmp_path)
    with TestClient(create_app(services=services), headers=APP_HEADERS) as page:
        for path in ("/", "/projects/1"):
            response = page.head(path)
            assert response.status_code == 200, path
            assert response.headers["content-type"].startswith("text/html")
            assert response.content == b""


def test_the_status_line_asks_the_embedding_endpoint_once_a_minute(session, services, monkeypatch):
    asked: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        vector = [0.5] * get_settings().embedding_dim
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": vector}]})

    embedder = Embedder(get_settings())
    embedder._client = httpx.Client(
        base_url="http://embeddings", transport=httpx.MockTransport(answer)
    )
    app = create_app(services=replace(services, embedder=embedder))
    with TestClient(app, headers=APP_HEADERS) as client:
        # Every tab polls it every minute, and every pane that opens asks too.
        for _ in range(3):
            assert client.get("/api/status").json()["embeddings"]["ok"] is True
        assert len(asked) == 1

        monkeypatch.setattr(embeddings, "CHECK_SECONDS", 0)
        assert client.get("/api/status").json()["embeddings"]["ok"] is True
        assert len(asked) == 2
