"""What the settings refuse to run with: no database named, or ENVIRONMENT=production with
settings that only a development machine should have. Either stops the process with one
message, from uvicorn, alembic and the maintenance command alike."""

import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from app.config import BACKEND_DIR, MISSING_DATABASE, ROOT_DIR, get_settings, load_settings
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
        "RUN_WORKER",
    ):
        monkeypatch.delenv(name, raising=False)
    return {
        "_env_file": None,
        "environment": "production",
        "database_url": A_DATABASE,
        "storage_url": (tmp_path / "data").as_uri(),
        "app_url": "https://emudoc.example.com",
        "smtp_url": "smtp://mailer:secret@smtp.example.com:587",
    }


def test_a_complete_production_configuration_is_accepted(deployment):
    settings = load_settings(**deployment)
    assert settings.environment == "production"
    assert settings.production_problems() == []


def test_production_refuses_every_development_convenience_in_one_message(deployment):
    # An operator fixes the whole list in one go instead of one setting per restart.
    for name in ("storage_url", "app_url", "smtp_url"):
        del deployment[name]
    deployment["database_url"] = WITH_THE_DEVELOPMENT_PASSWORD
    deployment["local_account"] = True

    with pytest.raises(SystemExit) as stop:
        load_settings(**deployment)
    message = stop.value.code
    assert message.startswith("ENVIRONMENT is production, but")
    for variable in ("DATABASE_URL", "LOCAL_ACCOUNT", "STORAGE_URL", "APP_URL", "SMTP_URL"):
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
    # Each indexing thread holds a connection while it works, and the heartbeat, housekeeper
    # and alerter take one now and then; a pool that cannot hold them all would fail jobs for a
    # reason the operator set, so the settings are refused with the arithmetic.
    deployment.update(run_worker=True, worker_concurrency=13)
    with pytest.raises(ValueError, match="WORKER_CONCURRENCY is 13") as error:
        load_settings(**deployment)
    assert "need 16 database connections" in str(error.value)
    assert "DB_POOL_SIZE + DB_MAX_OVERFLOW allows 15" in str(error.value)
    assert load_settings(**deployment, db_max_overflow=11).worker_concurrency == 13
    deployment["worker_concurrency"] = 12
    assert load_settings(**deployment).db_pool_size == 5
    # An API replica that runs no worker has no indexing threads to hold connections.
    deployment.update(run_worker=False, worker_concurrency=50)
    assert load_settings(**deployment).run_worker is False


def test_development_runs_with_everything_production_refuses(deployment):
    deployment.update(
        environment="development",
        database_url=WITH_THE_DEVELOPMENT_PASSWORD,
        local_account=True,
        app_url="",
        smtp_url="",
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


def test_the_interactive_api_docs_are_served_in_development_only(client, services, production):
    # `client` was built before the switch to production, so it is the development app.
    assert client.get("/openapi.json").json()["info"]["title"] == "Emudoc"
    assert client.get("/docs").status_code == 200
    assert API_DOCS <= paths_of(client.app)

    app = create_app(services=services)
    assert not API_DOCS & paths_of(app)
