"""The migrations build the schema the models describe, and a database from before them is
taken over rather than rebuilt.

Each test leaves the schema as it found it, since the whole run shares it.
"""

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect, text

from app.db import FIRST_REVISION, Base, alembic_config, engine, migrate
from tests.conftest import drop_schema


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
    migrate()


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


def test_database_made_before_migrations_is_stamped(rebuilt_afterwards):
    # Builds before migrations made the tables with `create_all` and kept no version.
    drop_schema()
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        Base.metadata.create_all(connection)

    migrate()

    assert current_revision() == FIRST_REVISION
    assert table_names() == set(Base.metadata.tables) | {"alembic_version"}


def test_database_missing_tables_is_refused(rebuilt_afterwards):
    drop_schema()
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        Base.metadata.create_all(connection)
        connection.execute(text("DROP TABLE chat_messages, chats"))

    with pytest.raises(RuntimeError, match="lacks the tables chat_messages, chats") as error:
        migrate()
    assert "docker compose down -v" in str(error.value)
    assert current_revision() is None
