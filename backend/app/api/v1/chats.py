"""The versioned API's chats (app/api/v1/): a program starts a chat, asks in it with an
Idempotency-Key, and has the answer, which the worker writes (app/answers.py), by polling the
message until its status is no longer `streaming` or by following its events.

The events are the API's own, not the page's: the page's stream carries the agent's steps, its
retried turns and its restarts, which add up to the answer only as `Reply.apply` folds them, and
which change with the agent. This one folds them as they come and sends what the answer became:
`text`, more of it; `reset`, the answer so far in place of what was sent, when a turn was tried
again or the run started over; `sources`, passages it may cite now; and `end`, the message as it
was settled. Each carries the id of the last of the page's events folded into it, so a program
that lost the stream follows again from where it was with `Last-Event-ID`, and the stream folds
the events up to there before it sends anything.
"""

import json
import logging
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import or_, select
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.orm import Session

from app.answers import KEEPALIVE, Reply, events_after, fold
from app.api import chats
from app.api.deps import AccountDep, ChatDep, OwnChatDep, ProjectDep, SessionDep, limited
from app.api.v1.deps import PAGE, After, Limit, OnceDep, cursor, cursor_of, shown
from app.api.v1.schemas import (
    Asked,
    Chat,
    ChatChange,
    ChatList,
    Message,
    MessageList,
    Question,
    Source,
)
from app.db import SessionLocal
from app.idempotency import fingerprint
from app.models import Chat as ChatRow
from app.models import ChatMessage
from app.ratelimit import QUESTIONS
from app.schemas import ChatPatch, QuestionIn
from app.shutdown import Stop

log = logging.getLogger(__name__)

router = APIRouter(tags=["chats"])

NO_MESSAGE = "Message not found"


@router.get("/projects/{project_id}/chats", response_model=ChatList)
def list_chats(
    project: ProjectDep,
    session: SessionDep,
    account: AccountDep,
    limit: Limit = PAGE,
    after: After = None,
):
    """The chats of the project the key's member started, and those shared with it, the newest
    first."""
    start = cursor(after, int)
    filters = [
        ChatRow.project_id == project.id,
        ChatRow.deleted_at.is_(None),
        or_(ChatRow.user_id == account.user.id, ChatRow.visibility == "project"),
    ]
    if start:
        filters.append(ChatRow.id < start[0])
    query = select(ChatRow).where(*filters).order_by(ChatRow.id.desc()).limit(limit + 1)
    rows, more = shown(session.scalars(query).all(), limit)
    return ChatList(
        data=[chat_of(chat) for chat in rows], next=cursor_of(rows[-1].id) if more else None
    )


@router.post("/projects/{project_id}/chats", response_model=Chat, status_code=201)
def create_chat(
    project: ProjectDep, request: Request, session: SessionDep, account: AccountDep, once: OnceDep
):
    """Start a chat, private to the key's member; a viewer may, since asking is reading."""
    return once.run(
        lambda: fingerprint(request, None),
        201,
        lambda: chat_of(chats.create_chat(project, session, account)),
    )


@router.get("/chats/{chat_id}", response_model=Chat)
def get_chat(chat: ChatDep):
    return chat_of(chat)


@router.patch("/chats/{chat_id}", response_model=Chat)
def update_chat(
    chat: OwnChatDep, body: ChatChange, session: SessionDep, account: AccountDep, request: Request
):
    """Rename the chat, share it with the project, or take it private again."""
    changed = ChatPatch(**body.model_dump())
    return chat_of(chats.update_chat(chat, changed, session, account, request))


@router.delete("/chats/{chat_id}", status_code=204)
def delete_chat(chat: OwnChatDep, session: SessionDep, account: AccountDep, request: Request):
    """Put the chat in its starter's trash."""
    chats.delete_chat(chat, session, account, request)


@router.get("/chats/{chat_id}/messages", response_model=MessageList)
def list_messages(chat: ChatDep, session: SessionDep, limit: Limit = PAGE, after: After = None):
    """The chat's questions and answers, the oldest first."""
    start = cursor(after, int)
    query = select(ChatMessage).where(ChatMessage.chat_id == chat.id)
    if start:
        query = query.where(ChatMessage.id > start[0])
    rows, more = shown(
        session.scalars(query.order_by(ChatMessage.id).limit(limit + 1)).all(), limit
    )
    return MessageList(
        data=[message_of(session, message) for message in rows],
        next=cursor_of(rows[-1].id) if more else None,
    )


@router.post(
    "/chats/{chat_id}/messages",
    response_model=Asked,
    status_code=201,
    dependencies=[Depends(limited(QUESTIONS))],
)
def ask(
    chat: OwnChatDep,
    body: Question,
    request: Request,
    session: SessionDep,
    account: AccountDep,
    once: OnceDep,
):
    """Ask a question in a chat the key's member started; the worker writes the answer, which
    is followed by its id. It counts as one of the month's questions (402 past the plan's), and
    is refused with 409 while the chat's last answer is still being written and with 429 while
    the organisation has too many being written at once. With an Idempotency-Key, the question
    sent again is given the first one's answer, and asked and counted once."""

    def work() -> Asked:
        asked = chats.ask(chat, QuestionIn(content=body.content), session, account, request)
        return Asked(
            question=message_of(session, session.get(ChatMessage, asked.question.id)),
            answer=message_of(session, session.get(ChatMessage, asked.answer.id)),
        )

    return once.run(lambda: fingerprint(request, body.model_dump(mode="json")), 201, work)


@router.get("/chats/{chat_id}/messages/{message_id}", response_model=Message)
def get_message(chat: ChatDep, message_id: int, session: SessionDep):
    """A question or an answer of the chat: while an answer is `streaming`, what it says so
    far."""
    message = session.scalar(
        select(ChatMessage).where(ChatMessage.id == message_id, ChatMessage.chat_id == chat.id)
    )
    if message is None:
        raise HTTPException(404, NO_MESSAGE)
    return message_of(session, message)


@router.get(
    "/chats/{chat_id}/messages/{message_id}/events",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": (
                "Server-sent events: `text` ({text}) adds to the answer, `reset` ({content, "
                "sources}) replaces it, `sources` ({sources}) adds passages it may cite, and "
                "`end` (a Message) ends the stream once it is settled. A stream that ends "
                "without `end` is followed again with the `Last-Event-ID` of its last event."
            ),
        }
    },
)
def events(chat: ChatDep, message_id: int, request: Request, session: SessionDep):
    """Follow an answer as it is written, from its start or from the `Last-Event-ID` seen
    last.
    \f
    The request's session gives its connection back before the stream begins, as the page's
    does (app/api/chats.py); FastAPI leaves what follows the form feed out of the contract."""
    followed = chats.answer_in(chat, message_id, session).id
    session.commit()
    return StreamingResponse(
        _follow(chat.id, followed, chats.last_seen(request), request.app.state.stop),
        media_type="text/event-stream",
        headers=chats.SSE_HEADERS,
    )


@router.post("/chats/{chat_id}/messages/{message_id}/stop", response_model=Message)
def stop(chat: OwnChatDep, message_id: int, session: SessionDep):
    """End the answer with what it says so far."""
    return message_of(session, chats.stop(chat, message_id, session))


def chat_of(chat: ChatRow) -> Chat:
    return Chat(
        id=chat.id,
        project_id=chat.project_id,
        user_id=chat.user_id,
        title=chat.title,
        visibility=chat.visibility,
        created_at=chat.created_at,
        updated_at=chat.updated_at,
    )


def message_of(session: Session, message: ChatMessage) -> Message:
    """The message as the API words it: an answer still being written as its events add up so
    far, a settled one as its row holds it."""
    content, sources, notice, error = (
        message.content,
        message.sources,
        message.notice,
        message.error,
    )
    if message.status == "streaming":
        reply = fold(session, message.id)
        content, sources = reply.content, list(reply.sources.values())
        notice, error = reply.notice, reply.error
    return Message(
        id=message.id,
        chat_id=message.chat_id,
        role=message.role,
        content=content,
        status=message.status,
        sources=[source_of(source) for source in sources],
        notice=notice,
        error=error,
        created_at=message.created_at,
    )


def source_of(source: dict[str, Any]) -> Source:
    """A passage as the agent was shown it (`SourceOut` in app/schemas.py), by its id."""
    return Source(
        passage_id=source["id"],
        document_id=source["document_id"],
        document_title=source["document_title"],
        page_start=source.get("page_start"),
        page_end=source.get("page_end"),
        excerpt=source.get("excerpt", ""),
    )


class _Sent:
    """What a follower holds of the answer, by the events sent to it."""

    def __init__(self, reply: Reply) -> None:
        self.content = reply.content
        self.sources = set(reply.sources)

    def changes(self, reply: Reply) -> Iterator[tuple[str, dict[str, Any]]]:
        """The events that bring the follower to what `reply` holds now."""
        if self.sources - set(reply.sources) or not reply.content.startswith(self.content):
            yield (
                "reset",
                {
                    "content": reply.content,
                    "sources": [_dumped(source) for source in reply.sources.values()],
                },
            )
        else:
            if more := reply.content[len(self.content) :]:
                yield "text", {"text": more}
            if added := [s for key, s in reply.sources.items() if key not in self.sources]:
                yield "sources", {"sources": [_dumped(source) for source in added]}
        self.content = reply.content
        self.sources = set(reply.sources)


def _dumped(source: dict[str, Any]) -> dict[str, Any]:
    return source_of(source).model_dump(mode="json")


def _event(seq: int, name: str, data: dict[str, Any]) -> str:
    return f"id: {seq}\nevent: {name}\ndata: {json.dumps(data, default=str)}\n\n"


def _folded_through(message_id: int, through: int) -> Reply:
    """The answer as a follower saw it, its events up to the seq `through` folded."""
    reply = Reply()
    if through < 0:
        return reply
    with SessionLocal() as session:
        for seq, event in events_after(session, message_id, -1):
            if seq > through:
                break
            reply.apply(event)
    return reply


def _settled(chat_id: int, message_id: int) -> dict[str, Any] | None:
    with SessionLocal() as session:
        message = session.get(ChatMessage, message_id)
        if message is None or message.chat_id != chat_id:
            return None
        return message_of(session, message).model_dump(mode="json")


async def _follow(chat_id: int, message_id: int, after: int, stop: Stop) -> AsyncIterator[str]:
    """Send what the answer becomes as its events are written, until it is settled, and a
    keep-alive comment whenever nothing was sent for SSE_KEEPALIVE_SECONDS; a server told to stop
    ends the stream without `end`, as the page's does (`_follow` in app/api/chats.py)."""
    reply = await anyio.to_thread.run_sync(_folded_through, message_id, after)
    sent = _Sent(reply)
    sent_at = time.monotonic()
    while True:
        try:
            status, rows = await anyio.to_thread.run_sync(chats.poll_answer, message_id, after)
        except PoolTimeout:
            log.warning(
                "Following answer %s stopped: every database connection is in use", message_id
            )
            return
        for seq, event in rows:
            reply.apply(event)
            after = seq
        for name, data in sent.changes(reply):
            yield _event(after, name, data)
        if status is None:
            return
        if status != "streaming":
            if (
                message := await anyio.to_thread.run_sync(_settled, chat_id, message_id)
            ) is not None:
                yield _event(after, "end", message)
            return
        if stop.requested:
            return
        if rows:
            sent_at = time.monotonic()
            continue
        if time.monotonic() - sent_at >= chats.SSE_KEEPALIVE_SECONDS:
            yield KEEPALIVE
            sent_at = time.monotonic()
        await anyio.sleep(chats.EVENT_POLL_SECONDS)
