"""Drafts: documents the agent writes from a project's documents for its members to edit and send,
a reply email, a dispute letter, a summary memo, a comparison table or a report, each kept with
its versions.

A member asks for a draft of a kind, says what it should say, and names the documents it is about
(`start_draft`; a disputed document's "Write a dispute letter" asks for one about it); the
worker's `write_draft` job has the agent write it (`write_draft`). The input is the server's, as a
discrepancy report's is (app/check_runs.py): what the member asked, each document with the facts
read from it beside the references of the passages they were read from, which the agent may cite
from the start, and what the project's newest run of cross-checks with a finding about them found
(`findings_about`), never the model's choice of what was compared; its five read-only tools read
around them. Each kind is written to its template in force for the project, the built-in one or
the project's own (app/draft_templates.py), said after the rules every draft keeps (`DRAFT_TASK`),
which the template cannot change.

A version is never changed: the agent's writing adds one, and so does each member's save
(`save_version`) and each writing again, the older ones kept to go back to. A version's text cites
passages as [P<id>] and keeps the passages it cites as they were (`cited_sources`), since a
document's passages are written anew at every reading: a version reads the same after its
documents are read again or deleted, its citations opening what is there now.

It is model spend: the writing's calls are recorded as `draft` usage of the draft, held to the
day's spending limits as it starts, and each asking counts as one of the month's questions
(app/api/drafts.py). It is written whole, not streamed: the page asks for the draft until its
writing settles. What a draft says is what documents say, like a chat's answers: the members'
pages hold it, never the audit log, a log line or a notice, and a draft is kept as long as the
organisation keeps a chat, from when it was last asked for or saved (`prune_drafts` in
app/retention.py).
"""

import logging
import re
from collections.abc import Generator
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.orm import Session

from app.agent.loop import AGENT_PROMPTS, as_data, run_agent
from app.agent.tools import worded_value
from app.answers import Reply
from app.check_runs import findings_about, latest_run_about, sources_of
from app.db import SessionLocal
from app.draft_templates import KIND_FORMS, OWN_TEMPLATE, kind_task, template_for
from app.indexing.pipeline import Services
from app.jobs import LIVE, WRITE_DRAFT, Lease, LeaseLost, enqueue, renew
from app.models import Document, Draft, DraftVersion, Fact, Project, User, WorkflowRun
from app.prompts import prompt_version
from app.spending import check_platform_spend, check_spend
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
# The facts a draft's input lists of each document it is about (DRAFT_DOCUMENTS in app/schemas.py
# is how many documents that may be).
DOCUMENT_FACTS = 40
# The statuses of a writing under way, which a member's save and another writing wait for.
WRITING = ("queued", "writing")
# How many characters of the member's words a draft about no document takes its title from.
TITLE_CHARS = 80

PROJECT_GONE = (
    "The project went into the trash before the draft was written. Restore it to write it again."
)
WRITE_FAILED = "The draft failed unexpectedly. Ask for it again in a moment."
NOTHING_WRITTEN = "The model wrote nothing. Ask for the draft again."

DRAFT_TASK = """\
Your task now is a draft for the project's members to edit and send, not an answer to a \
question. The message says what kind of draft to write, what the member asked of it, and what \
the server read for it: the documents it is about, each with the facts read from it, and what a \
run of the project's cross-checks found about them. Those findings are the server's: state them \
as they are, never decide again whether a rule holds.

Write the draft itself in Markdown, ready to edit, with no word to the member before or after \
it. Rest every statement about what a document says on the facts given or on your tools' \
results, and cite its passage straight after it: the references given in the message, or \
returned by your tools, and no other. Where the draft needs what no document states (a name, an \
address, a date to answer by), write a placeholder in square brackets, such as [Your name], \
rather than make it up. Use the tools to read what the facts alone do not settle.

"""
# The version of a draft's prompts (app/prompts.py), which its usage rows carry: the agent's, the
# task, and how a kind is told (a template's own tone and parts vary by draft, in none).
DRAFT_PROMPT_VERSION = prompt_version(
    *AGENT_PROMPTS, DRAFT_TASK, kind_task, *KIND_FORMS.values(), OWN_TEMPLATE
)

CITATION = re.compile(r"\[P(\d+)\]")
_REVIEWS = {"to_review": "in review", "approved": "approved", "disputed": "disputed"}


def draft_title(kind: str, titles: list[str], instructions: str) -> str:
    """A draft's name in the project's list: its kind, and the documents it is about or the
    start of what the member asked of it."""
    name = KIND_NAMES[kind]
    if titles:
        about = titles[0] if len(titles) == 1 else f"{titles[0]} and {len(titles) - 1} more"
        return f"{name}: {about}"[:300]
    words = " ".join(instructions.split())
    if not words:
        return name
    if len(words) > TITLE_CHARS:
        # Cut at the last whole word that fits.
        words = words[:TITLE_CHARS].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return f"{name}: {words}"


def start_draft(
    session: Session,
    project: Project,
    user_id: int,
    kind: str,
    instructions: str,
    documents: list[Document],
) -> Draft:
    """A draft of the kind about the documents, queued for the worker to write in the caller's
    session. The caller commits."""
    draft = Draft(
        org_id=project.org_id,
        project_id=project.id,
        created_by=user_id,
        kind=kind,
        title=draft_title(kind, [document.display_title for document in documents], instructions),
        instructions=instructions,
        documents=[{"id": document.id, "title": document.display_title} for document in documents],
    )
    session.add(draft)
    session.flush()
    queue_draft(session, draft, user_id)
    return draft


def queue_draft(session: Session, draft: Draft, user_id: int) -> None:
    """Queue the job that writes the draft for the member who asked, ahead of uploads: they are
    waiting for it."""
    draft.status, draft.error, draft.notice = "queued", None, None
    draft.updated_at = func.now()
    enqueue(
        session,
        WRITE_DRAFT,
        org_id=draft.org_id,
        project_id=draft.project_id,
        payload={"draft_id": draft.id, "user_id": user_id},
        priority=LIVE,
    )


def latest_number(session: Session, draft_id: int) -> int:
    """The number of the draft's newest version, 0 for none."""
    return (
        session.scalar(
            select(func.max(DraftVersion.number)).where(DraftVersion.draft_id == draft_id)
        )
        or 0
    )


def save_version(session: Session, draft: Draft, text: str, user_id: int) -> DraftVersion:
    """A member's text as the draft's newest version, keeping the passages it cites of those its
    versions cited before. The caller holds the draft's row and commits."""
    known: dict[int, dict[str, Any]] = {}
    for sources in session.scalars(
        select(DraftVersion.sources)
        .where(DraftVersion.draft_id == draft.id)
        .order_by(DraftVersion.number)
    ):
        known.update({source["id"]: source for source in sources})
    version = DraftVersion(
        draft_id=draft.id,
        number=latest_number(session, draft.id) + 1,
        text=text,
        sources=cited_sources(text, known),
        by_model=False,
        user_id=user_id,
    )
    session.add(version)
    draft.updated_at = func.now()
    return version


def cited_sources(text: str, known: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    """The passages a text cites, of those known, in the order it first cites them."""
    cited = dict.fromkeys(int(match) for match in CITATION.findall(text))
    return [known[passage_id] for passage_id in cited if passage_id in known]


def write_draft(
    draft_id: int, user_id: int | None, services: Services, lease: Lease | None = None
) -> str | None:
    """The worker's handler: have the agent write the draft for the member who asked, kept as
    its newest version. Run again after a lost lease, it starts the writing over. If it breaks,
    the writing ends failed with a sentence before the job fails with the traceback."""
    try:
        _write(draft_id, user_id, services, lease)
    except LeaseLost:
        raise
    except Exception:
        log.exception("Draft %s failed", draft_id)
        fail_draft(draft_id)
        raise
    return None


def _write(draft_id: int, user_id: int | None, services: Services, lease: Lease | None) -> None:
    with SessionLocal() as session:
        draft = session.get(Draft, draft_id)
        if draft is None or draft.status not in WRITING:
            # Deleted, or written already.
            return
        project = session.get(Project, draft.project_id)
        if project.deleted_at is not None:
            _settle(session, draft_id, None, Reply(error=PROJECT_GONE), None, lease)
            return
        # Whose usage the model calls are, read before the rollback below expires the rows:
        # reading one afterwards would take a connection back for the whole writing.
        asker = session.scalar(select(User.id).where(User.id == user_id)) if user_id else None
        payer = {"org_id": draft.org_id, "project_id": draft.project_id, "user_id": asker}
        project_id = draft.project_id
        # The kind's template as it is now, as the documents are read as they are now.
        task = DRAFT_TASK + kind_task(template_for(session, project_id, draft.kind))
        over = check_platform_spend(session) or check_spend(session, project.organization)
        draft.status = "writing"
        _commit(session, lease)
        message, sources, run_id = draft_input(session, session.get(Draft, draft_id))
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
        log.info("Draft %s was not written: %s", draft_id, over)
        events = _only({"type": "error", "message": over})
    reply = Reply(sources=dict(sources))
    charged = False
    try:
        for event in events:
            reply.apply(event)
        events.close()
        # What the writing used is recorded before it is settled: the page stops asking at
        # done, so a draft done before its usage would never show it.
        charged = True
        _charge(usage, draft_id, payer)
        with SessionLocal() as session:
            _settle(session, draft_id, asker, reply, run_id, lease)
    finally:
        if not charged:
            try:
                events.close()
            finally:
                _charge(usage, draft_id, payer)


def _only(event: dict[str, Any]) -> Generator[dict[str, Any]]:
    yield event


def _settle(
    session: Session,
    draft_id: int,
    user_id: int | None,
    reply: Reply,
    run_id: int | None,
    lease: Lease | None,
) -> None:
    """Keep the writing as it ended: the draft's newest version, or why it failed, the versions
    before kept either way; committed with the lease renewed. A draft deleted meanwhile, or no
    longer being written, takes nothing."""
    draft = session.scalar(
        select(Draft).where(Draft.id == draft_id, Draft.status.in_(WRITING)).with_for_update()
    )
    if draft is not None:
        error = reply.error or (None if reply.content.strip() else NOTHING_WRITTEN)
        if error is not None:
            draft.status, draft.error = "failed", error
        else:
            session.add(
                DraftVersion(
                    draft_id=draft_id,
                    number=latest_number(session, draft_id) + 1,
                    text=reply.content,
                    sources=cited_sources(reply.content, reply.sources),
                    by_model=True,
                    user_id=_still(session, User, user_id),
                )
            )
            draft.status, draft.error = "done", None
            # The run its findings came from, unless it was deleted meanwhile.
            draft.run_id = _still(session, WorkflowRun, run_id)
        draft.notice = reply.notice
        draft.updated_at = func.now()
    _commit(session, lease)


def _still(
    session: Session, model: type[User] | type[WorkflowRun], row_id: int | None
) -> int | None:
    """The row's id if it is still there, held from deletion until the commit, else None."""
    if row_id is None:
        return None
    return session.scalar(
        select(model.id).where(model.id == row_id).with_for_update(key_share=True)
    )


def _charge(usage: ModelUsage, draft_id: int, payer: dict[str, int | None]) -> None:
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
                    ref=(DRAFT, draft_id),
                )
            session.commit()
    except PoolTimeout:
        log.warning(
            "The usage of draft %s was not recorded: every database connection is in use",
            draft_id,
        )


def _commit(session: Session, lease: Lease | None) -> None:
    if lease is not None and not renew(session, lease):
        session.rollback()
        raise LeaseLost(lease)
    session.commit()


def fail_draft(draft_id: int, error: str | None = None) -> None:
    """End the draft's writing under way failed, its versions kept."""
    with SessionLocal() as session:
        session.execute(
            update(Draft)
            .where(Draft.id == draft_id, Draft.status.in_(WRITING))
            .values(status="failed", error=error or WRITE_FAILED, updated_at=func.now())
        )
        session.commit()


def abandon_draft(draft_id: int, attempts: int) -> None:
    """Every run of the job was lost to a dying process: the writing ends failed, for a member
    to ask again."""
    fail_draft(
        draft_id,
        f"It stopped unexpectedly {attempts} times and was not tried again. Try again in a moment.",
    )


def draft_input(
    session: Session, draft: Draft
) -> tuple[str, dict[int, dict[str, Any]], int | None]:
    """What the draft is written from: its kind, what the member asked of it, the documents it is
    about that are still in the project out of the trash, each with the facts read from it beside
    the references of their passages, and what the newest run of the project's cross-checks with
    a finding about them found; the passages as the agent's sources, and that run's id."""
    lines = [
        f"The draft to write: {KIND_NAMES[draft.kind].lower()}.",
        "",
        "What the member asked of it:",
        draft.instructions.strip() or "Nothing beyond its kind.",
    ]
    asked = [named["id"] for named in draft.documents]
    found = {
        document.id: document
        for document in session.scalars(
            select(Document).where(
                Document.id.in_(asked),
                Document.project_id == draft.project_id,
                Document.deleted_at.is_(None),
            )
        )
    }
    documents = [found[document_id] for document_id in asked if document_id in found]
    if not asked:
        lines += ["", "No document was named: find those the request is about with your tools."]
        return "\n".join(lines), {}, None

    facts = list(
        session.scalars(
            select(Fact)
            .where(Fact.document_id.in_(list(found)), Fact.project_id == draft.project_id)
            .order_by(Fact.document_id, Fact.name, Fact.id)
        )
    )
    sources = sources_of(
        session, draft.project_id, {fact.passage_id for fact in facts if fact.passage_id}
    )
    # What the documents say, and what the checks found of them, fenced as the tools' results
    # are: the member's request above is the one instruction of the message.
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
    run = latest_run_about(session, draft.project_id, list(found))
    if run is None:
        return "\n".join(lines), sources, None
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
    return "\n".join(lines), sources, run.id


def _document_lines(
    document: Document, facts: list[Fact], sources: dict[int, dict[str, Any]]
) -> list[str]:
    """One document for the draft's input: its names, its kind and review, its summary, and the
    facts read from it with the references of the passages they were read from."""
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
