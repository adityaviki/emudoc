"""The database engine, the base every table derives from, and the schema check at start."""

from collections.abc import Iterator

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import MetaData, create_engine
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.dialects.postgresql.base import ischema_names
from sqlalchemy.engine import Engine
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


def make_engine(settings: Settings) -> Engine:
    """The engine, with its pool sized by the settings (DB_POOL_SIZE and the two beside it).

    `pool_pre_ping` checks a connection as the pool hands it out, so one the database closed
    while it sat idle is replaced instead of failing the request that got it. `hide_parameters`
    keeps a statement's values out of the errors it raises, and so out of the log: they are a
    passage's text, a question, a fact (app/logs.py).
    """
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        hide_parameters=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
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
