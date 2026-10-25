"""Chats with the agent: any number per project, each saved with its answers.

A question is answered by the worker, not in the request (app/answers.py): `ask` saves the question
and an empty answer, queues the job and returns both at once; the client then follows the answer's
events through `events`, a server-sent event stream read from the table, and can pick it up again
from the last event it saw after a reload or a dropped connection. `stop` ends an answer early with
what arrived so far, `retry` writes a stopped or failed answer again in its place, and `regenerate`
writes the chat's last answer again, from its question or the question its member edited, which the
plan counts as a question asked. A chat is about the whole project, one of its folders or documents
chosen from it (app/scopes.py), which its starter chooses as it starts and changes whenever they
like. A chat deleted goes into the trash, its starter's alone (app/trash.py); an answer still being
written there finishes. Whoever reads a chat downloads an answer of it, or the whole chat, as a Word
document, a PDF or its Markdown (app/chat_export.py), which the audit log keeps by the chat's id and
the answer's, never their words. Each reader pins the chats they read to the top of their own list.
"""

import logging
import time
from collections.abc import AsyncIterator
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.orm import Session, selectinload

from app.accounts import Account, zone_of
from app.answers import (
    KEEPALIVE,
    STOPPED,
    answers_in_flight,
    events_after,
    fold,
    retry_answer,
    settle,
    sse,
    start_answer,
)
from app.api.auth import app_url
from app.api.deps import (
    RETRY_AFTER,
    AccountDep,
    ChatDep,
    OwnChatDep,
    ProjectDep,
    SessionDep,
    limited,
)
from app.audit import audit
from app.chat_export import (
    NOT_LAID_OUT,
    Asked,
    answer_file,
    chat_file,
    exchange_of,
    exchanges_of,
    file_name,
)
from app.config import get_settings
from app.costs import costs_shown
from app.db import SessionLocal
from app.draft_export import MEDIA_TYPES, ExportFormat, ExportRefused
from app.entitlements import PlanRefused, Refusal, check_question
from app.logs import about
from app.models import Chat, ChatMessage, ChatPin, Document, Folder, Project
from app.notices import MONTH_QUESTIONS, note_quota
from app.ratelimit import EXPORTS, QUESTIONS
from app.schemas import (
    AskOut,
    ChatDetail,
    ChatIn,
    ChatMessageOut,
    ChatOut,
    ChatPatch,
    ChatScopeIn,
    QuestionIn,
    RegenerateIn,
    RegenerateOut,
    RetryOut,
)
from app.share_links import question_of
from app.shutdown import Stop
from app.spending import check_platform_spend, check_spend, retry_after
from app.storage import content_disposition
from app.trash import trash_chat
from app.usage import ANSWER, record

log = logging.getLogger(__name__)

router = APIRouter(tags=["chats"])

TITLE_LENGTH = 60
TOO_MANY_ANSWERS = "Too many questions are being answered right now. Wait a moment and try again."
STILL_ANSWERING = (
    "This chat is still being answered. Wait for the answer, or stop it, before asking again."
)
NOT_THE_LAST = "Only the chat's last answer can be tried again. Ask the question again instead."
NOT_THE_LAST_QUESTION = (
    "Only the chat's last question can be answered again or edited. Ask a new question instead."
)
BOTH_SCOPES = "A chat is about one folder or about chosen documents, not both. Choose one."
FINISHED = "This answer is finished, so there is nothing to try again. Ask a follow-up instead."
STILL_WRITTEN = "This answer is still being written. Download it once it is finished."
NOTHING_ASKED = "Nothing has been asked in this chat yet, so there is nothing to download."
# The statuses of an answer the member may try again: one that ended without being finished.
RETRIED = ("stopped", "failed")
# How often the events route looks for new events of an answer it is following. Each look is
# one short query from a thread; the wait between them holds no thread.
EVENT_POLL_SECONDS = 0.1
# How long the stream may carry nothing before a keep-alive comment goes out. Shorter than any
# proxy's or load balancer's idle timeout (a minute is the usual, nginx's default among them),
# so the connection outlives the model's thinking and the client never has to follow again
# for a silence alone.
SSE_KEEPALIVE_SECONDS = 15.0
SSE_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}


@router.get("/projects/{project_id}/chats", response_model=list[ChatOut])
def list_chats(project: ProjectDep, session: SessionDep, account: AccountDep):
    """The caller's own chats in the project, and the ones other members shared with it: those
    the caller pinned first, the newest pin first, then the rest by their latest question."""
    pins = and_(ChatPin.chat_id == Chat.id, ChatPin.user_id == account.user.id)
    rows = session.execute(
        select(Chat, ChatPin.pinned_at)
        .outerjoin(ChatPin, pins)
        .where(
            Chat.project_id == project.id,
            Chat.deleted_at.is_(None),
            or_(Chat.user_id == account.user.id, Chat.visibility == "project"),
        )
        # What each is about, in a query apiece for the list rather than one a chat.
        .options(selectinload(Chat.scope_folder), selectinload(Chat.scope_documents))
        .order_by(ChatPin.pinned_at.desc().nulls_last(), Chat.updated_at.desc(), Chat.id.desc())
    ).all()
    return [as_read(chat, pinned_at is not None) for chat, pinned_at in rows]


@router.post("/projects/{project_id}/chats", response_model=ChatOut, status_code=201)
def create_chat(
    project: ProjectDep, session: SessionDep, account: AccountDep, body: ChatIn | None = None
):
    """Start a chat, about the whole project or about the folder or documents `scope` names."""
    chat = Chat(project_id=project.id, user_id=account.user.id)
    if body is not None and body.scope is not None:
        _about(session, chat, body.scope)
    session.add(chat)
    session.commit()
    return as_read(chat, pinned=False)


@router.get("/chats/{chat_id}", response_model=ChatDetail)
def get_chat(chat: ChatDep, session: SessionDep, account: AccountDep):
    return as_read(chat, pinned_by(session, chat, account), ChatDetail)


@router.patch("/chats/{chat_id}", response_model=ChatOut)
def update_chat(
    chat: OwnChatDep, body: ChatPatch, session: SessionDep, account: AccountDep, request: Request
):
    """Rename the chat, share it with the project and take it private again, or change what it
    is about: its next answer reads what it is about then."""
    if body.title is not None:
        chat.title = body.title.strip()
    if body.scope is not None:
        _about(session, chat, body.scope)
    if body.visibility is not None and body.visibility != chat.visibility:
        chat.visibility = body.visibility
        # The title stays out of the row: it is the chat's first question until renamed.
        action = "chat_shared" if chat.visibility == "project" else "chat_unshared"
        audit(
            session,
            account,
            request,
            action,
            project_id=chat.project_id,
            target=("chat", chat.id),
        )
    session.commit()
    return as_read(chat, pinned_by(session, chat, account))


@router.put("/chats/{chat_id}/pin", response_model=ChatOut)
def pin_chat(chat: ChatDep, session: SessionDep, account: AccountDep):
    """Pin the chat to the top of the caller's chat list: their own, or one shared with the
    project, which each of its readers pins for themselves. A pinned chat stays where its pin
    put it."""
    session.execute(
        insert(ChatPin)
        .values(chat_id=chat.id, user_id=account.user.id)
        .on_conflict_do_nothing(index_elements=[ChatPin.chat_id, ChatPin.user_id])
    )
    session.commit()
    return as_read(chat, pinned=True)


@router.delete("/chats/{chat_id}/pin", response_model=ChatOut)
def unpin_chat(chat: ChatDep, session: SessionDep, account: AccountDep):
    """Take the chat back among the caller's others, by its latest question."""
    session.execute(
        delete(ChatPin).where(ChatPin.chat_id == chat.id, ChatPin.user_id == account.user.id)
    )
    session.commit()
    return as_read(chat, pinned=False)


def pinned_by(session: Session, chat: Chat, account: Account) -> bool:
    """Whether the account's member pinned the chat."""
    return session.get(ChatPin, (chat.id, account.user.id)) is not None


def as_read[T: ChatOut](chat: Chat, pinned: bool, schema: type[T] = ChatOut) -> T:
    """The chat as its reader sees it, pinned or not by them."""
    return schema.model_validate(chat).model_copy(update={"pinned": pinned})


@router.delete("/chats/{chat_id}", status_code=204)
def delete_chat(chat: OwnChatDep, session: SessionDep, account: AccountDep, request: Request):
    """Put the chat in its starter's trash; shared, it leaves the project's list with it."""
    audit(
        session,
        account,
        request,
        "chat_deleted",
        project_id=chat.project_id,
        target=("chat", chat.id),
    )
    trash_chat(session, chat)
    session.commit()


@router.post(
    "/chats/{chat_id}/messages",
    response_model=AskOut,
    status_code=201,
    dependencies=[Depends(limited(QUESTIONS))],
)
def ask(
    chat: OwnChatDep,
    body: QuestionIn,
    session: SessionDep,
    account: AccountDep,
    request: Request,
):
    """Add a question to the chat and queue its answer, which `events` streams."""
    question = body.content.strip()
    if not question:
        raise HTTPException(422, "Send a question to get an answer.")
    if len(question) > (cap := get_settings().max_question_chars):
        raise HTTPException(413, too_long(len(question), cap))
    if chat.messages and chat.messages[-1].status == "streaming":
        raise HTTPException(409, STILL_ANSWERING)
    # The count is taken before the question is saved, or a refused question would sit in the
    # chat with no answer.
    refuse_past_answers_in_flight(session, account, f"A question in chat {chat.id}")
    if refusal := check_question(session, account.organization):
        raise PlanRefused(refusal)
    refuse_past_spending(session, account, f"A question in chat {chat.id}")
    if not chat.title:
        chat.title = _title(question)
    chat.updated_at = func.now()
    asked, answer = start_answer(session, chat, question)
    # What the plan counts, with the question, so that deleting the chat gives none back.
    record(
        session,
        org_id=account.organization.id,
        project_id=chat.project_id,
        user_id=account.user.id,
        kind=ANSWER,
        measures={"questions": 1},
    )
    note_quota(session, account.organization, MONTH_QUESTIONS, app_url(request))
    # The request's lines name the answer its job writes, as the job's lines do.
    about(message_id=answer.id)
    session.commit()
    return AskOut(question=asked, answer=answer)


@router.get("/chats/{chat_id}/messages/{message_id}/events")
def events(chat: ChatDep, message_id: int, request: Request, session: SessionDep):
    """Follow an answer as server-sent events, from the start or from the `Last-Event-ID` the
    client saw last. The stream ends with an `end` event once the answer is finished, stopped
    or failed, and without one when the connection drops, which the client takes as a cue to
    follow again.

    The stream looks at the table with a session of its own each time, so the request's gives
    its connection back before the stream begins, not when it ends: an answer is followed for
    as long as it is written, and twenty members following answers would otherwise hold twenty
    connections and wait for more at each look (the load test, deploying.md "Load"). It has
    only read, so the commit writes nothing.
    """
    followed = answer_in(chat, message_id, session).id
    session.commit()
    return StreamingResponse(
        _follow(followed, last_seen(request), request.app.state.stop, costs_shown()),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.post("/chats/{chat_id}/messages/{message_id}/stop", response_model=ChatMessageOut)
def stop(chat: OwnChatDep, message_id: int, session: SessionDep):
    """End the answer with what arrived so far. The row is settled here, not by the worker, so
    that a stop takes effect at once whether or not a worker is alive: the worker's next
    write finds the answer settled and ends its run (app/answers.py)."""
    message = answer_in(chat, message_id, session)
    # The lock orders this with the worker's appends: one in flight lands before the fold, and
    # the ones after it find the row settled.
    session.execute(select(ChatMessage.id).where(ChatMessage.id == message.id).with_for_update())
    session.refresh(message)
    if message.status == "streaming":
        reply = fold(session, message.id)
        reply.notice = STOPPED
        settle(session, message.id, message.attempt, reply, "stopped")
        session.commit()
        session.refresh(message)
    return message


@router.post(
    "/chats/{chat_id}/messages/{message_id}/retry",
    response_model=RetryOut,
    dependencies=[Depends(limited(QUESTIONS))],
)
def retry(chat: OwnChatDep, message_id: int, session: SessionDep, account: AccountDep):
    """Write the chat's last answer again in its place, once it was stopped or failed: the row
    is emptied and streaming, and the worker writes it from the question as for a new one,
    which `events` streams.

    The plan counted the question when it was asked, and the member asked once, so a retry
    counts none; it is held to everything else that holds a question, the model's runs in
    flight and the day's spending, since each try is paid for.
    """
    message = answer_in(chat, message_id, session)
    # The lock orders this with a stop or a run settling the answer, and with the earlier try's
    # appends, so that no event of it lands after the seq this reads.
    session.execute(select(ChatMessage.id).where(ChatMessage.id == message.id).with_for_update())
    session.refresh(message)
    if message.status == "streaming":
        raise HTTPException(409, STILL_ANSWERING)
    if message.status not in RETRIED:
        raise HTTPException(409, FINISHED)
    if chat.messages[-1].id != message.id:
        raise HTTPException(409, NOT_THE_LAST)
    refuse_past_answers_in_flight(session, account, f"A question in chat {chat.id}")
    refuse_past_spending(session, account, f"A question in chat {chat.id}")
    # A try is the chat's latest activity, as a question is: it moves up the list, and its
    # retention counts from now (app/retention.py).
    chat.updated_at = func.now()
    last_event_id = retry_answer(session, chat, message)
    session.commit()
    return RetryOut(answer=message, last_event_id=last_event_id)


@router.post(
    "/chats/{chat_id}/messages/{message_id}/regenerate",
    response_model=RegenerateOut,
    dependencies=[Depends(limited(QUESTIONS))],
)
def regenerate(
    chat: OwnChatDep,
    message_id: int,
    body: RegenerateIn,
    session: SessionDep,
    account: AccountDep,
    request: Request,
):
    """Write the chat's last answer again in its place, finished or not, from its question as it
    is or as `question` edits it: the row is emptied and streaming, and the worker writes it as
    for a new question, which `events` streams.

    The member asks for another answer, so the plan counts a question, as `ask` does, and holds it
    to everything that holds one. The answer's share links show it as it is written again.
    """
    message = answer_in(chat, message_id, session)
    # The lock orders this with a run settling the answer, as `retry` takes it.
    session.execute(select(ChatMessage.id).where(ChatMessage.id == message.id).with_for_update())
    session.refresh(message)
    if message.status == "streaming":
        raise HTTPException(409, STILL_ANSWERING)
    if chat.messages[-1].id != message.id:
        raise HTTPException(409, NOT_THE_LAST_QUESTION)
    asked = question_of(session, message)
    if asked is None:
        raise HTTPException(409, NOT_THE_LAST_QUESTION)
    edited = None
    if body.question is not None:
        if not (edited := body.question.strip()):
            raise HTTPException(422, "Send a question to get an answer.")
        if len(edited) > (cap := get_settings().max_question_chars):
            raise HTTPException(413, too_long(len(edited), cap))
    refuse_past_answers_in_flight(session, account, f"A question in chat {chat.id}")
    if refusal := check_question(session, account.organization):
        raise PlanRefused(refusal)
    refuse_past_spending(session, account, f"A question in chat {chat.id}")
    if edited is not None and edited != asked.content:
        # A title the first question gave follows it; one the member chose stays.
        if chat.title == _title(asked.content) and chat.messages[0].id == asked.id:
            chat.title = _title(edited)
        asked.content = edited
    chat.updated_at = func.now()
    last_event_id = retry_answer(session, chat, message)
    record(
        session,
        org_id=account.organization.id,
        project_id=chat.project_id,
        user_id=account.user.id,
        kind=ANSWER,
        measures={"questions": 1},
    )
    note_quota(session, account.organization, MONTH_QUESTIONS, app_url(request))
    session.commit()
    return RegenerateOut(question=asked, answer=message, last_event_id=last_event_id)


@router.get(
    "/chats/{chat_id}/messages/{message_id}/export",
    response_class=Response,
    responses={200: {"content": {media: {} for media in MEDIA_TYPES.values()}}},
    dependencies=[Depends(limited(EXPORTS))],
)
def export_answer(
    chat: ChatDep,
    message_id: int,
    session: SessionDep,
    account: AccountDep,
    request: Request,
    written_as: Annotated[ExportFormat, Query(alias="format")] = "docx",
):
    """The answer with its question as a file to save (app/chat_export.py): a Word document,
    with `format=pdf` a PDF, with `format=md` its Markdown, each citation a numbered note naming
    the passage it cites. The download is recorded once the file is made, before it is sent, by
    the chat's id, the answer's and the format, never what they say."""
    message = answer_in(chat, message_id, session)
    if message.status == "streaming":
        raise HTTPException(409, STILL_WRITTEN)
    question = question_of(session, message)
    exchange = exchange_of(question.content if question else "", message)
    title = _title(exchange.question)
    asked = _asked(session, chat, account)
    # The file is made with no transaction held: a long answer's takes a moment.
    session.commit()
    try:
        content = answer_file(written_as, title, exchange, asked)
    except ExportRefused:
        raise HTTPException(422, NOT_LAID_OUT.format("answer")) from None
    audit(
        session,
        account,
        request,
        "answer_exported",
        project_id=chat.project_id,
        target=("chat", chat.id),
        detail={"answer": message.id, "format": written_as},
    )
    session.commit()
    return _file_response(content, file_name(title, written_as, "Answer"), written_as)


@router.get(
    "/chats/{chat_id}/export",
    response_class=Response,
    responses={200: {"content": {media: {} for media in MEDIA_TYPES.values()}}},
    dependencies=[Depends(limited(EXPORTS))],
)
def export_chat(
    chat: ChatDep,
    session: SessionDep,
    account: AccountDep,
    request: Request,
    written_as: Annotated[ExportFormat, Query(alias="format")] = "docx",
):
    """Every question of the chat with its answer as one file to save (app/chat_export.py), as
    `export_answer` makes an answer's, its citations numbered across the file. An answer still
    being written is in it as far as its row holds it, which is nothing yet, and says so. The
    download is recorded as an answer's is, with how many answers the file holds."""
    exchanges = exchanges_of(chat.messages)
    if not exchanges:
        raise HTTPException(409, NOTHING_ASKED)
    title = chat.title or "New chat"
    asked = _asked(session, chat, account)
    session.commit()
    try:
        content = chat_file(written_as, title, exchanges, asked)
    except ExportRefused:
        raise HTTPException(422, NOT_LAID_OUT.format("chat")) from None
    audit(
        session,
        account,
        request,
        "chat_exported",
        project_id=chat.project_id,
        target=("chat", chat.id),
        detail={"answers": len(exchanges), "format": written_as},
    )
    session.commit()
    return _file_response(content, file_name(title, written_as, "Chat"), written_as)


def _about(session: Session, chat: Chat, scope: ChatScopeIn) -> None:
    """Make the chat about what `scope` names, of its project and out of the trash: a folder,
    documents, or with neither the whole project."""
    if scope.folder_id is not None and scope.document_ids:
        raise HTTPException(422, BOTH_SCOPES)
    folder = None
    documents: list[Document] = []
    if scope.folder_id is not None:
        folder = session.scalar(
            select(Folder).where(
                Folder.id == scope.folder_id,
                Folder.project_id == chat.project_id,
                Folder.deleted_at.is_(None),
            )
        )
        if folder is None:
            raise HTTPException(404, "Folder not found")
    if scope.document_ids:
        wanted = set(scope.document_ids)
        documents = list(
            session.scalars(
                select(Document)
                .where(
                    Document.id.in_(wanted),
                    Document.project_id == chat.project_id,
                    Document.deleted_at.is_(None),
                )
                .order_by(Document.id)
            )
        )
        if len(documents) != len(wanted):
            raise HTTPException(404, "Document not found")
    chat.scope_kind = "folder" if folder else "documents" if documents else "project"
    chat.scope_folder = folder
    chat.scope_documents = documents


def _asked(session: Session, chat: Chat, account: Account) -> Asked:
    """Who asked in the chat and where, with the days in the zone of the member downloading it."""
    project = session.get(Project, chat.project_id)
    return Asked(chat.started_by, project.name, zone_of(account.user.time_zone))


def _file_response(content: bytes, name: str, written_as: ExportFormat) -> Response:
    return Response(
        content,
        media_type=MEDIA_TYPES[written_as],
        headers={"Content-Disposition": content_disposition(name)},
    )


def refuse_past_answers_in_flight(session: Session, account: Account, what: str) -> None:
    """Refuse another run of the agent while the organisation has MAX_ANSWERS in flight;
    `what` names the run refused for the log: "A question in chat 12"."""
    cap = get_settings().max_answers
    if (in_flight := answers_in_flight(session, account.organization.id)) >= cap:
        log.warning(
            "%s was refused: organisation %s has %s answers in flight, the most MAX_ANSWERS allows",
            what,
            account.organization.id,
            in_flight,
        )
        raise HTTPException(429, TOO_MANY_ANSWERS, headers={"Retry-After": str(RETRY_AFTER)})


def refuse_past_spending(session: Session, account: Account, what: str) -> None:
    """Refuse another run of the agent past the day's spending (app/spending.py): the
    platform's limit first, since no plan lifts it, as a 503 with the seconds until midnight
    UTC, when it resets; then the plan's, as a 402."""
    if reason := check_platform_spend(session):
        log.warning("%s was refused: %s", what, reason)
        raise HTTPException(503, reason, headers={"Retry-After": str(retry_after())})
    if reason := check_spend(session, account.organization):
        raise PlanRefused(Refusal("daily_spend_micros", reason))


def answer_in(chat: Chat, message_id: int, session: Session) -> ChatMessage:
    """The chat's answer with this id, or 404."""
    message = session.scalar(
        select(ChatMessage).where(ChatMessage.id == message_id, ChatMessage.chat_id == chat.id)
    )
    if message is None or message.role != "assistant":
        raise HTTPException(404, "Answer not found")
    about(message_id=message.id)
    return message


def last_seen(request: Request) -> int:
    """The seq of the last event the client saw, from the header a reconnecting client sends."""
    try:
        return int(request.headers.get("Last-Event-ID", -1))
    except ValueError:
        return -1


def poll_answer(message_id: int, after: int) -> tuple[str | None, list[tuple[int, dict]]]:
    """The answer's status, then the events past `after`: in that order, so that a status that
    says the answer is finished was read before the last events were, never after."""
    with SessionLocal() as session:
        status = session.scalar(select(ChatMessage.status).where(ChatMessage.id == message_id))
        if status is None:
            return None, []
        return status, events_after(session, message_id, after)


async def _follow(message_id: int, after: int, stop: Stop, costs: bool) -> AsyncIterator[str]:
    """Send the events as they are written until the answer is settled, and a keep-alive
    comment whenever nothing was sent for SSE_KEEPALIVE_SECONDS. A server told to stop ends
    the stream without an end event, rather than hold its stop until the answer is written
    (app/shutdown.py): the client follows again from where it was, on the next server. The
    run's `done` event says what it cost only when `costs` (app/costs.py).

    Async, so that waiting for the next event holds no request thread; each look at the table
    takes one for the length of a query.
    """
    sent_at = time.monotonic()
    while True:
        try:
            status, rows = await anyio.to_thread.run_sync(poll_answer, message_id, after)
        except PoolTimeout:
            # Every connection was in use for DB_POOL_TIMEOUT: the stream ends without an end
            # event, and the client follows again from where it was once it notices.
            log.warning(
                "Following answer %s stopped: every database connection is in use", message_id
            )
            return
        for seq, event in rows:
            if event["type"] == "done" and not costs:
                event = {**event, "usage": {**event["usage"], "cost_micros": None}}
            yield sse(seq, event)
            after = seq
        if status is None:
            # Removed with its chat while being followed.
            return
        if status != "streaming":
            yield sse(None, {"type": "end", "status": status})
            return
        if stop.requested:
            return
        if rows:
            sent_at = time.monotonic()
            continue
        if time.monotonic() - sent_at >= SSE_KEEPALIVE_SECONDS:
            yield KEEPALIVE
            sent_at = time.monotonic()
        await anyio.sleep(EVENT_POLL_SECONDS)


def too_long(length: int, cap: int) -> str:
    """The refusal of a question of `length` characters, past the `cap` one may have."""
    return (
        f"This question is {length:,} characters long, and a question can be {cap:,} at most. "
        "Shorten it, or upload the text as a document and ask about that."
    )


def _title(question: str) -> str:
    """The question on one line, cut at a word to fit the chat list."""
    line = " ".join(question.split())
    if len(line) <= TITLE_LENGTH:
        return line
    cut = line.rfind(" ", 0, TITLE_LENGTH)
    return line[: cut if cut > 0 else TITLE_LENGTH - 1] + "…"
