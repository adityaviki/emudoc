"""Alembic's entry point: where the migrations run and what they compare the models against.

The database comes from the app's settings, so the command line and the app see the same one.
The app does not run migrations itself; `alembic upgrade head` is a step before it starts, and
the app only checks the result (`check_schema` in app/db.py).
"""

import logging
from logging.config import fileConfig

from alembic import context
from pgvector.sqlalchemy import Vector
from sqlalchemy import Connection, text

from app import models  # noqa: F401  (registers every table on Base.metadata)
from app.config import get_settings
from app.db import MIGRATION_LOCK, Base, engine

config = context.config
target_metadata = Base.metadata


def render_item(kind: str, obj: object, autogen_context) -> str | bool:
    """Write pgvector's column type with its import; autogenerate does not know the module."""
    if kind == "type" and isinstance(obj, Vector):
        autogen_context.imports.add("from pgvector.sqlalchemy import Vector")
        return f"Vector({obj.dim})"
    return False


def run_migrations_offline() -> None:
    """Print the SQL instead of running it: `alembic upgrade head --sql`."""
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_item=render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # The command line logs which migrations run. A process that has set up logging already,
    # such as the test suite, keeps its own.
    if config.config_file_name and not logging.getLogger().hasHandlers():
        fileConfig(config.config_file_name, disable_existing_loggers=False)
    with engine.connect() as connection:
        run_on(connection)


def run_on(connection: Connection) -> None:
    context.configure(
        connection=connection, target_metadata=target_metadata, render_item=render_item
    )
    with context.begin_transaction():
        # Two processes upgrading the same database at once (replicas of a new release) wait
        # for each other here, and the second finds nothing left to do. The lock lasts for the
        # transaction, which is the whole run, and Alembic itself takes none.
        connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": MIGRATION_LOCK})
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
