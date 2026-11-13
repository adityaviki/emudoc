"""Writings: a document of a kind the agent writes into a member's My files outside a chat, to the
project's template of the kind (app/draft_templates.py): today a disputed document's dispute
letter, which its review asks for (`start_writing`).

The worker's `write_draft` job has the agent write it (`write_document`). The input is the
server's, as a discrepancy report's is (app/check_runs.py): the kind, each document it is about
with the facts read from it beside the references of the passages they were read from, which the
agent may cite from the start, and what the project's newest run of cross-checks with a finding
about them found (`findings_about`), never the model's choice of what was compared; its five
read-only tools read around them. The kind's template in force is said after the rules every such
document keeps (`DRAFT_TASK`), which the template cannot change.

The agent writes Markdown, kept as a Word document in the member's My files (`draft_file` in
app/draft_export.py, as `create_document` writes one in a chat, app/agent/writing.py), its
citations numbered notes naming the passages: a version by the agent, the member's alone, which
they open, download, give new versions and copy into the project's library as any file of theirs
(app/my_files.py). The writing's row (`MyFileWriting`) is kept while it waits and is written,
which their My files show, and goes in the transaction that keeps the file; one that failed stays,
with its sentence, until they try it again or dismiss it.

It is model spend: the writing's calls are recorded as `draft` usage, held to the day's spending
cap as it starts, and each asking counts as one of the workspace's questions
(app/api/my_files.py). It is written whole, not streamed: the page asks for the member's My files
until it settles.
"""

import io
import logging
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.orm import Session

from app.agent.loop import AGENT_PROMPTS, as_data, run_agent
from app.agent.tools import worded_value
from app.answers import Reply
from app.audit import audit_event
from app.check_runs import findings_about, latest_run_about, sources_of
from app.db import SessionLocal
from app.draft_export import draft_file, file_stem
from app.draft_templates import kind_task, template_for
from app.indexing.pipeline import Services
from app.jobs import LIVE, WRITE_DRAFT, Lease, LeaseLost, enqueue, renew
from app.models import Document, Fact, MyFileWriting, Organization, Project
from app.my_files import Refused, add_file, newest
from app.prompts import prompt_version
from app.spending import check_spend
from app.usage import DRAFT, ModelUsage, record

log = logging.getLogger(__name__)

# What each kind is called, in its title and in the agent's input.
KIND_NAMES = {
    "reply_email": "Reply email",
    "dispute_letter": "Dispute letter",
    "summary_memo": "Summary memo",
    "comparison_table": "Comparison table",
    "report": "Report",
}
# The facts the input lists of each document it is about (DRAFT_DOCUMENTS in app/schemas.py is
# how many documents that may be).
DOCUMENT_FACTS = 40
# The statuses of a writing under way, which trying it again and settling it wait for.
WRITING = ("queued", "writing")

PROJECT_GONE = (
    "The project went into the trash before the document was written. Restore it to write it again."
)
WRITE_FAILED = "The writing failed unexpectedly. Try it again in a moment."
NOTHING_WRITTEN = "The model wrote nothing. Try it again."

DRAFT_TASK = """\
Your task now is a document for the member to edit and send, kept as a Word document in their \
My files, not an answer to a question. The message says what kind of document to write and what \
the server read for it: the documents it is about, each with the facts read from it, and what a \
run of the project's cross-checks found about them. Those findings are the server's: state them \
as they are, never decide again whether a rule holds.

Write the document itself in Markdown, ready to edit, with no word to the member before or after \
it. Rest every statement about what a document says on the facts given or on your tools' \
results, and cite its passage straight after it: the references given in the message, or \
returned by your tools, and no other. Where the document needs what no document states (a name, \
an address, a date to answer by), write a placeholder in square brackets, such as [Your name], \
rather than make it up. Use the tools to read what the facts alone do not settle.

"""
# The version of a writing's prompts (app/prompts.py), which its usage rows carry: the agent's,
# which hold how a kind's template is told, and the task.
DRAFT_PROMPT_VERSION = prompt_version(*AGENT_PROMPTS, DRAFT_TASK)

_REVIEWS = {"to_review": "in review", "approved": "approved", "disputed": "disputed"}


def writing_title(kind: str, titles: list[str]) -> str:
    """A writing's name, and its file's: its kind, and the documents it is about."""
    name = KIND_NAMES[kind]
    if not titles:
        return name
    about = titles[0] if len(titles) == 1 else f"{titles[0]} and {len(titles) - 1} more"
    return f"{name} about {about}"[:300]


def start_writing(
    session: Session, project: Project, user_id: int, kind: str, documents: list[Document]
) -> MyFileWriting:
    """A document of the kind about the documents, queued for the worker to write into the
    member's My files, in the caller's session. The caller commits."""
    writing = MyFileWriting(
        org_id=project.org_id,
        project_id=project.id,
        user_id=user_id,
        kind=kind,
        title=writing_title(kind, [document.display_title for document in documents]),
        documents=[{"id": document.id, "title": document.display_title} for document in documents],
    )
    session.add(writing)
    session.flush()
    queue_writing(session, writing)
    return writing


def queue_writing(session: Session, writing: MyFileWriting) -> None:
    """Queue the job that writes it for the member who asked, ahead of uploads: they are waiting
    for it."""
    writing.status, writing.error = "queued", None
    writing.updated_at = func.now()
    enqueue(
        session,
        WRITE_DRAFT,
        org_id=writing.org_id,
        project_id=writing.project_id,
        payload={"writing_id": writing.id},
        priority=LIVE,
    )


def writings_of(session: Session, project_id: int, user_id: int) -> list[MyFileWriting]:
    """The member's writings in the project still under way or failed, the newest first."""
    return list(
        session.scalars(
            select(MyFileWriting)
            .where(MyFileWriting.project_id == project_id, MyFileWriting.user_id == user_id)
            .order_by(MyFileWriting.id.desc())
        )
    )


def write_document(writing_id: int, services: Services, lease: Lease | None = None) -> str | None:
    """The worker's handler: have the agent write the document and keep it in the member's My
    files. Run again after a lost lease, it starts the writing over. If it breaks, the writing
    ends failed with a sentence before the job fails with the traceback."""
    try:
        _write(writing_id, services, lease)
    except LeaseLost:
        raise
    except Exception:
        log.exception("Writing %s failed", writing_id)
        fail_writing(writing_id)
        raise
    return None


def _write(writing_id: int, services: Services, lease: Lease | None) -> None:
    with SessionLocal() as session:
        writing = session.get(MyFileWriting, writing_id)
        if writing is None or writing.status not in WRITING:
            # Dismissed, or settled already.
            return
        project = session.get_one(Project, writing.project_id)
        if project.deleted_at is not None:
            _settle_failed(session, writing_id, PROJECT_GONE, lease)
            return
        # Whose usage the model calls are, read before the rollback below expires the rows:
        # reading one afterwards would take a connection back for the whole writing.
        payer = {
            "org_id": writing.org_id,
            "project_id": writing.project_id,
            "user_id": writing.user_id,
        }
        project_id, title = writing.project_id, writing.title
        # The kind's template as it is now, as the documents are read as they are now.
        task = DRAFT_TASK + kind_task(template_for(session, project_id, writing.kind))
        over = check_spend(session)
        writing.status = "writing"
        _commit(session, lease)
        message, sources = writing_input(session, session.get_one(MyFileWriting, writing_id))
        # The reads are done, and the agent loop opens sessions of its own per tool call: no
        # connection is held while the model writes.
        session.rollback()

    usage = ModelUsage()
    events: Generator[dict[str, Any]]
    if over is None:
        events = run_agent(
            services,
            project_id,
            [{"role": "user", "content": message}],
            usage,
            task=task,
            sources=sources,
        )
    else:
        log.info("Writing %s was not written: %s", writing_id, over)
        events = _only({"type": "error", "message": over})
    reply = Reply(sources=dict(sources))
    charged = False
    try:
        for event in events:
            reply.apply(event)
        events.close()
        # What the writing used is recorded before it is settled: the page stops asking once
        # the file is there, so a writing settled before its usage would never show it.
        charged = True
        _charge(usage, writing_id, payer)
        with SessionLocal() as session:
            _settle(session, services, writing_id, title, reply, lease)
    finally:
        if not charged:
            try:
                events.close()
            finally:
                _charge(usage, writing_id, payer)


def _only(event: dict[str, Any]) -> Generator[dict[str, Any]]:
    yield event


def _settle(
    session: Session,
    services: Services,
    writing_id: int,
    title: str,
    reply: Reply,
    lease: Lease | None,
) -> None:
    """Keep the writing as it ended: its Word file in the member's My files, the writing gone in
    the same transaction, or why it failed; committed with the lease renewed. A writing dismissed
    meanwhile, or no longer being written, takes nothing."""
    error = reply.error or (None if reply.content.strip() else NOTHING_WRITTEN)
    if error is not None:
        _settle_failed(session, writing_id, error, lease)
        return
    # Made before the row is held: a long document takes a moment.
    data = draft_file("docx", reply.content, list(reply.sources.values()), title, datetime.now(UTC))
    writing = _held(session, writing_id)
    if writing is None:
        session.rollback()
        return
    kind, user_id = writing.kind, writing.user_id
    session.delete(writing)
    _renew(session, lease)
    try:
        file = add_file(
            session,
            services.storage,
            session.get_one(Organization, writing.org_id),
            session.get_one(Project, writing.project_id),
            user_id,
            f"{file_stem(title, KIND_NAMES[kind])}.docx",
            io.BytesIO(data),
            scanner=services.scanner,
            converter=services.converter,
            by_model=True,
            # What its versions' list says of it, beside "Written by the agent".
            note=KIND_NAMES[kind],
        )
    except Refused as refused:
        # Nothing was kept, the writing's deletion with it: it fails with My files' reason.
        _settle_failed(session, writing_id, refused.reason, lease)
        return
    # The member's row of the audit log, as the agent's writing in a chat has one
    # (app/agent/writing.py), with the kind it wrote in place of the chat.
    audit_event(
        session,
        "my_file_added",
        org_id=file.org_id,
        user_id=user_id,
        project_id=file.project_id,
        target=("my_file", file.id),
        detail={
            "kind": file.kind,
            "by_model": True,
            "writing": kind,
            "size_bytes": newest(session, file.id).size_bytes,
        },
    )
    session.commit()


def _settle_failed(session: Session, writing_id: int, error: str, lease: Lease | None) -> None:
    """End the writing failed with the sentence its member reads, committed with the lease
    renewed; one dismissed meanwhile, or no longer being written, takes nothing."""
    writing = _held(session, writing_id)
    if writing is not None:
        writing.status, writing.error = "failed", error
        writing.updated_at = func.now()
    _commit(session, lease)


def _held(session: Session, writing_id: int) -> MyFileWriting | None:
    """The writing under way, held until the commit."""
    return session.scalar(
        select(MyFileWriting)
        .where(MyFileWriting.id == writing_id, MyFileWriting.status.in_(WRITING))
        .with_for_update()
    )


def _charge(usage: ModelUsage, writing_id: int, payer: dict[str, int | None]) -> None:
    """Record the writing's model calls as the usage of the member who asked for it, in a
    transaction of its own, whichever way its run ended."""
    if not usage.calls:
        return
    try:
        with SessionLocal() as session:
            for model, calls in usage.models.items():
                record(
                    session,
                    **payer,
                    kind=DRAFT,
                    measures=calls.measures,
                    model=model,
                    prompt_version=DRAFT_PROMPT_VERSION,
                )
            session.commit()
    except PoolTimeout:
        log.warning(
            "The usage of writing %s was not recorded: every database connection is in use",
            writing_id,
        )


def _renew(session: Session, lease: Lease | None) -> None:
    """Renew the lease in the session's transaction, or roll it back and stop: another worker
    holds the job now."""
    if lease is not None and not renew(session, lease):
        session.rollback()
        raise LeaseLost(lease)


def _commit(session: Session, lease: Lease | None) -> None:
    _renew(session, lease)
    session.commit()


def fail_writing(writing_id: int, error: str | None = None) -> None:
    """End the writing under way failed."""
    with SessionLocal() as session:
        session.execute(
            update(MyFileWriting)
            .where(MyFileWriting.id == writing_id, MyFileWriting.status.in_(WRITING))
            .values(status="failed", error=error or WRITE_FAILED, updated_at=func.now())
        )
        session.commit()


def abandon_writing(writing_id: int, attempts: int) -> None:
    """Every run of the job was lost to a dying process: the writing ends failed, for its member
    to try again."""
    fail_writing(
        writing_id,
        f"It stopped unexpectedly {attempts} times and was not tried again. Try again in a moment.",
    )


def writing_input(
    session: Session, writing: MyFileWriting
) -> tuple[str, dict[int, dict[str, Any]]]:
    """What the document is written from: its kind, the documents it is about that are still in
    the project out of the trash, each with the facts read from it beside the references of their
    passages, and what the newest run of the project's cross-checks with a finding about them
    found; and the passages, as the agent's sources."""
    lines = [f"The document to write: {KIND_NAMES[writing.kind].lower()}."]
    asked = [named["id"] for named in writing.documents]
    found = {
        document.id: document
        for document in session.scalars(
            select(Document).where(
                Document.id.in_(asked),
                Document.project_id == writing.project_id,
                Document.deleted_at.is_(None),
            )
        )
    }
    documents = [found[document_id] for document_id in asked if document_id in found]
    if not documents:
        lines += [
            "",
            "The documents it was asked about were deleted since, or are in the trash: find "
            "those it is about with your tools.",
        ]
        return "\n".join(lines), {}

    facts = list(
        session.scalars(
            select(Fact)
            .where(Fact.document_id.in_(list(found)), Fact.project_id == writing.project_id)
            .order_by(Fact.document_id, Fact.name, Fact.id)
        )
    )
    sources = sources_of(
        session, writing.project_id, {fact.passage_id for fact in facts if fact.passage_id}
    )
    # What the documents say, and what the checks found of them, fenced as the tools' results
    # are: the server's words above are the one instruction of the message.
    read: dict[int, list[Fact]] = {}
    for fact in facts:
        read.setdefault(fact.document_id, []).append(fact)
    about = [
        "\n".join(_document_lines(document, read.get(document.id, []), sources))
        for document in documents
    ]
    lines += ["", "The documents it is about:", as_data("\n\n".join(about))]
    if gone := len(asked) - len(documents):
        lines += [
            "",
            f"{gone} more of the documents asked about were deleted since, or are in the trash: "
            "leave them out.",
        ]
    run = latest_run_about(session, writing.project_id, list(found))
    if run is None:
        return "\n".join(lines), sources
    findings, passages = findings_about(session, run, list(found))
    lines.append("")
    if findings:
        lines.append(as_data("\n".join(findings)))
        sources.update(passages)
    else:
        lines.append(
            f"A run of the project's cross-checks made {run.created_at:%d %B %Y at %H:%M} UTC "
            "found every value it compared about them to hold."
        )
    return "\n".join(lines), sources


def _document_lines(
    document: Document, facts: list[Fact], sources: dict[int, dict[str, Any]]
) -> list[str]:
    """One document for the input: its names, its kind and review, its summary, and the facts
    read from it with the references of the passages they were read from."""
    kind = document.template or document.doc_type
    about = [f'Document {document.id}: "{document.display_title}" ({document.filename})']
    if kind:
        about.append(f"{'an' if kind[:1].lower() in 'aeiou' else 'a'} {kind}")
    if document.review:
        about.append(f"{_REVIEWS[document.review]} by a member")
    lines = [", ".join(about) + "."]
    if document.summary:
        lines.append(f"Summary: {document.summary}")
    if not facts:
        lines.append("No facts were read from it.")
        return lines
    lines.append("Facts read from it:")
    for fact in facts[:DOCUMENT_FACTS]:
        where = (
            f" [P{fact.passage_id}]"
            if fact.passage_id in sources
            else (f" (page {fact.page})" if fact.page else "")
        )
        lines.append(f"- {fact.name}: {worded_value(fact)}{where}")
    if len(facts) > DOCUMENT_FACTS:
        lines.append(f"- And {len(facts) - DOCUMENT_FACTS} more, not listed.")
    return lines
