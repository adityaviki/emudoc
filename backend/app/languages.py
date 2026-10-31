"""The language a document is searched in, its own or its organisation's, and the one answers
are given in when a question does not show one.

Keyword search stems a passage's words by a text search configuration of the database
(`passages.language`, from which its `tsv` is generated), and parses a query by the same
configuration for each language a project holds (app/search.py). An organisation's owners and
admins choose its language (`organizations.language`, on the organisation page); until they
do, it is the deployment's FTS_LANGUAGE. A choice is one of the configurations the database
has (`known_languages`), as FTS_LANGUAGE is checked to be at start.

A document has a language of its own when the parse stage could tell which its words are in
(`detect_language`, py3langid's model, which no request reaches and which knows 97 languages,
those the database cannot stem being searched by their words as written), or when a member set
one; it is one of its details (app/details.py), `documents.language`, null for its
organisation's. The detection's ISO code is kept beside what it read (`read_language_code`),
since `simple` names no language and the enrich stage reads dates and numbers the way the
language writes them (app/locales.py). New passages take the document's language, else the
organisation's, as the structure stage writes them (`project_language`). Those indexed before
are moved by a job queued with a change of either (`queue_language`, `apply_language`): a batch
at a time in the order of their ids, each committed under the job's lease and under an advisory
lock of the organisation's in which it reads the languages again, so that a second change while
the job runs is what its later batches write, and two jobs of one organisation take turns rather
than undo each other's batches. Changing the column generates `tsv` again, and the passage's
vector is indexed again with it, so a document's passages are never moved in a member's request;
it asks no model and counts no page. A passage that a stage is writing as the organisation's
language changes may keep the language the stage read; indexing its document again moves it.

The agent is told the language an organisation chose (`answer_language`, a line of its
prompt): a member is answered in the language they ask in, and in the organisation's when a
question does not show one.
"""

import logging
import re
import threading
from collections.abc import Collection, Iterable
from typing import TYPE_CHECKING, NamedTuple

from sqlalchemy import Connection, exists, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import MIGRATION_LOCK, SessionLocal
from app.jobs import APPLY_LANGUAGE, BULK, Lease, LeaseLost, enqueue, renew
from app.models import Job, Organization

if TYPE_CHECKING:
    from py3langid.langid import LanguageIdentifier

log = logging.getLogger(__name__)

# The configuration that stems nothing, so that a word matches as it is written, in any
# language: no language to answer in.
WORDS_AS_WRITTEN = "simple"
# A language chosen for an organisation or a document that is none of `known_languages`.
UNKNOWN_LANGUAGE = "Emudoc cannot search documents in that language. Choose one of those offered."
# How many passages a batch moves: each one's `tsv` is generated again and its vector indexed
# again, so a batch holds its rows for a moment, not for the whole organisation.
BATCH = 500
# The advisory lock an organisation's batches take turns under, its id the second key.
LANGUAGE_LOCK = MIGRATION_LOCK + 3

# The text search configuration of each language the detection names (ISO 639-1) that the
# database can stem; any other is searched by its words as written.
CONFIGURATIONS = {
    "ar": "arabic",
    "hy": "armenian",
    "eu": "basque",
    "ca": "catalan",
    "da": "danish",
    "nl": "dutch",
    "en": "english",
    "fi": "finnish",
    "fr": "french",
    "de": "german",
    "el": "greek",
    "hi": "hindi",
    "hu": "hungarian",
    "id": "indonesian",
    "ga": "irish",
    "it": "italian",
    "lt": "lithuanian",
    "ne": "nepali",
    "no": "norwegian",
    "nn": "norwegian",
    "pt": "portuguese",
    "ro": "romanian",
    "ru": "russian",
    "sr": "serbian",
    "es": "spanish",
    "sv": "swedish",
    "ta": "tamil",
    "tr": "turkish",
}
# What the detection says of a text without words of any language.
NO_LANGUAGE = "zxx"
# How sure the detection must be before a document is searched in the language it found; below
# it the document follows its organisation's. Its sureness grows with the words it reads: a
# delivery log of dates, numbers and names is under 0.1, an invoice's words about 0.9, a page
# of prose 0.99 or more, and ten words of Spanish 0.37, Galician and Portuguese close behind.
SURE = 0.5
# How many characters of a document's words are read to tell its language: its first pages.
DETECT_CHARS = 20_000
# A word: letters, with an apostrophe or a hyphen inside. Digits, amounts and punctuation say
# nothing of a language and would make every invoice less sure.
_WORD = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*")

_KNOWN = text("SELECT cfgname FROM pg_ts_config ORDER BY 1")
_OF_PROJECT = text(
    "SELECT o.language FROM projects AS p JOIN organizations AS o ON o.id = p.org_id "
    "WHERE p.id = :project"
)
# The next batch of the organisation's passages in another language than their document's, or
# the organisation's for a document without one, past the last batch's ids, those in the trash
# among them, which a restore brings back. A passage a stage holds is passed over rather than
# waited for: the stage is rewriting it.
_MOVE = text(
    """
    UPDATE passages SET language = moved.language
    FROM (
        SELECT passages.id, CAST(COALESCE(documents.language, :language) AS regconfig) AS language
        FROM passages
        JOIN documents ON documents.id = passages.document_id
        JOIN projects ON projects.id = passages.project_id
        WHERE projects.org_id = :org AND passages.id > :after
          AND passages.language <> CAST(COALESCE(documents.language, :language) AS regconfig)
        ORDER BY passages.id
        LIMIT :batch
        FOR UPDATE OF passages SKIP LOCKED) AS moved
    WHERE passages.id = moved.id
    RETURNING passages.id
    """
)
# The identifier, loaded by the first detection of the process, which only the worker makes:
# its model holds about 75 MB, and takes a third of a second to load.
_IDENTIFIER: "LanguageIdentifier | None" = None
_LOADING = threading.Lock()


def known_languages(connection: Connection | Session) -> list[str]:
    """The text search configurations the database has, by name, which a passage may be
    indexed with."""
    return list(connection.scalars(_KNOWN))


def language_of(organization: Organization) -> str:
    """The language the organisation's documents are searched in: its own, or the
    deployment's."""
    return organization.language or get_settings().fts_language


def project_language(session: Session, project_id: int) -> str:
    """The language a project's documents without one of their own are indexed with: its
    organisation's."""
    return chosen_language(session, project_id) or get_settings().fts_language


def chosen_language(session: Session, project_id: int) -> str | None:
    """The language a project's organisation chose, None while it chose none."""
    return session.scalar(_OF_PROJECT, {"project": project_id})


class Detected(NamedTuple):
    """The language a document's words are written in: the configuration they are searched in,
    and the language's ISO 639 code, which says how its dates and numbers are written
    (app/locales.py) where the configuration is WORDS_AS_WRITTEN and names none."""

    language: str
    code: str


def detect_language(texts: Iterable[str], known: Collection[str]) -> Detected | None:
    """The language the texts' words are written in, the first DETECT_CHARS of them, as the
    configuration of `known` that stems it, WORDS_AS_WRITTEN for one the database cannot stem,
    beside its code; None when the words are too few or too mixed to tell (`SURE`)."""
    words = _words(texts)
    if not words:
        return None
    found, sureness = _identifier().classify(words)
    if found == NO_LANGUAGE or sureness < SURE:
        return None
    language = CONFIGURATIONS.get(found, WORDS_AS_WRITTEN)
    return Detected(language if language in known else WORDS_AS_WRITTEN, found)


def _words(texts: Iterable[str]) -> str:
    """The texts' first words, up to DETECT_CHARS characters, without the rest of them."""
    held: list[str] = []
    length = 0
    for chunk in texts:
        for word in _WORD.finditer(chunk):
            held.append(word[0])
            length += len(word[0]) + 1
            if length >= DETECT_CHARS:
                return " ".join(held)
    return " ".join(held)


def _identifier() -> "LanguageIdentifier":
    global _IDENTIFIER
    with _LOADING:
        if _IDENTIFIER is None:
            # Imported here, so that a process that never detects, such as the API's in
            # production, holds neither numpy nor the model.
            from py3langid.langid import MODEL_FILE, LanguageIdentifier

            _IDENTIFIER = LanguageIdentifier.from_model_file(MODEL_FILE, norm_probs=True)
        return _IDENTIFIER


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
    """Queue the move of the organisation's passages to their documents' languages or its own, in
    the caller's session, unless one is queued already, which will read the languages as it
    runs. One running is not enough: it may have read its last batch before the change."""
    waiting = session.scalar(
        select(
            exists().where(Job.kind == APPLY_LANGUAGE, Job.org_id == org_id, Job.status == "queued")
        )
    )
    if not waiting:
        enqueue(session, APPLY_LANGUAGE, org_id=org_id, priority=BULK)


def apply_language(org_id: int, lease: Lease) -> int:
    """Move the organisation's passages to their documents' languages, or its own for a
    document without one, a batch a transaction, each committed with the lease renewed; returns
    how many moved. Run again after a lost lease, it finds those
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
    log.info(
        "%s passages of organisation %s are searched in their documents' languages now, or in %s",
        moved,
        org_id,
        language,
    )
    return moved
