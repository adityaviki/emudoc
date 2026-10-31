"""The sample project the organisation an account signs up with starts with (SAMPLE_PROJECT):
Emudoc's own documents of one supplier, the canonical scenario of checking an invoice against
the contract, the price list and the delivery log, with three questions to ask about them.

Nothing is indexed at sign-up. The files in `files/` were indexed once by the pipeline, as an
upload is (`build_sample`, the `build-sample` maintenance command, which a developer runs with a
model), and `index.json` holds what it made of them: each document's blocks, outline, passages,
summaries and facts. A new organisation gets a copy (`add_sample`): the files put into the
storage under the new documents' keys and the rows written with new ids, in the sign-up's
transaction, so that the first answer with a citation waits for no upload and no model. Only
the vectors are left out, since they are the deployment's embedding model's: each document's
index job runs the embed stage alone, a few seconds, and a search finds the passages by their
words meanwhile.

The sample is outside the plan: its project and documents count against nothing the plan holds
(app/entitlements.py), and nothing more is uploaded into it (app/api/documents.py), so that it
never becomes a project the plan did not count. Its questions are asked like any other, and
count as any do; a member deletes it like any project.

`index.json` is a snapshot: a later change to the parsers or the pipeline makes it older, not
wrong, since it describes files that do not change. Run `build-sample` again when such a change
should show in the sample, or when the files change.
"""

import hashlib
import json
import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from functools import cache
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.audit import audit_event
from app.config import Settings, get_settings
from app.db import SessionLocal
from app.embeddings import Embedder
from app.indexing.pipeline import Services, process_document
from app.jobs import LIVE, queue_index_by_id
from app.llm import LLM
from app.models import (
    Block,
    Document,
    Fact,
    Organization,
    Passage,
    Project,
    Section,
    UsageEvent,
)
from app.parsing import MIME_TYPES
from app.restore import place_taken
from app.storage import LocalStorage, Storage, StorageError, original_key

log = logging.getLogger(__name__)

FILES = Path(__file__).parent / "files"
INDEX = Path(__file__).parent / "index.json"
BUILD_COMMAND = "python -m app.maintenance build-sample"

NAME = "Sample: Acme supplier contract"
DESCRIPTION = (
    "Emudoc's sample documents: a supplier's contract, three of its invoices, the price list, "
    "the delivery log, an email and the notes of a review meeting. Ask about them; your own "
    "documents go in a project of your own."
)
# Each asks the agent to read several documents against each other, as the product is for:
# invoice INV-2026-0503 bills 340 pallets with none of the contract's 6 % discount, two
# deliveries were late, and the prices are the list's.
QUESTIONS = (
    "Does invoice INV-2026-0503 apply the volume discount the contract sets?",
    "Which deliveries were late, why, and does the contract give Northwind a credit for them?",
    "Do the invoices charge the prices on the price list?",
)
# Why nothing is uploaded into the sample project (app/api/documents.py).
SAMPLE_UPLOADS = (
    "This is the sample project, which holds Emudoc's sample documents alone. Create a project "
    "of your own to upload yours."
)
# The scan stage of a sample document, which no member uploaded.
NOTHING_TO_SCAN = "One of Emudoc's sample documents, not an upload, so there was nothing to scan."

# The stages whose work `index.json` holds; the scan has nothing to do, and the vectors are
# the deployment's.
COPIED_STAGES = ("parse", "structure", "enrich")
# The tables of a document in `index.json`, a row to a line.
TABLES = ("blocks", "sections", "passages", "facts")


@dataclass(frozen=True)
class SampleFile:
    """One document of the sample: its file as it is shipped, and what the pipeline made of it
    (an entry of `index.json`)."""

    name: str
    data: bytes
    index: dict[str, Any]


@cache
def sample_files() -> tuple[SampleFile, ...]:
    """The sample's files with their index, by name, read once. Refused with `RuntimeError`
    when `index.json` does not describe exactly the files beside it as they are."""
    try:
        documents = json.loads(INDEX.read_text())["documents"]
        names = sorted(path.name for path in FILES.iterdir())
    except (OSError, ValueError, KeyError) as error:
        raise RuntimeError(
            f"The sample project cannot be read from {INDEX.parent} ({error}). Rebuild the "
            f"image, run `{BUILD_COMMAND}` in a checkout, or set SAMPLE_PROJECT=false."
        ) from None
    by_name = {entry["filename"]: entry for entry in documents}
    if sorted(by_name) != names:
        raise RuntimeError(
            f"The sample's index.json describes {', '.join(sorted(by_name))}, and its files are "
            f"{', '.join(names)}. Run `{BUILD_COMMAND}` to index the files as they are."
        )
    files = []
    for name in names:
        data = (FILES / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != by_name[name]["sha256"]:
            raise RuntimeError(
                f"The sample's {name} is not the file its index.json describes. Run "
                f"`{BUILD_COMMAND}` to index it as it is, or put the file back."
            )
        files.append(SampleFile(name, data, by_name[name]))
    return tuple(files)


def check_sample(settings: Settings) -> None:
    """Refuse to start with SAMPLE_PROJECT on and a sample that cannot be copied, which would
    otherwise be found at the first sign-up."""
    if settings.sample_project:
        sample_files()


def add_sample(session: Session, organization: Organization, storage: Storage) -> Project | None:
    """Give the organisation the sample project, as the account it was made for signs up, in
    the caller's transaction, which commits it with each document's index job.

    None when SAMPLE_PROJECT is off, or when the storage would not take the files, which is
    logged: an account is never refused for its sample. A file stored before the failure is left
    for the orphan sweep, as an upload's is (app/maintenance.py).
    """
    if not get_settings().sample_project:
        return None
    try:
        with session.begin_nested():
            return _copy(session, organization, storage)
    except StorageError as error:
        log.warning("Organisation %s starts without the sample project: %s", organization.id, error)
        return None


def _copy(session: Session, organization: Organization, storage: Storage) -> Project:
    project, documents = copy_sample(session, organization, storage)
    # The vectors are the deployment's: each document's job runs its embed stage, the one not
    # done, before an upload waiting to be indexed, since its member is about to ask.
    queue_index_by_id(session.connection(), [document.id for document in documents], LIVE)
    audit_event(
        session,
        "sample_added",
        org_id=organization.id,
        project_id=project.id,
        target=("project", project.id),
        detail={"name": project.name, "documents": len(documents)},
    )
    return project


def copy_sample(
    session: Session, organization: Organization, storage: Storage
) -> tuple[Project, list[Document]]:
    """The sample project and its documents written into the organisation from the index, and
    their files put into the storage, each document ready but for its embed stage, which
    nothing is queued to run: a sign-up queues it (`add_sample`), and the golden questions run
    it themselves (app/golden.py)."""
    files = sample_files()
    project = Project(
        org_id=organization.id,
        name=NAME,
        description=DESCRIPTION,
        sample=True,
        suggestions=list(QUESTIONS),
    )
    session.add(project)
    session.flush()
    return project, [_copy_document(session, project, storage, file) for file in files]


def _copy_document(
    session: Session, project: Project, storage: Storage, file: SampleFile
) -> Document:
    entry = file.index
    suffix = Path(file.name).suffix.lower()
    document = Document(
        project_id=project.id,
        filename=file.name,
        mime_type=MIME_TYPES[suffix],
        size_bytes=len(file.data),
        sha256=entry["sha256"],
        storage_key="",
        # Searched by its words and asked about at once, while its job embeds it.
        status="ready",
        stages={
            "scan": {"status": "skipped", "detail": NOTHING_TO_SCAN},
            **{name: entry["stages"][name] for name in COPIED_STAGES},
            "embed": {"status": "pending", "detail": ""},
        },
        page_count=entry["page_count"],
        page_sizes=entry["page_sizes"],
        title=entry["title"],
        doc_type=entry["doc_type"],
        doc_date=_date(entry["doc_date"]),
        read_title=entry["title"],
        read_doc_type=entry["doc_type"],
        read_doc_date=_date(entry["doc_date"]),
        summary=entry["summary"],
        # Ready as it is made, so that the time an upload waits to be ready (`DOCUMENT_READY`,
        # app/metrics.py) is measured for uploads alone.
        ready_at=datetime.now(UTC),
    )
    session.add(document)
    session.flush()
    document.storage_key = original_key(project.org_id, project.id, document.id, suffix)
    # As for an upload (app/api/documents.py): a place that holds files is a lost document's.
    if storage.holds(document.storage_prefix):
        raise StorageError(place_taken(document.id, document.storage_prefix))
    storage.put(document.storage_key, file.data, document.mime_type)

    owned = {"document_id": document.id, "project_id": project.id}
    session.add_all(
        Block(document_id=document.id, ordinal=ordinal, **block)
        for ordinal, block in enumerate(entry["blocks"])
    )
    sections: list[Section] = []
    for ordinal, row in enumerate(entry["sections"]):
        parent = row["parent"]
        section = Section(
            **owned,
            ordinal=ordinal,
            parent_id=None if parent is None else sections[parent].id,
            **_without(row, "parent"),
        )
        session.add(section)
        session.flush()  # its subsections need its id
        sections.append(section)
    language = get_settings().fts_language
    passages = [
        Passage(
            **owned,
            ordinal=ordinal,
            section_id=sections[row["section"]].id,
            language=language,
            **_without(row, "section"),
        )
        for ordinal, row in enumerate(entry["passages"])
    ]
    session.add_all(passages)
    session.flush()  # the facts name their passages
    session.add_all(
        Fact(
            **owned,
            passage_id=None if row["passage"] is None else passages[row["passage"]].id,
            date=_date(row["date"]),
            **_without(row, "passage", "date"),
        )
        for row in entry["facts"]
    )
    return document


def _without(row: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key not in keys}


def _date(value: str | None) -> date | None:
    return None if value is None else date.fromisoformat(value)


def build_sample(settings: Settings, llm: LLM | None = None) -> dict[str, Any]:
    """What `index.json` holds: the sample's files indexed by `llm` or the models the settings
    name (`indexed_sample`). Refused with `RuntimeError` without a model, or when a stage did not
    do its work."""
    with indexed_sample(settings, llm, "Sample build") as (org_id, ids):
        with SessionLocal() as session:
            documents = [_indexed(session, session.get(Document, id_)) for id_ in ids]
            models = session.scalars(
                select(UsageEvent.model)
                .where(UsageEvent.org_id == org_id, UsageEvent.model.is_not(None))
                .distinct()
                .order_by(UsageEvent.model)
            ).all()
    return {
        "built": {"on": datetime.now(UTC).date().isoformat(), "models": list(models)},
        "documents": documents,
    }


@contextmanager
def indexed_sample(
    settings: Settings, llm: LLM | None, name: str, flags: dict[str, bool] | None = None
) -> Iterator[tuple[int, list[int]]]:
    """The sample's files indexed by the pipeline as an upload is, by `llm` or the models the
    settings name, in an organisation called `name` made for the run with the feature `flags`
    chosen for it, yielding its id and the documents' in the files' order; the organisation is
    deleted after with what the run recorded of its usage, so that the calls are no
    organisation's.

    The vectors are left out, so the embed stage is marked done before the run, which then never
    asks the embeddings endpoint; nor the scanner, since the files are the app's own. Refused
    with `RuntimeError` without a model.
    """
    llm = llm or LLM(settings)
    if not llm.available:
        raise RuntimeError(f"The sample is indexed by a model, and none answers: {llm.problem()}")
    slug = re.sub(r"\W+", "-", name.lower())
    with TemporaryDirectory(prefix=f"emudoc-{slug}-") as directory:
        services = Services(llm, Embedder(settings), LocalStorage(Path(directory)))
        with SessionLocal() as session:
            organization = Organization(
                name=name,
                slug=f"{slug}-{uuid4().hex[:12]}",
                plan="enterprise",
                flags=dict(flags or {}),
            )
            session.add(organization)
            session.flush()
            org_id = organization.id
            project = Project(org_id=org_id, name=NAME)
            session.add(project)
            session.flush()
            ids = [_upload(session, project, services.storage, path) for path in _files()]
            session.commit()
        try:
            for document_id in ids:
                process_document(document_id, services)
            yield org_id, ids
        finally:
            with SessionLocal() as session:
                session.execute(delete(Organization).where(Organization.id == org_id))
                session.commit()


def _files() -> list[Path]:
    return sorted(FILES.iterdir())


def _upload(session: Session, project: Project, storage: Storage, path: Path) -> int:
    data = path.read_bytes()
    suffix = path.suffix.lower()
    document = Document(
        project_id=project.id,
        filename=path.name,
        mime_type=MIME_TYPES[suffix],
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        storage_key="",
        status="queued",
        stages={"embed": {"status": "done", "detail": ""}},
    )
    session.add(document)
    session.flush()
    document.storage_key = original_key(project.org_id, project.id, document.id, suffix)
    storage.put(document.storage_key, data, document.mime_type)
    return document.id


def _indexed(session: Session, document: Document) -> dict[str, Any]:
    """A document's entry of `index.json`: its own fields, and its rows by ordinal, which name
    one another by their place in the lists rather than by id."""
    for name in COPIED_STAGES:
        stage = document.stages.get(name, {})
        if stage.get("status") != "done":
            raise RuntimeError(
                f"The sample was not built: the {name} stage of {document.filename} is "
                f"{stage.get('status', 'pending')}: {stage.get('detail') or 'it did not run'}"
            )

    def rows(model: type, order: Any) -> list[Any]:
        return list(
            session.scalars(select(model).where(model.document_id == document.id).order_by(order))
        )

    sections = rows(Section, Section.ordinal)
    passages = rows(Passage, Passage.ordinal)
    section_at = {section.id: place for place, section in enumerate(sections)}
    passage_at = {passage.id: place for place, passage in enumerate(passages)}
    return {
        "filename": document.filename,
        "sha256": document.sha256,
        "title": document.title,
        "doc_type": document.doc_type,
        "doc_date": _iso(document.doc_date),
        "summary": document.summary,
        "page_count": document.page_count,
        "page_sizes": document.page_sizes,
        "stages": {name: document.stages[name] for name in COPIED_STAGES},
        "blocks": [
            {
                "kind": block.kind,
                "level": block.level,
                "text": block.text,
                "page": block.page,
                "bbox": block.bbox,
            }
            for block in rows(Block, Block.ordinal)
        ],
        "sections": [
            {
                "parent": None if section.parent_id is None else section_at[section.parent_id],
                "level": section.level,
                "title": section.title,
                "path": section.path,
                "summary": section.summary,
                "page_start": section.page_start,
                "page_end": section.page_end,
                "block_start": section.block_start,
                "block_end": section.block_end,
            }
            for section in sections
        ],
        "passages": [
            {
                "section": section_at[passage.section_id],
                "text": passage.text,
                "context": passage.context,
                "page_start": passage.page_start,
                "page_end": passage.page_end,
                "block_start": passage.block_start,
                "block_end": passage.block_end,
            }
            for passage in passages
        ],
        "facts": [
            {
                "name": fact.name,
                "value_type": fact.value_type,
                "value": fact.value,
                "number": fact.number,
                "date": _iso(fact.date),
                "currency": fact.currency,
                "quote": fact.quote,
                "page": fact.page,
                "block_ordinal": fact.block_ordinal,
                "passage": None if fact.passage_id is None else passage_at[fact.passage_id],
                "verified": fact.verified,
            }
            for fact in rows(Fact, Fact.id)
        ],
    }


def _iso(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def written(index: dict[str, Any]) -> str:
    """`index.json` as it is kept: a document's fields a line each and its rows a line each, so
    that a rebuild's changes read row by row in a diff."""

    def compact(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False)

    def items(entries: list[str], indent: str) -> list[str]:
        return [
            f"{indent}{entry}{',' if place < len(entries) - 1 else ''}"
            for place, entry in enumerate(entries)
        ]

    documents = []
    for document in index["documents"]:
        fields = []
        for key, value in document.items():
            if key in TABLES:
                rows = items([compact(row) for row in value], "        ")
                fields.append("\n".join([f'"{key}": [', *rows, "      ]"]))
            else:
                fields.append(f"{compact(key)}: {compact(value)}")
        documents.append("\n".join(["{", *items(fields, "      "), "    }"]))
    lines = [
        "{",
        f'  "built": {compact(index["built"])},',
        '  "documents": [',
        *items(documents, "    "),
        "  ]",
        "}",
    ]
    return "\n".join(lines) + "\n"
