"""What every process checks before it serves or works (app/startup.py) that no other module's
tests cover: a temporary directory to write uploads in, in development a word on a database
URL whose certificate check would fail, and an embeddings endpoint that answers."""

import os
import tempfile
from dataclasses import replace

import httpx
import pytest

from app.config import get_settings
from app.startup import (
    check_embedder,
    check_model,
    check_temporary_directory,
    prepare,
    warn_about_trust_store,
)
from tests.conftest import FakeLLM
from tests.test_embeddings import embedder, refuse, vector

pytestmark = pytest.mark.skipif(os.geteuid() == 0, reason="root writes in any directory")


def test_no_temporary_directory_to_write_in_stops_the_start(monkeypatch, tmp_path, services):
    # A read-only filesystem without a tmpfs: the directory Python settled on cannot be written.
    locked = tmp_path / "tmp"
    locked.mkdir(mode=0o500)
    monkeypatch.setattr(tempfile, "tempdir", str(locked))
    with pytest.raises(RuntimeError, match="Mount a tmpfs at /tmp, or set TMPDIR") as refusal:
        prepare(get_settings(), services)
    assert "Permission denied" in str(refusal.value)

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    check_temporary_directory()


def test_a_tmpdir_python_passed_over_is_named(monkeypatch, tmp_path, caplog):
    locked = tmp_path / "encrypted"
    locked.mkdir(mode=0o500)
    monkeypatch.setenv("TMPDIR", str(locked))
    # Python chooses again, as a process that starts does.
    monkeypatch.setattr(tempfile, "tempdir", None)
    with caplog.at_level("WARNING", logger="app.startup"):
        check_temporary_directory()
    chosen = tempfile.gettempdir()
    assert chosen != str(locked)
    assert f"TMPDIR names {locked}, which this process cannot write in" in caplog.text
    assert f"go to {chosen} instead" in caplog.text

    caplog.clear()
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setattr(tempfile, "tempdir", None)
    with caplog.at_level("WARNING", logger="app.startup"):
        check_temporary_directory()
    assert tempfile.gettempdir() == str(tmp_path)
    assert caplog.text == ""


def test_development_says_why_the_system_s_authorities_would_not_be_found(
    monkeypatch, caplog, services
):
    # Production refuses the setting (test_config.py); development starts, and the warning
    # comes before the first connection fails with "certificate verify failed".
    monkeypatch.setattr("app.startup.BUNDLED_LIBPQ", True)
    for name in ("SSL_CERT_FILE", "SSL_CERT_DIR"):
        monkeypatch.delenv(name, raising=False)
    settings = get_settings()
    trusting = "postgresql+psycopg://app:secret@db.example.com/emudoc?sslrootcert=system"
    monkeypatch.setattr(settings, "database_listen_url", trusting)
    with caplog.at_level("WARNING", logger="app.startup"):
        prepare(settings, services)
    assert "DATABASE_LISTEN_URL trusts the system's certificate authorities" in caplog.text
    assert "set SSL_CERT_FILE to" in caplog.text

    caplog.clear()
    with caplog.at_level("WARNING", logger="app.startup"):
        warn_about_trust_store(settings, {"SSL_CERT_DIR": "/"})
    assert caplog.text == ""


# The embeddings endpoint, asked for one vector at start.

# What Ollama answers for a model it has not pulled; the message stays out of what is said.
NOT_PULLED = {
    "error": {
        "message": 'model "nomic-embed-text" not found, try pulling it first',
        "type": "not_found_error",
    }
}


@pytest.fixture
def production(monkeypatch):
    monkeypatch.setattr(get_settings(), "environment", "production")


def test_production_refuses_an_endpoint_that_turns_the_request_down(production, services):
    # A wrong address, model, size or key fails every document's embed stage and every
    # search's vectors, so the deployment stops at once with the settings to look at.
    services = replace(
        services, embedder=embedder(lambda request: httpx.Response(404, json=NOT_PULLED))
    )
    with pytest.raises(RuntimeError) as refusal:
        prepare(get_settings(), services)
    message = str(refusal.value)
    assert message.startswith(
        "The embeddings endpoint turns requests down, and production does not start without "
        "it. Embedding endpoint returned 404 (error code not_found_error). Check that "
        "EMBEDDING_BASE_URL is the address /embeddings is under"
    )
    assert "such as http://embeddings:8080/v1 for the compose file's server" in message
    assert "(Ollama's after `ollama pull`) as vectors of EMBEDDING_DIM" in message
    assert "try pulling it first" not in message

    def short(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.5] * 1024}]})

    with pytest.raises(RuntimeError, match="returns 1024-dimensional vectors but EMBEDDING_DIM"):
        check_embedder(get_settings(), embedder(short))


def test_an_endpoint_out_of_reach_lets_production_start(production, services, caplog):
    # The compose file's server may still be starting beside the app; the stage tries again.
    with caplog.at_level("WARNING", logger="app.startup"):
        prepare(get_settings(), replace(services, embedder=embedder(refuse)))
    assert (
        "Embedding endpoint unreachable at http://embeddings. Until it answers, search matches "
        "keywords only, and the embed stage of each document indexed meanwhile is tried again."
    ) in caplog.messages


def test_development_starts_whatever_the_endpoint_says(services, caplog):
    with caplog.at_level("WARNING", logger="app.startup"):
        check_embedder(get_settings(), embedder(lambda request: httpx.Response(401)))
        check_embedder(get_settings(), embedder(refuse))
    refused, unreachable = caplog.messages
    assert refused.startswith(
        "Embedding endpoint returned 401. Search matches keywords only. Check that "
        "EMBEDDING_BASE_URL"
    )
    assert unreachable.startswith("Embedding endpoint unreachable at http://embeddings.")


def test_the_start_names_the_version_first(services, monkeypatch, caplog):
    # A refused start is read against the release that refused, so the line comes before any
    # check that could refuse.
    monkeypatch.setattr("app.startup.VERSION", "0123456789abcdef0123456789abcdef01234567")
    with caplog.at_level("INFO", logger="app.startup"):
        prepare(get_settings(), services)
    assert caplog.messages[0] == "Starting Emudoc 0123456789abcdef0123456789abcdef01234567"


def test_the_start_names_the_endpoint_that_answers(production, caplog):
    with caplog.at_level("INFO", logger="app.startup"):
        check_embedder(get_settings(), embedder(vector))
    assert "Passages are embedded by nomic-embed-text at http://127.0.0.1:9/v1" in caplog.messages


def test_a_start_refused_for_its_endpoint_queues_nothing_to_embed_again(
    production, services, session, indexed
):
    # The passages were embedded by another model than EMBEDDING_MODEL names, as after a
    # change of it, or a mistyped one that the endpoint does not serve. Queued before the
    # refusal, every document would be embedded again at the next start, whatever was fixed.
    refusing = embedder(lambda request: httpx.Response(404, json=NOT_PULLED))
    with pytest.raises(RuntimeError):
        prepare(get_settings(), replace(services, embedder=refusing))
    session.refresh(indexed)
    assert indexed.stages["embed"]["status"] == "done"

    prepare(get_settings(), services)
    session.refresh(indexed)
    assert indexed.stages["embed"]["status"] == "pending"


# The models: which do the work and which take it over, built at start and never asked.


def test_the_start_names_the_models_and_their_fallbacks(monkeypatch, caplog):
    monkeypatch.setattr(get_settings(), "agent_fallback_model", "anthropic:claude-haiku-5-5")
    with caplog.at_level("INFO", logger="app.startup"):
        check_model(get_settings(), FakeLLM())
    assert caplog.messages == [
        "Documents are indexed by google:gemini-2.5-flash-lite",
        "Questions are answered by google:gemini-2.5-flash-lite, and by "
        "anthropic:claude-haiku-5-5 when it cannot answer",
    ]


def test_production_says_what_stops_while_a_model_without_a_fallback_cannot_answer(
    production, monkeypatch, caplog
):
    monkeypatch.setattr(get_settings(), "agent_fallback_model", "anthropic:claude-haiku-5-5")
    with caplog.at_level("WARNING", logger="app.startup"):
        check_model(get_settings(), FakeLLM())
    assert caplog.messages == [
        "INDEX_MODEL has no fallback, so while google:gemini-2.5-flash-lite is down, refuses "
        "the key or has been retired, no document gets its summaries, facts or scanned pages "
        "read; set INDEX_FALLBACK_MODEL, best to a model of another provider."
    ]
