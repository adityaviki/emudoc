"""Drafts (app/drafts.py): a member asks the agent for a draft of a kind about some of a project's
documents, which the worker writes, then edits it into new versions or has it written again; the
project's members read its drafts, newest first, and each one's versions, and download a version
as a Word document, a PDF or its Markdown (app/draft_export.py). How the project's drafts of each
kind read is its template (app/draft_templates.py): the built-in one, or the project's own in its
place. A draft an answer offers (`create_draft`, app/agent/tools.py) is written by the member's
press under it, as one asked for here.

Whoever may change the project asks for a draft, saves a version and deletes one, and writes its
templates, each a row of the organisation's audit log naming the draft by its kind and id alone,
never what it says, and a template by its kind and its parts' names; anyone who sees the project
reads them, and downloads a draft, which the audit log keeps too. A writing is a run of the agent,
so asking for one is held to what holds a question: the agent's runs in flight and the day's
spending; it counts as one of the workspace's questions, and a draft an answer offered counts none,
its question counted already.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.accounts import Account
from app.api.chats import (
    STILL_ANSWERING,
    answer_in,
    refuse_past_answers_in_flight,
    refuse_past_spending,
)
from app.api.check_runs import count_question, refuse_agent_run
from app.api.deps import (
    AccountDep,
    DraftDep,
    EditableDraftDep,
    EditableProjectDep,
    EditorDep,
    OwnChatDep,
    ProjectDep,
    SessionDep,
    limited,
)
from app.audit import audit
from app.check_runs import gone_documents
from app.draft_export import MEDIA_TYPES, ExportFormat, ExportRefused, draft_file, file_name
from app.draft_templates import Template, template_of, templates_for
from app.drafts import WRITING, latest_number, queue_draft, save_version, start_draft
from app.models import ChatMessage, Document, Draft, DraftTemplate, DraftVersion, Project, User
from app.ratelimit import EXPORTS, QUESTIONS
from app.schemas import (
    DraftDocumentOut,
    DraftIn,
    DraftKind,
    DraftOut,
    DraftRewriteIn,
    DraftSectionOut,
    DraftsOut,
    DraftSummaryOut,
    DraftTemplateIn,
    DraftTemplateOut,
    DraftTemplatesOut,
    DraftVersionIn,
    DraftVersionOut,
    DraftVersionSummaryOut,
)
from app.storage import content_disposition

router = APIRouter(tags=["drafts"])

WRITING_NOW = (
    "The model is writing this draft now. Wait for its version, then edit that one or ask again."
)
SAVED_MEANWHILE = (
    "A newer version of this draft was saved while you were editing it. Your text is still in "
    "the editor: save it again to keep it as the newest version, or cancel to read the other."
)
NO_SUCH_VERSION = "This draft has no such version."
NO_OWN_TEMPLATE = "This project has no template of its own for this kind: the built-in one applies."
NO_OFFER = "This answer offers no draft. Ask for one in the chat, or on the project's drafts page."
OFFER_GONE = (
    "The draft written from this answer was deleted. Ask for a new one on the project's drafts "
    "page."
)

# Drafts a page of the project's list.
DRAFTS_SHOWN = 20
MAX_DRAFTS_SHOWN = 100


@router.post(
    "/projects/{project_id}/drafts",
    response_model=DraftOut,
    status_code=201,
    dependencies=[Depends(limited(QUESTIONS))],
)
def ask_draft(
    project: EditableProjectDep,
    body: DraftIn,
    session: SessionDep,
    account: AccountDep,
    request: Request,
):
    """Have the agent write a draft of the kind about the documents named, or about what it finds
    when none are; the worker writes it, which the page follows."""
    asked = list(dict.fromkeys(body.document_ids))
    found = {
        document.id: document
        for document in session.scalars(
            select(Document).where(
                Document.id.in_(asked),
                Document.project_id == project.id,
                Document.deleted_at.is_(None),
            )
        )
    }
    if len(found) < len(asked):
        raise HTTPException(404, "Document not found")
    refuse_agent_run(session, account, f"A draft in project {project.id}")
    draft = start_draft(
        session,
        project,
        account.user.id,
        body.kind,
        body.instructions.strip(),
        [found[document_id] for document_id in asked],
    )
    count_question(session, account, project.id)
    _audit(session, account, request, "draft_created", draft, documents=len(asked))
    session.commit()
    return _out(session, draft)


@router.post(
    "/chats/{chat_id}/messages/{message_id}/draft",
    response_model=DraftOut,
    status_code=201,
    dependencies=[Depends(limited(QUESTIONS))],
)
def write_offered_draft(
    chat: OwnChatDep,
    message_id: int,
    session: SessionDep,
    account: EditorDep,
    request: Request,
    response: Response,
):
    """Have the draft the answer offers written, as one asked for on the drafts page: its kind,
    its instructions and those of its documents still in the project. Its question was counted
    when it was asked, so this counts none, held to the rest that holds a question, the agent's
    runs in flight and the day's spending, since the writing is a run of the model of its own.
    An offer is written once: pressed again, it answers the draft written from it."""
    message = answer_in(chat, message_id, session)
    # The lock orders this with a second press, and with a stop or the run settling the answer.
    session.execute(select(ChatMessage.id).where(ChatMessage.id == message.id).with_for_update())
    session.refresh(message)
    if message.status == "streaming":
        raise HTTPException(409, STILL_ANSWERING)
    offer = message.draft_offer
    if offer is None:
        raise HTTPException(404, NO_OFFER)
    if offer.get("written"):
        if message.draft_id is None:
            raise HTTPException(409, OFFER_GONE)
        response.status_code = 200
        return _out(session, session.get_one(Draft, message.draft_id))
    what = f"The draft of answer {message.id}"
    refuse_past_answers_in_flight(session, account, what)
    refuse_past_spending(session, what)
    asked = [named["id"] for named in offer["documents"]]
    found = {
        document.id: document
        for document in session.scalars(
            select(Document).where(
                Document.id.in_(asked),
                Document.project_id == chat.project_id,
                Document.deleted_at.is_(None),
            )
        )
    }
    documents = [found[document_id] for document_id in asked if document_id in found]
    draft = start_draft(
        session,
        session.get_one(Project, chat.project_id),
        account.user.id,
        offer["kind"],
        offer["instructions"],
        documents,
    )
    message.draft_offer = {**offer, "written": True}
    message.draft_id = draft.id
    _audit(
        session, account, request, "draft_created", draft, documents=len(documents), chat=chat.id
    )
    session.commit()
    return _out(session, draft)


@router.get("/projects/{project_id}/drafts", response_model=DraftsOut)
def list_drafts(
    project: ProjectDep,
    session: SessionDep,
    before: int | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_DRAFTS_SHOWN)] = DRAFTS_SHOWN,
):
    """The project's drafts, newest first, a page at a time before the draft `before` names."""
    query = (
        select(Draft, User.name, _versions_of(Draft.id))
        .outerjoin(User, User.id == Draft.created_by)
        .where(Draft.project_id == project.id)
    )
    if before is not None:
        query = query.where(Draft.id < before)
    rows = session.execute(query.order_by(Draft.id.desc()).limit(limit + 1)).all()
    shown = rows[:limit]
    return DraftsOut(
        drafts=[_summary(draft, name, versions) for draft, name, versions in shown],
        next_before=shown[-1][0].id if len(rows) > limit else None,
    )


@router.get("/drafts/{draft_id}", response_model=DraftOut)
def get_draft(draft: DraftDep, session: SessionDep):
    """The draft whole: what was asked of it, its versions, and the newest one's text."""
    return _out(session, draft)


@router.get("/drafts/{draft_id}/versions/{number}", response_model=DraftVersionOut)
def get_version(draft: DraftDep, number: Annotated[int, Path(ge=1)], session: SessionDep):
    """One of the draft's versions, an older one to read or to start from."""
    row = session.execute(
        select(DraftVersion, User.name)
        .outerjoin(User, User.id == DraftVersion.user_id)
        .where(DraftVersion.draft_id == draft.id, DraftVersion.number == number)
    ).first()
    if row is None:
        raise HTTPException(404, NO_SUCH_VERSION)
    return _version_out(*row)


@router.get(
    "/drafts/{draft_id}/versions/{number}/export",
    response_class=Response,
    responses={200: {"content": {media: {} for media in MEDIA_TYPES.values()}}},
    dependencies=[Depends(limited(EXPORTS))],
)
def export_version(
    draft: DraftDep,
    number: Annotated[int, Path(ge=1)],
    session: SessionDep,
    account: AccountDep,
    request: Request,
    written_as: Annotated[ExportFormat, Query(alias="format")] = "docx",
):
    """One of the draft's versions as a file to save (app/draft_export.py): a Word document, with
    `format=pdf` a PDF, with `format=md` its Markdown, each citation a numbered note naming the
    passage it cites. The download is recorded once the file is made, before it is sent, by the
    draft's kind, the version and the format, never what it says."""
    version = session.scalar(
        select(DraftVersion).where(DraftVersion.draft_id == draft.id, DraftVersion.number == number)
    )
    if version is None:
        raise HTTPException(404, NO_SUCH_VERSION)
    # The file is made with no transaction held: a long draft's takes a moment.
    session.commit()
    try:
        content = draft_file(
            written_as, version.text, version.sources, draft.title, version.created_at
        )
    except ExportRefused as refusal:
        raise HTTPException(422, str(refusal)) from None
    _audit(session, account, request, "draft_exported", draft, version=number, format=written_as)
    session.commit()
    return Response(
        content,
        media_type=MEDIA_TYPES[written_as],
        headers={
            "Content-Disposition": content_disposition(file_name(draft.title, number, written_as))
        },
    )


@router.post("/drafts/{draft_id}/versions", response_model=DraftOut, status_code=201)
def save(
    draft: EditableDraftDep,
    body: DraftVersionIn,
    session: SessionDep,
    account: AccountDep,
    request: Request,
):
    """Keep the member's text as the draft's newest version, provided no other was kept since
    the one they edited, and the model is not writing one."""
    _hold(session, draft)
    if draft.status in WRITING:
        raise HTTPException(409, WRITING_NOW)
    if body.base != latest_number(session, draft.id):
        raise HTTPException(409, SAVED_MEANWHILE)
    version = save_version(session, draft, body.text, account.user.id)
    _audit(session, account, request, "draft_changed", draft, version=version.number)
    session.commit()
    return _out(session, draft)


@router.post(
    "/drafts/{draft_id}/rewrite",
    response_model=DraftOut,
    dependencies=[Depends(limited(QUESTIONS))],
)
def rewrite(
    draft: EditableDraftDep,
    body: DraftRewriteIn,
    session: SessionDep,
    account: AccountDep,
    request: Request,
):
    """Have the agent write the draft again as a new version, from the documents as they read now
    and what the member asks of it now; the worker writes it, which the page follows."""
    _hold(session, draft)
    if draft.status in WRITING:
        raise HTTPException(409, WRITING_NOW)
    refuse_agent_run(session, account, f"Draft {draft.id}")
    if body.instructions is not None:
        draft.instructions = body.instructions.strip()
    queue_draft(session, draft, account.user.id)
    count_question(session, account, draft.project_id)
    _audit(session, account, request, "draft_changed", draft, rewritten=True)
    session.commit()
    return _out(session, draft)


@router.delete("/drafts/{draft_id}", status_code=204)
def delete_draft(
    draft: EditableDraftDep, session: SessionDep, account: AccountDep, request: Request
):
    """Delete the draft for good, with every version of it."""
    _audit(session, account, request, "draft_deleted", draft)
    session.delete(draft)
    session.commit()


@router.get("/projects/{project_id}/draft-templates", response_model=DraftTemplatesOut)
def list_draft_templates(project: ProjectDep, session: SessionDep):
    """The template in force for each kind of the project's drafts, in the order the kinds are
    offered: its own, or the built-in one."""
    return DraftTemplatesOut(
        templates=[_template_out(template) for template in templates_for(session, project.id)]
    )


@router.put("/projects/{project_id}/draft-templates/{kind}", response_model=DraftTemplateOut)
def save_draft_template(
    project: EditableProjectDep,
    kind: DraftKind,
    body: DraftTemplateIn,
    session: SessionDep,
    account: AccountDep,
    request: Request,
):
    """Write the project's own template of the kind, whole, in the built-in one's place: its
    tone, its parts in order and what else every draft of the kind should do. The next writing
    of a draft of the kind follows it."""
    sections = _sections(body)
    # Two first saves of one kind at once would both insert.
    session.execute(select(Project.id).where(Project.id == project.id).with_for_update())
    row = session.scalar(
        select(DraftTemplate).where(
            DraftTemplate.project_id == project.id, DraftTemplate.kind == kind
        )
    )
    if row is None:
        row = DraftTemplate(
            org_id=project.org_id,
            project_id=project.id,
            kind=kind,
            tone=body.tone,
            sections=sections,
            guidance=body.guidance,
            created_by=account.user.id,
        )
        session.add(row)
        session.flush()
        _template_audit(
            session,
            account,
            request,
            "draft_template_created",
            row,
            sections=[section["heading"] for section in sections],
        )
    elif (row.tone, row.sections, row.guidance) != (body.tone, sections, body.guidance):
        before = {section["heading"]: section for section in row.sections}
        after = {section["heading"]: section for section in sections}
        _template_audit(
            session,
            account,
            request,
            "draft_template_changed",
            row,
            added=[heading for heading in after if heading not in before],
            removed=[heading for heading in before if heading not in after],
            changed=[
                heading
                for heading in after
                if heading in before and after[heading] != before[heading]
            ],
            tone=row.tone != body.tone,
            guidance=row.guidance != body.guidance,
        )
        row.tone, row.sections, row.guidance = body.tone, sections, body.guidance
    session.commit()
    session.refresh(row)
    return _template_out(template_of(row))


@router.delete("/projects/{project_id}/draft-templates/{kind}", status_code=204)
def delete_draft_template(
    project: EditableProjectDep,
    kind: DraftKind,
    session: SessionDep,
    account: AccountDep,
    request: Request,
):
    """Delete the project's own template of the kind for good: the built-in one applies again to
    the next writing, and the drafts written already stay as they are."""
    row = session.scalar(
        select(DraftTemplate)
        .where(DraftTemplate.project_id == project.id, DraftTemplate.kind == kind)
        .with_for_update()
    )
    if row is None:
        raise HTTPException(404, NO_OWN_TEMPLATE)
    _template_audit(session, account, request, "draft_template_deleted", row)
    session.delete(row)
    session.commit()


def _sections(body: DraftTemplateIn) -> list[dict[str, Any]]:
    """The template's parts as they are kept, each named once."""
    named: set[str] = set()
    for section in body.sections:
        if section.heading.casefold() in named:
            raise HTTPException(
                400,
                f"Two parts are named “{section.heading}”. Give each part a name of its own.",
            )
        named.add(section.heading.casefold())
    return [section.model_dump() for section in body.sections]


def _template_audit(
    session: Session,
    account: Account,
    request: Request,
    action: str,
    row: DraftTemplate,
    **detail: Any,
) -> None:
    """The audit row of a draft template, named by its kind, its parts by their names and
    whether its tone and the rest it asks changed: what they say is the members' wording."""
    audit(
        session,
        account,
        request,
        action,
        project_id=row.project_id,
        target=("draft_template", row.id),
        detail={"kind": row.kind, **detail},
    )


def _template_out(template: Template) -> DraftTemplateOut:
    return DraftTemplateOut(
        kind=template.kind,
        id=template.id,
        built_in=template.id is None,
        tone=template.tone,
        sections=[
            DraftSectionOut(
                heading=section.heading,
                description=section.description,
                required=section.required,
            )
            for section in template.sections
        ],
        guidance=template.guidance,
        updated_at=template.updated_at,
    )


def _hold(session: Session, draft: Draft) -> None:
    """Lock the draft's row, which orders a save or a writing with another at once and with the
    worker settling one, and read it again."""
    session.execute(select(Draft.id).where(Draft.id == draft.id).with_for_update())
    session.refresh(draft)


def _audit(
    session: Session, account: Account, request: Request, action: str, draft: Draft, **detail
) -> None:
    """The audit row of a draft, named by its kind and id alone: what it says, its name among it,
    is what documents say."""
    audit(
        session,
        account,
        request,
        action,
        project_id=draft.project_id,
        target=("draft", draft.id),
        detail={"kind": draft.kind, **detail},
    )


def _versions_of(draft_id: Any) -> Any:
    """How many versions the draft has, as a column of the list's query."""
    return (
        select(func.count())
        .where(DraftVersion.draft_id == draft_id)
        .correlate(Draft)
        .scalar_subquery()
    )


def _summary(draft: Draft, created_by: str | None, versions: int) -> DraftSummaryOut:
    return DraftSummaryOut(
        id=draft.id,
        project_id=draft.project_id,
        kind=draft.kind,
        title=draft.title,
        status=draft.status,
        created_by=created_by,
        versions=versions,
        created_at=draft.created_at,
        updated_at=draft.updated_at,
    )


def _version_out(version: DraftVersion, author: str | None) -> DraftVersionOut:
    return DraftVersionOut(
        number=version.number,
        by_model=version.by_model,
        author=author,
        created_at=version.created_at,
        text=version.text,
        sources=version.sources,
    )


def _out(session: Session, draft: Draft) -> DraftOut:
    session.refresh(draft)
    # The versions' texts stay in the database but the newest one's.
    history = [
        DraftVersionSummaryOut(number=number, by_model=by_model, author=author, created_at=at)
        for number, by_model, at, author in session.execute(
            select(DraftVersion.number, DraftVersion.by_model, DraftVersion.created_at, User.name)
            .outerjoin(User, User.id == DraftVersion.user_id)
            .where(DraftVersion.draft_id == draft.id)
            .order_by(DraftVersion.number.desc())
        )
    ]
    latest = (
        session.execute(
            select(DraftVersion, User.name)
            .outerjoin(User, User.id == DraftVersion.user_id)
            .where(DraftVersion.draft_id == draft.id, DraftVersion.number == history[0].number)
        ).one()
        if history
        else None
    )
    created_by = session.scalar(select(User.name).where(User.id == draft.created_by))
    gone = set(
        gone_documents(session, draft.project_id, [named["id"] for named in draft.documents])
    )
    return DraftOut(
        **_summary(draft, created_by, len(history)).model_dump(),
        instructions=draft.instructions,
        documents=[
            DraftDocumentOut(id=named["id"], title=named["title"], gone=named["id"] in gone)
            for named in draft.documents
        ],
        run_id=draft.run_id,
        error=draft.error,
        notice=draft.notice,
        usage=draft.usage,
        history=history,
        latest=_version_out(*latest) if latest else None,
    )
