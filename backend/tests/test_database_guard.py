"""The suite destroys whatever database it runs against, so it refuses every one but the local
test database, before anything connects."""

import os
import subprocess
import sys

import pytest

from app.config import BACKEND_DIR
from app.db import engine
from tests.conftest import ALLOW_REMOTE, TEST_DATABASE, refuse_unless_test_database

LOCAL = "postgresql+psycopg://emudoc:emudoc@127.0.0.1:5437"
ELSEWHERE = "postgresql+psycopg://emudoc:secret@db.example.com:5432"


def test_the_running_suite_is_on_the_test_database():
    refuse_unless_test_database(engine.url)
    assert engine.url.database == TEST_DATABASE


@pytest.mark.parametrize(
    "url",
    [
        f"{LOCAL}/emudoc",
        f"{LOCAL}/postgres",
        f"{LOCAL}/emudoc_tests",
        "postgresql+psycopg:///emudoc",
    ],
)
def test_another_database_on_this_machine_is_refused(url):
    # Only the app's settings being read before conftest.py set the environment gets here, so
    # the message says what to do about that.
    with pytest.raises(pytest.UsageError, match=f"is not the {TEST_DATABASE} database") as error:
        refuse_unless_test_database(url)
    assert "tests/conftest.py" in str(error.value)


@pytest.mark.parametrize(
    "server",
    [
        "emudoc:emudoc@127.0.0.1:5437",
        "emudoc:emudoc@127.0.0.2:5437",
        "emudoc:emudoc@localhost",
        "emudoc:emudoc@[::1]:5437",
        "",
    ],
)
def test_the_test_database_on_this_machine_passes(server, monkeypatch):
    monkeypatch.delenv(ALLOW_REMOTE, raising=False)
    refuse_unless_test_database(f"postgresql+psycopg://{server}/{TEST_DATABASE}")


@pytest.mark.parametrize("host", ["db.example.com", "10.0.0.5", "db"])
def test_a_server_elsewhere_needs_the_opt_in(host, monkeypatch):
    url = f"postgresql+psycopg://emudoc:secret@{host}:5432/{TEST_DATABASE}"
    monkeypatch.delenv(ALLOW_REMOTE, raising=False)
    with pytest.raises(pytest.UsageError, match=ALLOW_REMOTE) as error:
        refuse_unless_test_database(url)
    assert f"{host}:5432/{TEST_DATABASE}, a server that is not on this machine" in str(error.value)
    assert "secret" not in str(error.value)

    monkeypatch.setenv(ALLOW_REMOTE, "1")
    refuse_unless_test_database(url)
    # The opt-in covers the server, never the database.
    with pytest.raises(pytest.UsageError, match=f"is not the {TEST_DATABASE} database"):
        refuse_unless_test_database(f"{ELSEWHERE}/emudoc")


def test_the_run_stops_before_connecting_to_a_server_elsewhere():
    # What a developer sees who points TEST_DATABASE_SERVER at a shared server: pytest stops
    # while loading tests/conftest.py and collects nothing. The host does not exist, so an
    # attempt to connect would have surfaced as a different error.
    env = {name: value for name, value in os.environ.items() if name != ALLOW_REMOTE}
    env["TEST_DATABASE_SERVER"] = "emudoc:secret@db.internal:5432"
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert run.returncode == pytest.ExitCode.USAGE_ERROR
    assert f"db.internal:5432/{TEST_DATABASE}, a server that is not on this machine" in run.stderr
    assert ALLOW_REMOTE in run.stderr
    assert "secret" not in run.stdout + run.stderr
    assert "test_database_guard.py" not in run.stdout
