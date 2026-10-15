"""The database engine, the base every table derives from, and the schema check at start."""

from collections.abc import Iterator

import psycopg
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from psycopg.errors import QueryCanceled
from sqlalchemy import Connection, MetaData, create_engine, text
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.dialects.postgresql.base import ischema_names
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import BACKEND_DIR, Settings, get_settings

# SQLAlchemy can write a `regconfig` column (`passages.language`) but does not recognise one it
# reads back from the catalog, so the migration check would see an untyped column and warn.
# Registering the name is what pgvector does for `vector`.
ischema_names.setdefault("regconfig", REGCONFIG)

# What a request is refused with when every connection in the pool is in use for longer than
# DB_POOL_TIMEOUT (`pool_exhausted` in app/main.py sends it with 503; an answer that is being
# streamed ends with it).
BUSY = "The app is handling as many requests as it can right now. Wait a moment and try again."
# What a request is refused with when the database stopped one of its statements, at the role's
# statement_timeout (`statement_canceled` in app/main.py).
TOO_SLOW = (
    "The database took longer over this request than it allows, so the request was stopped. "
    "Try again in a moment."
)
# The timeouts a deployment gives its database role (deploying.md, "The database"), lifted by
# `unbounded`; transaction_timeout is Postgres 17's, and a server without it is not asked.
_UNBOUNDED = text(
    "SELECT set_config('statement_timeout', '0', true), "
    "set_config('idle_in_transaction_session_timeout', '0', true), "
    "CASE WHEN current_setting('transaction_timeout', true) IS NOT NULL "
    "THEN set_config('transaction_timeout', '0', true) END"
)


def make_engine(settings: Settings) -> Engine:
    """The engine, with its pool sized by the settings (DB_POOL_SIZE and the two beside it).

    `pool_pre_ping` checks a connection as the pool hands it out, so one the database closed
    while it sat idle is replaced instead of failing the request that got it. `hide_parameters`
    keeps a statement's values out of the errors it raises, and so out of the log: they are a
    passage's text, a question, a fact (app/logs.py).

    Behind a pooler in transaction mode (DATABASE_POOLER) each transaction may run on another
    server connection, and psycopg, which prepares a statement after its fifth run on a
    connection, would find it missing there; so nothing is prepared.
    """
    pooled = settings.database_pooler == "transaction"
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        hide_parameters=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        connect_args={"prepare_threshold": None} if pooled else {},
    )


engine = make_engine(get_settings())
SessionLocal = sessionmaker(engine, expire_on_commit=False)

# Constraints are named the way Postgres names them when left unnamed, so a migration can name
# the constraint it drops, and a database made before there were migrations needs no renaming.
# Check constraints are named by hand in models.py.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "%(table_name)s_%(column_0_N_name)s_key",
    "fk": "%(table_name)s_%(column_0_N_name)s_fkey",
    "pk": "%(table_name)s_pkey",
}

MIGRATIONS_DIR = BACKEND_DIR / "migrations"
# The advisory lock a migration run holds (migrations/env.py), so that two processes upgrading
# the same database take turns instead of both altering tables. Any fixed number works as long
# as nothing else in the database locks on it.
MIGRATION_LOCK = 20261011
UPGRADE_COMMAND = "uv run alembic upgrade head"


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


def unbounded(connection: Connection | Session) -> None:
    """Lift the database role's timeouts for the rest of the caller's transaction.

    A deployment gives the role a statement_timeout and an idle_in_transaction_session_timeout
    (deploying.md, "The database"), so that a query that runs away on a member's request, or a
    transaction a process left open, does not hold its connection, its locks and vacuum's
    horizon until it ends by itself. Work whose length is the data's rather than a request's
    lifts them for itself: the migrations, resizing the vectors, the housekeeping round (whose
    walk of the storage keeps one transaction open between statements), a restore's repair, an
    account's purge and a project's deletion. The settings are the transaction's own
    (`is_local`), so they end with it, and a pooler in transaction mode carries them nowhere.
    """
    connection.execute(_UNBOUNDED)


def connection_lost(error: BaseException) -> bool:
    """Whether the database went away under the work: its connection dropped (a failover, a
    restart, a pooler that let go, a timeout that ended the session) or none could be made.
    Unlike an error in what was asked, it passes by itself. SQLAlchemy flags the first
    (`connection_invalidated`); the second is psycopg's error without a SQLSTATE, since the
    server said nothing."""
    if not isinstance(error, DBAPIError):
        return False
    refused = isinstance(error.orig, psycopg.OperationalError) and error.orig.sqlstate is None
    return error.connection_invalidated or refused


def canceled(error: DBAPIError) -> bool:
    """Whether the database stopped the statement: its statement_timeout, or an operator's
    pg_cancel_backend."""
    return isinstance(error.orig, QueryCanceled)


def alembic_config() -> Config:
    """Alembic set up for this backend, wherever the process was started from."""
    config = Config(BACKEND_DIR / "alembic.ini")
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return config


def check_schema() -> None:
    """Refuse to serve a database whose schema is not the one this code was written for.

    The migrations are applied by `alembic upgrade head`, run before the app starts (`dev.sh`
    does it; a deployment does it as a release step), not by the app itself: several replicas
    starting at once would race one another altering tables, and a migration that fails should
    stop a release rather than take the app down with it. So at start the app only compares the
    database's revision with the newest migration and says what to run when they differ.
    """
    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    with engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
    if current == head:
        return
    state = (
        "has no schema yet"
        if current is None
        else f"is at schema revision {current}, and this version of Emudoc needs {head}"
    )
    raise RuntimeError(
        f"The database {state}. Run `{UPGRADE_COMMAND}` in the backend directory, then start "
        "the app again."
    )
