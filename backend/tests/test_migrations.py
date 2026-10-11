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

from app.config import get_settings
from app.db import MIGRATION_LOCK, UPGRADE_COMMAND, Base, alembic_config, check_schema, engine
from app.indexing.pipeline import process_document
from app.main import create_app
from app.models import Project
from tests.conftest import STORAGE_ROOT, build_schema, drop_schema

INITIAL_SCHEMA = "bedb624406a6"
PASSAGE_LANGUAGE = "27bd56b4a29e"
STORAGE_KEYS = "5c1e7a9d2b40"


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
        with TestClient(create_app(services=services)):
            pass

    # A database ahead of the code, after a rollback of a release, is named as such.
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO alembic_version VALUES ('0000')"))
    with pytest.raises(RuntimeError, match="is at schema revision 0000, and this version"):
        check_schema()


def test_passages_keep_the_language_their_text_index_was_built_with(
    rebuilt_afterwards, indexed, session, monkeypatch
):
    # Before the language column, `tsv` was generated with FTS_LANGUAGE baked into the schema.
    # Going back to that revision and forward again, the rows get the configured language,
    # which is the one their index was built with; new rows default to English.
    monkeypatch.setattr(get_settings(), "fts_language", "simple")
    # The migrations on the way back alter tables this session has read, which needs its open
    # transaction to end first.
    session.rollback()
    command.downgrade(alembic_config(), INITIAL_SCHEMA)
    with engine.connect() as connection:
        columns = {column["name"] for column in inspect(connection).get_columns("passages")}
    assert "language" not in columns

    command.upgrade(alembic_config(), "head")
    with engine.connect() as connection:
        languages = connection.execute(text("SELECT DISTINCT language FROM passages")).scalars()
        assert set(languages) == {"simple"}
        column = next(
            column
            for column in inspect(connection).get_columns("passages")
            if column["name"] == "language"
        )
        assert column["default"] == "'english'::regconfig"
        assert connection.scalar(
            text("SELECT count(*) FROM passages WHERE tsv @@ plainto_tsquery('simple', 'notice')")
        )


def test_documents_get_keys_from_their_old_paths(rebuilt_afterwards, indexed, session):
    # Before storage keys, `storage_path` was the absolute path of the original under the data
    # directory. Going back gives the row that path, and coming forward derives the key from
    # the organisation, the ids and the extension.
    key, project_id, document_id = indexed.storage_key, indexed.project_id, indexed.id
    assert key == f"orgs/1/projects/{project_id}/documents/{document_id}/original.pdf"
    # Renaming the column needs an exclusive lock, so this session's open transaction ends first.
    session.rollback()
    command.downgrade(alembic_config(), PASSAGE_LANGUAGE)
    with engine.connect() as connection:
        columns = {column["name"] for column in inspect(connection).get_columns("documents")}
        assert "storage_key" not in columns and "storage_path" in columns
        path = connection.scalar(text("SELECT storage_path FROM documents"))
    assert path == str(STORAGE_ROOT / "files" / str(project_id) / str(document_id) / "original.pdf")

    command.upgrade(alembic_config(), "head")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT storage_key FROM documents")) == key


def test_waiting_documents_get_jobs_when_the_queue_arrives(
    rebuilt_afterwards, session, services, make_document, sample_pdf, sample_docx, sample_csv
):
    # Before the queue, a worker claimed documents by their status. Coming forward, every
    # document that was waiting gets its job, the one a worker of the old version held is
    # queued once more, and a finished one gets nothing.
    queued = make_document(sample_pdf)
    project = session.get(Project, queued.project_id)
    held = make_document(sample_docx, project=project)
    held.status = "processing"
    finished = make_document(sample_csv, project=project)
    session.commit()
    process_document(finished.id, services)

    command.downgrade(alembic_config(), STORAGE_KEYS)
    assert "jobs" not in table_names()
    command.upgrade(alembic_config(), "head")
    with engine.connect() as connection:
        jobs = connection.execute(
            text(
                "SELECT (payload->>'document_id')::int, kind, status, org_id, project_id "
                "FROM jobs ORDER BY id"
            )
        ).all()
        statuses = connection.scalars(text("SELECT status FROM documents ORDER BY id")).all()
    assert jobs == [
        (queued.id, "index_document", "queued", 1, project.id),
        (held.id, "index_document", "queued", 1, project.id),
    ]
    assert statuses == ["queued", "queued", "ready"]


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
