"""Chats with the agent: any number per project, each saved with its answers.

A question is answered by the worker, not in the request (app/answers.py): `ask` saves the
question and an empty answer, queues the job and returns both at once; the client then follows
the answer's events through `events`, a server-sent event stream read from the table, and can
pick it up again from the last event it saw after a reload or a dropped connection. `stop`
ends an answer early with what arrived so far.
"""

import logging
import time
from collections.abc import AsyncIterator

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.orm import Session

from app.answers import (
    KEEPALIVE,
    STOPPED,
    answers_in_flight,
    events_after,
    fold,
    settle,
    sse,
    start_answer,
)
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
from app.config import get_settings
from app.db import SessionLocal
from app.entitlements import check_question
from app.models import Chat, ChatMessage
from app.ratelimit import QUESTIONS
from app.schemas import AskOut, ChatDetail, ChatMessageOut, ChatOut, ChatPatch, QuestionIn
from app.spending import check_platform_spend, check_spend, retry_after

log = logging.getLogger(__name__)

router = APIRouter(tags=["chats"])

TITLE_LENGTH = 60
TOO_MANY_ANSWERS = "Too many questions are being answered right now. Wait a moment and ask again."
STILL_ANSWERING = (
    "This chat is still being answered. Wait for the answer, or stop it, before asking again."
)
# How often the events route looks for new events of an answer it is following. Each look is
# one short query from a thread; the wait between them holds no thread.
EVENT_POLL_SECONDS = 0.1
# How long the stream may carry nothing before a keep-alive comment goes out. Shorter than any
# proxy's or load balancer's idle timeout (a minute is the usual, nginx's default among them),
# so the connection outlives the model's thinking and the client never has to follow again
# for a silence alone.
SSE_KEEPALIVE_SECONDS = 15.0
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@router.get("/projects/{project_id}/chats", response_model=list[ChatOut])
def list_chats(project: ProjectDep, session: SessionDep, account: AccountDep):
    """The caller's own chats in the project, and the ones other members shared with it."""
    return session.scalars(
        select(Chat)
        .where(
            Chat.project_id == project.id,
            or_(Chat.user_id == account.user.id, Chat.visibility == "project"),
        )
        .order_by(Chat.updated_at.desc(), Chat.id.desc())
    ).all()


@router.post("/projects/{project_id}/chats", response_model=ChatOut, status_code=201)
def create_chat(project: ProjectDep, session: SessionDep, account: AccountDep):
    chat = Chat(project_id=project.id, user_id=account.user.id)
    session.add(chat)
    session.commit()
    return chat


@router.get("/chats/{chat_id}", response_model=ChatDetail)
def get_chat(chat: ChatDep):
    return chat


@router.patch("/chats/{chat_id}", response_model=ChatOut)
def update_chat(
    chat: OwnChatDep, body: ChatPatch, session: SessionDep, account: AccountDep, request: Request
):
    """Rename the chat, or share it with the project and take it private again."""
    if body.title is not None:
        chat.title = body.title.strip()
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
    return chat


@router.delete("/chats/{chat_id}", status_code=204)
def delete_chat(chat: OwnChatDep, session: SessionDep, account: AccountDep, request: Request):
    audit(
        session,
        account,
        request,
        "chat_deleted",
        project_id=chat.project_id,
        target=("chat", chat.id),
    )
    session.delete(chat)
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
    cap = get_settings().max_answers
    if (in_flight := answers_in_flight(session, account.organization.id)) >= cap:
        log.warning(
            "A question in chat %s was refused: organisation %s has %s answers in flight, "
            "the most MAX_ANSWERS allows",
            chat.id,
            account.organization.id,
            in_flight,
        )
        raise HTTPException(429, TOO_MANY_ANSWERS, headers={"Retry-After": str(RETRY_AFTER)})
    if reason := check_question(session, account.organization):
        raise HTTPException(402, reason)
    # The day's spending (app/spending.py): the platform's limit first, since no plan lifts it,
    # as a 503 with the seconds until midnight UTC, when it resets; then the plan's, as a 402.
    if reason := check_platform_spend(session):
        log.warning("A question in chat %s was refused: %s", chat.id, reason)
        raise HTTPException(503, reason, headers={"Retry-After": str(retry_after())})
    if reason := check_spend(session, account.organization):
        raise HTTPException(402, reason)
    if not chat.title:
        chat.title = _title(question)
    chat.updated_at = func.now()
    asked, answer = start_answer(session, chat, question)
    session.commit()
    return AskOut(question=asked, answer=answer)


@router.get("/chats/{chat_id}/messages/{message_id}/events")
def events(chat: ChatDep, message_id: int, request: Request, session: SessionDep):
    """Follow an answer as server-sent events, from the start or from the `Last-Event-ID` the
    client saw last. The stream ends with an `end` event once the answer is finished, stopped
    or failed, and without one when the connection drops, which the client takes as a cue to
    follow again."""
    message = _message_in(chat, message_id, session)
    return StreamingResponse(
        _follow(message.id, _last_seen(request)),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.post("/chats/{chat_id}/messages/{message_id}/stop", response_model=ChatMessageOut)
def stop(chat: OwnChatDep, message_id: int, session: SessionDep):
    """End the answer with what arrived so far. The row is settled here, not by the worker, so
    that a stop takes effect at once whether or not a worker is alive: the worker's next
    write finds the answer settled and ends its run (app/answers.py)."""
    message = _message_in(chat, message_id, session)
    # The lock orders this with the worker's appends: one in flight lands before the fold, and
    # the ones after it find the row settled.
    session.execute(select(ChatMessage.id).where(ChatMessage.id == message.id).with_for_update())
    session.refresh(message)
    if message.status == "streaming":
        reply = fold(session, message.id)
        reply.notice = STOPPED
        settle(session, message.id, reply, "stopped")
        session.commit()
        session.refresh(message)
    return message


def _message_in(chat: Chat, message_id: int, session: Session) -> ChatMessage:
    message = session.scalar(
        select(ChatMessage).where(ChatMessage.id == message_id, ChatMessage.chat_id == chat.id)
    )
    if message is None or message.role != "assistant":
        raise HTTPException(404, "Answer not found")
    return message


def _last_seen(request: Request) -> int:
    """The seq of the last event the client saw, from the header a reconnecting client sends."""
    try:
        return int(request.headers.get("Last-Event-ID", -1))
    except ValueError:
        return -1


def _poll(message_id: int, after: int) -> tuple[str | None, list[tuple[int, dict]]]:
    """The answer's status, then the events past `after`: in that order, so that a status that
    says the answer is finished was read before the last events were, never after."""
    with SessionLocal() as session:
        status = session.scalar(select(ChatMessage.status).where(ChatMessage.id == message_id))
        if status is None:
            return None, []
        return status, events_after(session, message_id, after)


async def _follow(message_id: int, after: int) -> AsyncIterator[str]:
    """Send the events as they are written until the answer is settled, and a keep-alive
    comment whenever nothing was sent for SSE_KEEPALIVE_SECONDS.

    Async, so that waiting for the next event holds no request thread; each look at the table
    takes one for the length of a query.
    """
    sent_at = time.monotonic()
    while True:
        try:
            status, rows = await anyio.to_thread.run_sync(_poll, message_id, after)
        except PoolTimeout:
            # Every connection was in use for DB_POOL_TIMEOUT: the stream ends without an end
            # event, and the client follows again from where it was once it notices.
            log.warning(
                "Following answer %s stopped: every database connection is in use", message_id
            )
            return
        for seq, event in rows:
            yield sse(seq, event)
            after = seq
        if status is None:
            # Removed with its chat while being followed.
            return
        if status != "streaming":
            yield sse(None, {"type": "end", "status": status})
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
