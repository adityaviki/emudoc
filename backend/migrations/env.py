"""Alembic's entry point: where the migrations run and what they compare the models against.

The database comes from the app's settings, so the command line and the app see the same one.
When the app applies the migrations itself (`app.db.migrate`), it hands its connection over
through `config.attributes["connection"]` and the migrations run inside its transaction.
"""

from logging.config import fileConfig

from alembic import context
from pgvector.sqlalchemy import Vector
from sqlalchemy import Connection

from app import models  # noqa: F401  (registers every table on Base.metadata)
from app.config import get_settings
from app.db import Base, engine

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
    connection: Connection | None = config.attributes.get("connection")
    if connection is not None:
        run_on(connection)
        return
    # From the command line: log what runs, on a connection of its own.
    if config.config_file_name:
        fileConfig(config.config_file_name)
    with engine.connect() as own_connection:
        run_on(own_connection)


def run_on(connection: Connection) -> None:
    context.configure(
        connection=connection, target_metadata=target_metadata, render_item=render_item
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
