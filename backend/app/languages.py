"""The language an organisation's documents are searched in, and answered in when a question
does not show one.

Keyword search stems a passage's words by a text search configuration of the database
(`passages.language`, from which its `tsv` is generated), and parses a query by the same
configuration for each language a project holds (app/search.py). An organisation's owners and
admins choose its language (`organizations.language`, on the organisation page); until they
do, it is the deployment's FTS_LANGUAGE. A choice is one of the configurations the database
has (`known_languages`), as FTS_LANGUAGE is checked to be at start.

New passages take the language as the structure stage writes them (`project_language`). Those
indexed before are moved to it by a job queued with the change (`queue_language`,
`apply_language`): a batch at a time in the order of their ids, each committed under the job's
lease and under an advisory lock of the organisation's in which it reads the language again,
so that a second change while the job runs is what its later batches write, and two jobs of one
organisation take turns rather than undo each other's batches. Changing the column generates
`tsv` again and asks no model and counts no page. A passage that a stage is writing as the
language changes may keep the language the stage read; indexing its document again moves it.

The agent is told the language an organisation chose (`answer_language`, a line of its
prompt): a member is answered in the language they ask in, and in the organisation's when a
question does not show one.
"""

import logging

from sqlalchemy import Connection, exists, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import MIGRATION_LOCK, SessionLocal
from app.jobs import APPLY_LANGUAGE, BULK, Lease, LeaseLost, enqueue, renew
from app.models import Job, Organization

log = logging.getLogger(__name__)

# The configuration that stems nothing, so that a word matches as it is written, in any
# language: no language to answer in.
WORDS_AS_WRITTEN = "simple"
# How many passages a batch moves: each one's `tsv` is generated again and its vector indexed
# again, so a batch holds its rows for a moment, not for the whole organisation.
BATCH = 500
# The advisory lock an organisation's batches take turns under, its id the second key.
LANGUAGE_LOCK = MIGRATION_LOCK + 3

_KNOWN = text("SELECT cfgname FROM pg_ts_config ORDER BY 1")
_OF_PROJECT = text(
    "SELECT o.language FROM projects AS p JOIN organizations AS o ON o.id = p.org_id "
    "WHERE p.id = :project"
)
# The next batch of the organisation's passages in another language, past the last batch's ids,
# those in the trash among them, which a restore brings back. A passage a stage holds is passed
# over rather than waited for: the stage is rewriting it.
_MOVE = text(
    """
    UPDATE passages SET language = CAST(:language AS regconfig)
    WHERE id IN (
        SELECT passages.id FROM passages JOIN projects ON projects.id = passages.project_id
        WHERE projects.org_id = :org AND passages.id > :after
          AND passages.language <> CAST(:language AS regconfig)
        ORDER BY passages.id
        LIMIT :batch
        FOR UPDATE OF passages SKIP LOCKED)
    RETURNING id
    """
)


def known_languages(connection: Connection | Session) -> list[str]:
    """The text search configurations the database has, by name, which a passage may be
    indexed with."""
    return list(connection.scalars(_KNOWN))


def language_of(organization: Organization) -> str:
    """The language the organisation's documents are searched in: its own, or the
    deployment's."""
    return organization.language or get_settings().fts_language


def project_language(session: Session, project_id: int) -> str:
    """The language a project's new passages are indexed with: its organisation's."""
    return session.scalar(_OF_PROJECT, {"project": project_id}) or get_settings().fts_language


def language_name(name: str) -> str:
    """A configuration's name as a sentence says it: "german" is German."""
    return name.replace("_", " ").capitalize()


def answer_language(organization: Organization) -> str:
    """The line of the agent's prompt that says which language the organisation chose, or
    nothing while it chose none or one that stems nothing."""
    chosen = organization.language
    if not chosen or chosen == WORDS_AS_WRITTEN:
        return ""
    name = language_name(chosen)
    return (
        f"\nThe organisation works in {name}: answer in the language of the question, and in "
        f"{name} when the question does not show one."
    )


def queue_language(session: Session, org_id: int) -> None:
    """Queue the move of the organisation's passages to its language, in the caller's session,
    unless one is queued already, which will read the language as it runs. One running is not
    enough: it may have read its last batch before the change."""
    waiting = session.scalar(
        select(
            exists().where(Job.kind == APPLY_LANGUAGE, Job.org_id == org_id, Job.status == "queued")
        )
    )
    if not waiting:
        enqueue(session, APPLY_LANGUAGE, org_id=org_id, priority=BULK)


def apply_language(org_id: int, lease: Lease) -> int:
    """Move the organisation's passages to its language, a batch a transaction, each committed
    with the lease renewed; returns how many moved. Run again after a lost lease, it finds those
    moved already in the language and goes on with the rest. Raises `LeaseLost` when the job is
    no longer this run's."""
    moved = after = 0
    while True:
        with SessionLocal() as session:
            session.execute(
                text("SELECT pg_advisory_xact_lock(:key, :org)"),
                {"key": LANGUAGE_LOCK, "org": org_id},
            )
            row = session.execute(
                select(Organization.language).where(Organization.id == org_id)
            ).first()
            if row is None:
                # Deleted since: its passages went with it.
                return moved
            language = row.language or get_settings().fts_language
            ids = session.scalars(
                _MOVE, {"language": language, "org": org_id, "after": after, "batch": BATCH}
            ).all()
            if not ids:
                break
            if not renew(session, lease):
                session.rollback()
                raise LeaseLost(lease)
            session.commit()
        moved += len(ids)
        after = max(ids)
    log.info("%s passages of organisation %s are searched in %s now", moved, org_id, language)
    return moved
