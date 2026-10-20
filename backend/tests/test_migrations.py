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
from pydantic_ai.messages import TextPart
from sqlalchemy import func, inspect, select, text, update

from app.config import get_settings
from app.db import MIGRATION_LOCK, UPGRADE_COMMAND, Base, alembic_config, check_schema, engine
from app.encrypted import TOTP_SECRETS, encrypt
from app.indexing.pipeline import process_document
from app.main import create_app
from app.models import NATURAL_ORDER, ChatMessage, Document, Project, User
from tests.conftest import STORAGE_ROOT, FakeTurn, build_schema, drop_schema
from tests.test_api import create_project
from tests.test_chats import answered, ask, new_chat

INITIAL_SCHEMA = "bedb624406a6"
PASSAGE_LANGUAGE = "27bd56b4a29e"
STORAGE_KEYS = "5c1e7a9d2b40"
USAGE_EVENTS = "4d17fad8beb9"
USAGE_ON_ROWS = "29c97f1a6f4f"
RATE_LIMITS = "133a863e3662"
BEFORE_ENCRYPTED_SECRETS = "2656abd623ec"
BEFORE_QUESTIONS_COUNTED = "0151920be687"


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


def test_file_names_are_in_the_collation_the_model_names():
    # Alembic compares no collations, so the comparison above would not see these two differ.
    with engine.connect() as connection:
        collation = connection.scalar(
            text(
                "SELECT collation_name FROM information_schema.columns "
                "WHERE table_name = 'documents' AND column_name = 'filename'"
            )
        )
    assert collation == Document.__table__.c.filename.type.collation == NATURAL_ORDER


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


def test_usage_recorded_before_the_columns_is_summed_into_them(
    rebuilt_afterwards, client, session, services, indexed
):
    # Before the columns, what a document or an answer used was only in the events. Going
    # back drops the columns; coming forward fills them from the events again, so the
    # document and the answer show what they showed.
    services.llm.agent_turns = [FakeTurn(TextPart("90 days."))]
    chat = new_chat(client, indexed.project_id)
    answered(client, services, chat["id"], "Notice period?")
    document_usage = client.get(f"/api/documents/{indexed.id}").json()["usage"]
    answer_usage = client.get(f"/api/chats/{chat['id']}").json()["messages"][-1]["usage"]
    assert set(document_usage) == {"upload", "enrich", "embed"} and set(answer_usage) == {"answer"}

    session.rollback()
    command.downgrade(alembic_config(), USAGE_EVENTS)
    with engine.connect() as connection:
        columns = {column["name"] for column in inspect(connection).get_columns("documents")}
    assert "usage" not in columns

    command.upgrade(alembic_config(), "head")
    assert client.get(f"/api/documents/{indexed.id}").json()["usage"] == document_usage
    reply = client.get(f"/api/chats/{chat['id']}").json()["messages"][-1]
    assert reply["usage"] == answer_usage


def test_organisations_from_before_plans_start_on_the_free_one_but_the_local_has_no_limits(
    rebuilt_afterwards, session, account, make_account
):
    other = make_account("ann@globex.example").organization.id
    local = account.organization.id
    session.rollback()
    command.downgrade(alembic_config(), USAGE_ON_ROWS)
    with engine.connect() as connection:
        columns = {column["name"] for column in inspect(connection).get_columns("organizations")}
    assert "plan" not in columns

    command.upgrade(alembic_config(), "head")
    with engine.connect() as connection:
        plans = dict(connection.execute(text("SELECT id, plan FROM organizations")).all())
    assert plans == {local: "enterprise", other: "free"}


def test_documents_from_before_scanning_are_served_as_they_were(
    rebuilt_afterwards, client, session, indexed
):
    # A document indexed before the scan stage existed was never scanned. Coming forward marks
    # its stage skipped, which is what a document says when there was no scanner, so that it
    # is served as it was; "Index again" scans it like any other.
    document_id = indexed.id
    session.rollback()
    command.downgrade(alembic_config(), RATE_LIMITS)
    with engine.connect() as connection:
        stages = connection.scalar(
            text("SELECT stages FROM documents WHERE id = :id"), {"id": document_id}
        )
    assert "scan" not in stages

    command.upgrade(alembic_config(), "head")
    shown = client.get(f"/api/documents/{document_id}").json()
    assert shown["status"] == "ready"
    assert shown["stages"]["scan"] == {
        "status": "skipped",
        "detail": "Uploaded before uploads were scanned.",
    }
    assert client.get(f"/api/documents/{document_id}/file").status_code == 200


def test_a_downgrade_past_encrypted_secrets_turns_their_two_factor_off(
    rebuilt_afterwards, session, account, make_account
):
    # The revision before cannot read an encrypted secret, and a migration has no key.
    encrypted, plain = account.user.id, make_account("ann@globex.example").user.id
    secret = "JBSWY3DPEHPK3PXP"
    for user_id, value in ((encrypted, encrypt(TOTP_SECRETS, encrypted, secret)), (plain, secret)):
        session.execute(
            update(User)
            .where(User.id == user_id)
            .values(totp_secret=value, totp_enabled_at=func.now())
        )
    session.commit()
    command.downgrade(alembic_config(), BEFORE_ENCRYPTED_SECRETS)
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT id, totp_secret, totp_enabled_at IS NOT NULL FROM users "
                "WHERE id = ANY(:ids)"
            ),
            {"ids": [encrypted, plain]},
        ).all()
    assert sorted(rows) == sorted([(encrypted, None, False), (plain, secret, True)])


def test_the_questions_of_the_chats_kept_are_counted_from_when_they_were_asked(
    rebuilt_afterwards, client, session, account
):
    # A question was counted by its chat's answers before; coming forward writes the event it
    # would have written as it was asked, so that the month counts what it counted.
    project = create_project(client)
    ask(client, new_chat(client, project["id"])["id"], "Notice period?")
    asked_at = session.scalar(select(ChatMessage.created_at).where(ChatMessage.role == "assistant"))
    session.rollback()
    command.downgrade(alembic_config(), BEFORE_QUESTIONS_COUNTED)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM usage_events")) == 0

    command.upgrade(alembic_config(), "head")
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT org_id, project_id, user_id, kind, unit, quantity, ref_type, occurred_at "
                "FROM usage_events"
            )
        ).all()
    assert rows == [
        (
            account.organization.id,
            project["id"],
            account.user.id,
            "answer",
            "questions",
            1,
            None,
            asked_at,
        )
    ]


def test_database_made_before_migrations_cannot_be_upgraded(rebuilt_afterwards):
    # Builds before migrations made the tables with `create_all` and kept no version. The
    # first migration would fail on its first table; it says what to do instead.
    drop_schema()
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        connection.execute(
            text(
                f"CREATE COLLATION {NATURAL_ORDER} (provider = icu, locale = 'und-u-kn-ks-level1')"
            )
        )
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
