"""What the settings refuse to run with: no database named, or ENVIRONMENT=production with
settings that only a development machine should have. Either stops the process with one
message, from uvicorn, alembic and the maintenance command alike."""

import os
import subprocess
import sys
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.config import (
    BACKEND_DIR,
    MISSING_DATABASE,
    ROOT_DIR,
    ModelPrice,
    get_settings,
    load_settings,
)
from app.main import create_app

A_DATABASE = "postgresql+psycopg://app:secret@db.internal:5432/emudoc"
# The compose container's password on a server elsewhere: a copied development setup.
WITH_THE_DEVELOPMENT_PASSWORD = "postgresql+psycopg://emudoc:emudoc@db.internal:5432/emudoc"
API_DOCS = {"/openapi.json", "/docs", "/redoc"}


def paths_of(app) -> set[str]:
    return {getattr(route, "path", "") for route in app.routes}


def test_a_process_without_a_database_stops_with_what_to_do(monkeypatch):
    monkeypatch.delenv("DATABASE_URL")
    with pytest.raises(SystemExit) as stop:
        load_settings(_env_file=None)
    assert stop.value.code == MISSING_DATABASE
    assert "DATABASE_URL is not set" in MISSING_DATABASE
    assert ".env.example" in MISSING_DATABASE


def test_a_blank_database_url_counts_as_unset(monkeypatch):
    # A `DATABASE_URL=` line that was cleared, or an empty variable, is the same mistake.
    monkeypatch.setenv("DATABASE_URL", "   ")
    with pytest.raises(SystemExit) as stop:
        load_settings(_env_file=None)
    assert stop.value.code == MISSING_DATABASE


def test_the_database_comes_from_the_environment_or_the_env_file(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", A_DATABASE)
    assert load_settings(_env_file=None).database_url == A_DATABASE

    monkeypatch.delenv("DATABASE_URL")
    env_file = tmp_path / ".env"
    env_file.write_text(f"DATABASE_URL={A_DATABASE}\n")
    assert load_settings(_env_file=env_file).database_url == A_DATABASE


def test_the_example_env_file_names_the_development_database(monkeypatch):
    # `cp .env.example .env` must keep working now that the code has no default.
    monkeypatch.delenv("DATABASE_URL")
    settings = load_settings(_env_file=ROOT_DIR / ".env.example")
    assert settings.database_url == "postgresql+psycopg://emudoc:emudoc@127.0.0.1:5437/emudoc"


def test_other_mistakes_keep_the_validation_error_that_names_the_setting():
    with pytest.raises(ValueError, match="environment"):
        load_settings(_env_file=None, environment="staging")


def test_model_prices_are_read_per_model_and_a_malformed_entry_is_refused():
    settings = load_settings(
        _env_file=None,
        model_prices=" google:gemini-2.5-flash-lite = 0.10/0.40 , openrouter:a/b=2/0,, c=0/0",
    )
    assert settings.prices == {
        "google:gemini-2.5-flash-lite": ModelPrice(Decimal("0.10"), Decimal("0.40")),
        "openrouter:a/b": ModelPrice(Decimal("2"), Decimal("0")),
        "c": ModelPrice(Decimal("0"), Decimal("0")),
    }
    assert load_settings(_env_file=None).prices == {}
    assert load_settings(_env_file=None, price_currency="EUR").price_currency == "EUR"

    for malformed in ["x", "x=1", "x=1/2/3", "x=a/b", "x=-1/2", "=1/2", "x=1/2 y=3/4"]:
        with pytest.raises(ValueError, match="MODEL_PRICES lists what each model costs"):
            load_settings(_env_file=None, model_prices=malformed)
    with pytest.raises(ValueError, match="price_currency"):
        load_settings(_env_file=None, price_currency="usd")


@pytest.mark.parametrize(
    "url", ["/var/lib/emudoc", "file://data", "s3://", "s3://bucket/prefix?region=x", "gs://b"]
)
def test_a_storage_url_names_a_directory_or_a_bucket(url):
    # A bare path, a relative file URL, a bucket-less s3 URL or another scheme is refused with
    # the two forms that work.
    with pytest.raises(ValueError, match="STORAGE_URL names a directory as file:///") as error:
        load_settings(_env_file=None, storage_url=url)
    assert repr(url) in str(error.value)


def test_the_storage_defaults_to_the_data_directory_in_the_checkout(monkeypatch):
    monkeypatch.delenv("STORAGE_URL")
    settings = load_settings(_env_file=None)
    assert settings.storage_url == (BACKEND_DIR / "data").as_uri()
    assert settings.storage_url.startswith("file:///")


def test_a_command_without_a_database_prints_the_sentence_and_no_traceback():
    # What an operator sees: the one message, exit status 1, no traceback. An empty variable
    # takes precedence over any `.env` beside the app, so the run does not depend on that file.
    env = dict(os.environ, DATABASE_URL="")
    run = subprocess.run(
        [sys.executable, "-m", "app.maintenance", "rebuild-embeddings"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert run.returncode == 1
    assert run.stderr.strip() == MISSING_DATABASE
    assert "Traceback" not in run.stderr


@pytest.fixture
def deployment(tmp_path, monkeypatch) -> dict[str, object]:
    """A complete production configuration, as arguments for `load_settings`.

    The suite's own environment is a development one, so every variable the checks look at is
    cleared from it first; what a test leaves out is then really missing.
    """
    for name in (
        "ENVIRONMENT",
        "DATABASE_URL",
        "STORAGE_URL",
        "LOCAL_ACCOUNT",
        "APP_URL",
        "SMTP_URL",
        "SCANNER_URL",
        "RUN_WORKER",
        "GOOGLE_PAID_TIER",
        "HOSTING_PROVIDER",
    ):
        monkeypatch.delenv(name, raising=False)
    return {
        "_env_file": None,
        "environment": "production",
        "database_url": A_DATABASE,
        "storage_url": (tmp_path / "data").as_uri(),
        "app_url": "https://emudoc.example.com",
        "smtp_url": "smtp://mailer:secret@smtp.example.com:587",
        "scanner_url": "clamd://clamav:3310",
        "google_paid_tier": True,
        "hosting_provider": "Hetzner Online GmbH, Germany",
    }


def test_a_complete_production_configuration_is_accepted(deployment):
    settings = load_settings(**deployment)
    assert settings.environment == "production"
    assert settings.production_problems() == []


def test_production_refuses_every_development_convenience_in_one_message(deployment):
    # An operator fixes the whole list in one go instead of one setting per restart.
    for name in (
        "storage_url",
        "app_url",
        "smtp_url",
        "scanner_url",
        "google_paid_tier",
        "hosting_provider",
    ):
        del deployment[name]
    deployment["database_url"] = WITH_THE_DEVELOPMENT_PASSWORD
    deployment["local_account"] = True

    with pytest.raises(SystemExit) as stop:
        load_settings(**deployment)
    message = stop.value.code
    assert message.startswith("ENVIRONMENT is production, but")
    for variable in (
        "DATABASE_URL",
        "LOCAL_ACCOUNT",
        "STORAGE_URL",
        "APP_URL",
        "SMTP_URL",
        "SCANNER_URL",
        "GOOGLE_PAID_TIER",
        "HOSTING_PROVIDER",
    ):
        assert variable in message
    # Each line says what to do; none repeats the database's address or password.
    assert "emudoc:emudoc" not in message and "db.internal" not in message


@pytest.mark.parametrize(
    ("change", "variable"),
    [
        ({"database_url": WITH_THE_DEVELOPMENT_PASSWORD}, "DATABASE_URL"),
        ({"local_account": True}, "LOCAL_ACCOUNT"),
        ({"storage_url": None}, "STORAGE_URL"),
        ({"app_url": ""}, "APP_URL"),
        ({"smtp_url": ""}, "SMTP_URL"),
        ({"scanner_url": ""}, "SCANNER_URL"),
        ({"google_paid_tier": False}, "GOOGLE_PAID_TIER"),
        ({"hosting_provider": " "}, "HOSTING_PROVIDER"),
    ],
)
def test_each_development_setting_is_refused_on_its_own(deployment, change, variable):
    for name, value in change.items():
        if value is None:
            del deployment[name]
        else:
            deployment[name] = value

    with pytest.raises(SystemExit) as stop:
        load_settings(**deployment)
    lines = stop.value.code.splitlines()
    assert len(lines) == 2 and variable in lines[1]


def test_a_spend_cap_without_prices_is_refused_in_production(deployment):
    # A cap measured against costs that are never recorded would hold nothing back.
    deployment["daily_spend_cap"] = "5"
    with pytest.raises(SystemExit) as stop:
        load_settings(**deployment)
    lines = stop.value.code.splitlines()
    assert len(lines) == 2 and "DAILY_SPEND_CAP" in lines[1] and "MODEL_PRICES" in lines[1]

    deployment["model_prices"] = "google:gemini-2.5-flash-lite=0.10/0.40"
    assert load_settings(**deployment).daily_spend_cap_micros == 5_000_000
    # Development is told nothing: it runs with whatever it has.
    del deployment["model_prices"]
    assert load_settings(**{**deployment, "environment": "development"}).production_problems() == []


def test_production_runs_without_a_scanner_only_when_told_to(deployment):
    # Unscanned uploads reach every member of a project, so leaving the scanner out is a
    # choice to state, not a default to fall into; development scans nothing by default.
    deployment["scanner_url"] = "off"
    assert load_settings(**deployment).production_problems() == []
    deployment["scanner_url"] = ""
    with pytest.raises(SystemExit) as stop:
        load_settings(**deployment)
    assert "set it to off to run without scanning on purpose" in stop.value.code
    assert load_settings(**{**deployment, "environment": "development"}).production_problems() == []


def test_production_takes_gemini_s_developer_api_only_on_the_paid_tier(deployment):
    # The free tier lets Google use what it is sent to improve its products, and a free key
    # looks like a paid one, so the operator says which it is; Vertex AI needs no such word.
    deployment["google_paid_tier"] = False
    with pytest.raises(SystemExit) as stop:
        load_settings(**deployment)
    (line,) = stop.value.code.splitlines()[1:]
    assert "AGENT_MODEL and INDEX_MODEL use the Gemini Developer API" in line
    assert "set GOOGLE_PAID_TIER=true, or use Vertex AI as google-cloud:<model>" in line

    deployment["agent_model"] = "anthropic:claude-opus-5-5"
    with pytest.raises(SystemExit) as stop:
        load_settings(**deployment)
    assert "INDEX_MODEL uses the Gemini Developer API" in stop.value.code
    deployment["index_model"] = "google-cloud:gemini-2.5-flash-lite"
    assert load_settings(**deployment).production_problems() == []
    deployment["index_model"] = "openrouter:google/gemini-2.5-flash-lite"
    assert load_settings(**deployment).production_problems() == []
    # A switched-off provider is still the one the deployment would use once it is back.
    deployment.update(index_model="google:gemini-2.5-flash-lite", disabled_providers="google")
    with pytest.raises(SystemExit):
        load_settings(**deployment)


def test_production_sends_nothing_off_its_own_network_unencrypted(deployment):
    # A managed database, a scanner on another host and a hosted embeddings endpoint are each
    # reached across networks nobody in the deployment controls.
    deployment.update(
        database_url="postgresql+psycopg://app:secret@db.example.com:5432/emudoc",
        scanner_url="clamd://clamav.example.com:3310",
        embedding_base_url="http://embeddings.example.com/v1",
    )
    with pytest.raises(SystemExit) as stop:
        load_settings(**deployment)
    lines = stop.value.code.splitlines()[1:]
    assert len(lines) == 3
    assert "DATABASE_URL connects to db.example.com without sslmode=verify-full" in lines[0]
    assert "SCANNER_URL streams every upload to the ClamAV daemon at clamav.example.com" in lines[1]
    assert (
        "EMBEDDING_BASE_URL sends every passage as it is indexed and every search to " in lines[2]
    )
    assert "secret" not in stop.value.code

    # Verified, over HTTPS, or inside the network, the same pieces are fit.
    deployment.update(
        database_url="postgresql+psycopg://app:secret@db.example.com:5432/emudoc"
        "?sslmode=verify-full&sslrootcert=system",
        scanner_url="clamd://clamav:3310",
        embedding_base_url="https://embeddings.example.com/v1",
    )
    assert load_settings(**deployment).production_problems() == []


def test_the_api_runs_the_worker_itself_in_development_only(deployment, monkeypatch):
    # Unset, the choice follows the environment: one process in development, and in production
    # a `python -m app.worker` of its own. Either can be said outright; a blank line is unset.
    assert load_settings(**deployment).run_worker is False
    assert load_settings(**deployment, run_worker=True).run_worker is True
    deployment["environment"] = "development"
    assert load_settings(**deployment).run_worker is True
    assert load_settings(**deployment, run_worker=False).run_worker is False
    monkeypatch.setenv("RUN_WORKER", "")
    assert load_settings(**deployment).run_worker is True


def test_a_process_that_runs_the_worker_needs_a_pool_for_its_threads(deployment):
    # Each indexing and answering thread holds a connection while it works, and the heartbeat,
    # housekeeper and alerter take one now and then; a pool that cannot hold them all would
    # fail jobs for a reason the operator set, so the settings are refused with the arithmetic.
    deployment.update(run_worker=True, worker_concurrency=5, answer_concurrency=8)
    with pytest.raises(
        ValueError, match="WORKER_CONCURRENCY is 5 and ANSWER_CONCURRENCY is 8"
    ) as error:
        load_settings(**deployment)
    assert "need 16 database connections" in str(error.value)
    assert "DB_POOL_SIZE + DB_MAX_OVERFLOW allows 15" in str(error.value)
    assert load_settings(**deployment, db_max_overflow=11).worker_concurrency == 5
    deployment["worker_concurrency"] = 4
    assert load_settings(**deployment).db_pool_size == 5
    # An API replica that runs no worker has no indexing threads to hold connections.
    deployment.update(run_worker=False, worker_concurrency=50)
    assert load_settings(**deployment).run_worker is False


def test_development_runs_with_everything_production_refuses(deployment):
    deployment.update(
        environment="development",
        database_url="postgresql+psycopg://emudoc:emudoc@db.example.com/emudoc",
        scanner_url="clamd://clamav.example.com:3310",
        embedding_base_url="http://embeddings.example.com/v1",
        local_account=True,
        app_url="",
        smtp_url="",
        google_paid_tier=False,
        hosting_provider="",
    )
    del deployment["storage_url"]
    assert load_settings(**deployment).production_problems() == []


def test_a_production_command_with_development_settings_prints_the_list_and_no_traceback():
    # The suite's environment is development's: the test database with the compose password,
    # LOCAL_ACCOUNT on, and here no app address or mail relay either.
    env = dict(os.environ, ENVIRONMENT="production", APP_URL="", SMTP_URL="")
    run = subprocess.run(
        [sys.executable, "-m", "app.maintenance", "rebuild-embeddings"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert run.returncode == 1
    assert run.stderr.startswith("ENVIRONMENT is production, but")
    for variable in ("DATABASE_URL", "LOCAL_ACCOUNT", "APP_URL", "SMTP_URL"):
        assert variable in run.stderr
    assert "Traceback" not in run.stderr


@pytest.fixture
def production(monkeypatch):
    """The running suite's settings, switched to production for the app's own start-up checks.
    The settings object is cached and shared, so the attribute is patched, never the object."""
    monkeypatch.setattr(get_settings(), "environment", "production")


def test_a_production_start_needs_the_model(production, services, monkeypatch):
    # Development degrades to parsing and search without a key; a deployment stops and says so.
    for name in ("GOOGLE_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match="production does not start without it") as error:
        with TestClient(create_app()):
            pass
    assert "Set GOOGLE_API_KEY in the environment" in str(error.value)

    # With a usable model the same start goes through.
    with TestClient(create_app(services=services)) as client:
        assert client.get("/api/health").json() == {"status": "ok"}


def test_a_production_start_runs_with_a_switched_off_model(production, monkeypatch, caplog):
    # The kill switch stops the spending, not the app: the process starts, says what is off,
    # and the status route shows the users why the model is missing.
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr(get_settings(), "disabled_providers", "google")
    with caplog.at_level("WARNING", logger="app.startup"):
        with TestClient(create_app()) as client:
            status = client.get("/api/status").json()["llm"]
    assert status["available"] is False
    assert status["error"].startswith("google:gemini-2.5-flash-lite is switched off:")
    assert "is switched off" in caplog.text and "runs without the model" in caplog.text


def test_the_interactive_api_docs_are_served_in_development_only(client, services, production):
    # `client` was built before the switch to production, so it is the development app.
    assert client.get("/openapi.json").json()["info"]["title"] == "Emudoc"
    assert client.get("/docs").status_code == 200
    assert API_DOCS <= paths_of(client.app)

    app = create_app(services=services)
    assert not API_DOCS & paths_of(app)
