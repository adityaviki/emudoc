"""The settings name no database by themselves: a process that is not told one stops and says
what to do, from uvicorn, alembic and the maintenance command alike."""

import os
import subprocess
import sys

import pytest

from app.config import BACKEND_DIR, MISSING_DATABASE, ROOT_DIR, load_settings

A_DATABASE = "postgresql+psycopg://app:secret@db.internal:5432/emudoc"


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
