"""The connections that leave the deployment's own network, and what production asks of them
(app/network.py): a database reached with its certificate checked, HTTPS to the models, the
bucket and the embeddings, and the scanner kept inside."""

import threading
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.db import make_engine
from app.jobs import listen
from app.main import create_app
from app.network import (
    database_problem,
    plain_http_problem,
    scanner_problem,
    trust_store_problem,
)
from app.startup import check_transport
from app.storage import LocalStorage, S3Storage
from tests.conftest import FakeS3Client

VERIFIED = "?sslmode=verify-full&sslrootcert=system"
DIRECTORY = LocalStorage(Path("unused"))


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://app:secret@db.internal:5432/emudoc",
        "postgresql+psycopg://app:secret@10.0.0.5/emudoc",
        "postgresql+psycopg://app:secret@db/emudoc",
        "postgresql+psycopg://app:secret@/emudoc",
        "postgresql+psycopg://app:secret@/emudoc?host=/run/postgresql",
        f"postgresql+psycopg://app:secret@db.example.com/emudoc{VERIFIED}",
        f"postgresql+psycopg://app:secret@[2a01:4f8::1]/emudoc{VERIFIED}",
    ],
)
def test_a_database_inside_the_network_or_verified_is_accepted(url):
    assert database_problem(url) is None


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://app:secret@db.example.com/emudoc",
        # Encrypted to whoever answers, or to any server the authority signed for.
        "postgresql+psycopg://app:secret@db.example.com/emudoc?sslmode=require",
        "postgresql+psycopg://app:secret@db.example.com/emudoc?sslmode=verify-ca",
        "postgresql+psycopg://app:secret@db.example.com/emudoc?sslmode=prefer",
        "postgresql+psycopg://app:secret@[2a01:4f8::1]/emudoc",
        # One host of several outside, by name and port.
        "postgresql+psycopg://app:secret@/emudoc?host=db.internal:5432,db.example.com:5432",
        "postgresql+psycopg://app:secret@db.example.com/emudoc?sslmode=verify-full&sslmode=require",
    ],
)
def test_a_database_off_the_network_needs_its_certificate_checked(url):
    problem = database_problem(url)
    assert problem is not None
    assert problem.startswith("DATABASE_URL connects to ")
    assert "add ?sslmode=verify-full&sslrootcert=system to the URL" in problem
    # The sentence goes to a log: the password stays out of it.
    assert "secret" not in problem


def test_the_sentence_names_the_host_that_leaves_the_network():
    problem = database_problem("postgresql+psycopg://app:secret@[2a01:4f8::1]:5432/emudoc")
    assert problem is not None and problem.startswith("DATABASE_URL connects to 2a01:4f8::1 ")


@pytest.mark.parametrize(
    ("url", "refused"),
    [
        ("http://embeddings.example.com/v1", True),
        ("http://95.216.1.9:11434/v1", True),
        ("https://embeddings.example.com/v1", False),
        ("http://127.0.0.1:11434/v1", False),
        ("http://ollama:11434/v1", False),
        ("http://gpu.internal/v1", False),
        ("", False),
    ],
)
def test_plain_http_is_refused_only_off_the_network(url, refused):
    problem = plain_http_problem("EMBEDDING_BASE_URL", url, "every passage")
    assert (problem is not None) == refused
    if refused:
        assert problem.startswith("EMBEDDING_BASE_URL sends every passage to ")
        assert "over plain HTTP; use its https:// address" in problem


@pytest.mark.parametrize(
    ("address", "refused"),
    [
        (("clamav.example.com", 3310), True),
        (("95.216.1.9", 3310), True),
        (("clamav", 3310), False),
        (("10.0.0.7", 3310), False),
        ("/run/clamav/clamd.ctl", False),
        (None, False),
    ],
)
def test_the_scanner_stays_inside_the_network(address, refused):
    problem = scanner_problem(address)
    assert (problem is not None) == refused
    if refused:
        assert "clamd's protocol has no encryption" in problem


def production(**changes: object) -> Settings:
    values = {"environment": "production", **changes}
    return Settings(
        _env_file=None, database_url="postgresql+psycopg://app:secret@db.internal/emudoc", **values
    )


def test_a_model_s_address_off_the_network_needs_https():
    settings = production(agent_model="openai:gpt-5.1", index_model="openai-chat:gpt-5.1")
    environ = {"OPENAI_BASE_URL": "http://models.example.com/v1"}
    with pytest.raises(RuntimeError) as error:
        check_transport(settings, DIRECTORY, environ)
    message = str(error.value)
    assert message.startswith(
        "ENVIRONMENT is production, but these would send the documents unencrypted across a "
        "network the deployment does not own:\n  - OPENAI_BASE_URL sends"
    )
    # One line for the one variable both models read.
    assert message.count("OPENAI_BASE_URL") == 1

    for fit in ("https://models.example.com/v1", "http://vllm.internal:8000/v1"):
        check_transport(settings, DIRECTORY, {"OPENAI_BASE_URL": fit})
    # An address no configured model reads is not this check's business; a fallback's is.
    check_transport(production(), DIRECTORY, environ)
    with pytest.raises(RuntimeError, match="OPENAI_BASE_URL sends"):
        check_transport(production(agent_fallback_model="openai:gpt-5.1"), DIRECTORY, environ)


def test_a_bucket_off_the_network_needs_https():
    storage = S3Storage(FakeS3Client(origin="http://s3.example.com"), "bucket")
    with pytest.raises(RuntimeError, match="AWS_ENDPOINT_URL sends every upload, and every page"):
        check_transport(production(), storage, {})
    for origin in ("https://s3.example.com", "http://minio:9000", "http://127.0.0.1:9000"):
        check_transport(production(), S3Storage(FakeS3Client(origin=origin), "bucket"), {})
    # Development connects as it is told.
    check_transport(production(environment="development"), storage, {})


def test_production_does_not_start_with_a_model_reached_over_plain_http(
    session, services, monkeypatch
):
    monkeypatch.setattr(get_settings(), "environment", "production")
    monkeypatch.setattr(get_settings(), "agent_model", "openai:gpt-5.1")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://models.example.com/v1")
    with pytest.raises(RuntimeError, match="OPENAI_BASE_URL sends the documents and the"):
        with TestClient(create_app(services=services)):
            pass

    monkeypatch.setenv("OPENAI_BASE_URL", "https://models.example.com/v1")
    with TestClient(create_app(services=services)) as client:
        assert client.get("/api/health").json()["status"] == "ok"


def test_the_pool_and_the_listener_keep_the_url_s_tls_parameters(monkeypatch):
    # sslmode and sslrootcert on DATABASE_URL are the whole configuration, so both kinds of
    # connection must hand them to libpq.
    url = f"postgresql+psycopg://app:secret@db.example.com:5432/emudoc{VERIFIED}"
    engine = make_engine(Settings(_env_file=None, database_url=url))
    _, arguments = engine.dialect.create_connect_args(engine.url)
    assert arguments["sslmode"] == "verify-full" and arguments["sslrootcert"] == "system"

    seen: list[str] = []

    def connect(conninfo: str, **_: object):
        seen.append(conninfo)
        raise psycopg.OperationalError("not here")

    monkeypatch.setattr(get_settings(), "database_url", url)
    monkeypatch.setattr(get_settings(), "database_pooler", "")
    monkeypatch.setattr(get_settings(), "database_listen_url", "")
    monkeypatch.setattr(psycopg, "connect", connect)
    with pytest.raises(psycopg.OperationalError):
        listen(threading.Event(), lambda: None, 0.1)
    (conninfo,) = seen
    assert conninfo.startswith("postgresql://app:secret@db.example.com:5432/emudoc?")
    assert "sslmode=verify-full" in conninfo and "sslrootcert=system" in conninfo


def test_the_trust_store_sentence_names_this_machine_s_bundle(monkeypatch, tmp_path):
    # The operator copies the path: the bundle this machine has, or, on a machine with neither
    # (the drill's container, say), both with the systems that keep them there.
    debian, fedora = tmp_path / "ca-certificates.crt", tmp_path / "ca-bundle.crt"
    monkeypatch.setattr(
        "app.network.SYSTEM_BUNDLES", ((str(debian), "Debian"), (str(fedora), "Fedora"))
    )
    url = f"postgresql+psycopg://app:secret@db.example.com/emudoc{VERIFIED}"
    unknown = trust_store_problem("DATABASE_URL", url, {}, bundled_libpq=True)
    assert unknown.endswith(
        f"set SSL_CERT_FILE to the system's bundle: {debian} on Debian, {fedora} on Fedora "
        "(the image sets it)."
    )
    fedora.write_text("")
    found = trust_store_problem("DATABASE_URL", url, {}, bundled_libpq=True)
    assert found.endswith(
        f"set SSL_CERT_FILE to this machine's bundle, {fedora} (the image sets it)."
    )
    # A URL that is not one is the engine's to refuse.
    assert trust_store_problem("DATABASE_URL", "not a url", {}, bundled_libpq=True) is None
