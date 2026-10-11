"""The migrations build the schema the models describe; the app checks the result at start and
leaves applying them to `alembic upgrade head`.

Each test leaves the schema as it found it, since the whole run shares it.
"""

from concurrent.futures import ThreadPoolExecutor

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from alembic.util import CommandError
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from app.db import MIGRATION_LOCK, UPGRADE_COMMAND, Base, alembic_config, check_schema, engine
from app.main import create_app
from tests.conftest import build_schema, drop_schema


def current_revision() -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def table_names() -> set[str]:
    with engine.connect() as connection:
        return set(inspect(connection).get_table_names())


@pytest.fixture
def rebuilt_afterwards():
    """Put the migrated schema back once a test has torn it down."""
    yield
    drop_schema()
    build_schema()


def test_migrations_and_models_agree():
    # Autogenerate against the migrated database finds nothing to add: a model change without
    # its migration shows up here as the operations that migration would need.
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        differences = compare_metadata(context, Base.metadata)
    assert differences == []
    assert current_revision() is not None


def test_downgrade_removes_every_table(rebuilt_afterwards):
    command.downgrade(alembic_config(), "base")
    assert table_names() == {"alembic_version"}
    assert current_revision() is None


def test_app_refuses_a_database_that_is_not_at_the_newest_migration(services, rebuilt_afterwards):
    check_schema()

    command.downgrade(alembic_config(), "base")
    with pytest.raises(RuntimeError, match="has no schema yet") as error:
        check_schema()
    assert UPGRADE_COMMAND in str(error.value)
    # The check is what stops the app from serving.
    with pytest.raises(RuntimeError, match="has no schema yet"):
        with TestClient(create_app(services=services, start_worker=False)):
            pass

    # A database ahead of the code, after a rollback of a release, is named as such.
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO alembic_version VALUES ('0000')"))
    with pytest.raises(RuntimeError, match="is at schema revision 0000, and this version"):
        check_schema()


def test_database_made_before_migrations_cannot_be_upgraded(rebuilt_afterwards):
    # Builds before migrations made the tables with `create_all` and kept no version. The
    # first migration would fail on its first table; it says what to do instead.
    drop_schema()
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        Base.metadata.create_all(connection)

    with pytest.raises(CommandError, match="docker compose down -v"):
        build_schema()
    assert current_revision() is None
    assert table_names() == set(Base.metadata.tables)


def test_migration_runs_take_turns():
    # Replicas of a release all run `alembic upgrade head` at once; the second waits for the
    # first instead of altering the same tables, and then finds nothing left to do.
    with engine.connect() as holder, ThreadPoolExecutor(1) as pool:
        holder.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": MIGRATION_LOCK})
        waiting = pool.submit(build_schema)
        with pytest.raises(TimeoutError):
            waiting.result(timeout=0.5)
        holder.rollback()
        waiting.result(timeout=10)
    check_schema()
