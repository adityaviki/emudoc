"""Chats with the agent: any number per project, each saved with its answers."""

import json
import logging
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.exc import TimeoutError as PoolTimeout

from app.agent.loop import run_agent
from app.api.deps import (
    RETRY_AFTER,
    AccountDep,
    ChatDep,
    OwnChatDep,
    ProjectDep,
    ServicesDep,
    SessionDep,
)
from app.config import get_settings
from app.db import BUSY, SessionLocal
from app.indexing.pipeline import Services
from app.models import Chat, ChatMessage, Project
from app.schemas import ChatDetail, ChatOut, ChatPatch, QuestionIn

log = logging.getLogger(__name__)

router = APIRouter(tags=["chats"])

TITLE_LENGTH = 60
TOO_MANY_ANSWERS = "Too many questions are being answered right now. Wait a moment and ask again."


class Answers:
    """The answers this process is streaming, counted so that `ask` can stop at MAX_ANSWERS.

    An answer runs on a request thread for the length of every model call, and the threads
    come from one pool shared with every other request (THREAD_HEADROOM in app/main.py says
    how it is sized), so the count is what keeps the chats from crowding out the rest.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.live = 0

    def start(self, cap: int) -> bool:
        """Count one more answer, unless the cap is reached."""
        with self._lock:
            if self.live >= cap:
                return False
            self.live += 1
            return True

    def end(self) -> None:
        with self._lock:
            self.live -= 1


answers = Answers()


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
def update_chat(chat: OwnChatDep, body: ChatPatch, session: SessionDep):
    """Rename the chat, or share it with the project and take it private again."""
    if body.title is not None:
        chat.title = body.title.strip()
    if body.visibility is not None:
        chat.visibility = body.visibility
    session.commit()
    return chat


@router.delete("/chats/{chat_id}", status_code=204)
def delete_chat(chat: OwnChatDep, session: SessionDep):
    session.delete(chat)
    session.commit()


@router.post("/chats/{chat_id}/messages")
def ask(chat: OwnChatDep, body: QuestionIn, session: SessionDep, services: ServicesDep):
    """Add a question to the chat and answer it, streamed as server-sent events."""
    question = body.content.strip()
    if not question:
        raise HTTPException(422, "Send a question to get an answer.")
    # The place in the count is taken before the question is saved, or a refused question
    # would sit in the chat with no answer.
    if not answers.start(get_settings().max_answers):
        log.warning(
            "A question in chat %s was refused: this process is answering %s already, "
            "the most MAX_ANSWERS allows",
            chat.id,
            answers.live,
        )
        raise HTTPException(429, TOO_MANY_ANSWERS, headers={"Retry-After": str(RETRY_AFTER)})
    try:
        if not chat.title:
            chat.title = _title(question)
        chat.updated_at = func.now()
        session.add(ChatMessage(chat_id=chat.id, role="user", content=question))
        session.commit()
    except BaseException:
        answers.end()
        raise
    return StreamingResponse(
        _counted(stream_answer(chat.id, services)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _counted(events: Iterator[str]) -> Iterator[str]:
    """Give the answer's place in the count back when its events end, however they end."""
    try:
        yield from events
    finally:
        answers.end()


def stream_answer(chat_id: int, services: Services) -> Iterator[str]:
    """Answer the chat's last question, saving the answer when it ends or the client stops it."""
    reply = _Reply()
    try:
        # The request's session closes when the endpoint returns; the stream outlives it.
        with SessionLocal() as session:
            chat = session.get(Chat, chat_id)
            project = session.get(Project, chat.project_id)
            # Answers without text, such as failed ones, are left out of the conversation.
            history = [
                {"role": message.role, "content": message.content}
                for message in chat.messages
                if message.content.strip()
            ]
            for event in run_agent(session, services, project, history):
                reply.apply(event)
                yield _sse(event)
    except GeneratorExit:
        # The client stopped the answer: the generator is closed where it last yielded.
        reply.notice = "Stopped."
        raise
    except PoolTimeout:
        # Every database connection was in use when a tool needed one (a request is refused
        # the same way, `pool_exhausted` in app/main.py): the answer ends with the sentence
        # that says to try again, and the operator sees the pool, not a traceback.
        log.warning("Answering in chat %s stopped: every database connection is in use", chat_id)
        reply.error = BUSY
        yield _sse({"type": "error", "message": BUSY})
    except Exception:
        log.exception("Answering in chat %s failed", chat_id)
        reply.error = "The answer failed unexpectedly."
        yield _sse({"type": "error", "message": reply.error})
    finally:
        reply.save(chat_id)


def _title(question: str) -> str:
    """The question on one line, cut at a word to fit the chat list."""
    line = " ".join(question.split())
    if len(line) <= TITLE_LENGTH:
        return line
    cut = line.rfind(" ", 0, TITLE_LENGTH)
    return line[: cut if cut > 0 else TITLE_LENGTH - 1] + "…"


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


@dataclass
class _Reply:
    """The answer as the client assembles it from the events (applyEvent in the frontend)."""

    content: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)
    sources: dict[int, dict[str, Any]] = field(default_factory=dict)
    notice: str | None = None
    error: str | None = None
    # Length of `content` when the current model turn began, to discard a turn that is retried.
    turn_start: int = 0

    def apply(self, event: dict[str, Any]) -> None:
        match event["type"]:
            case "text":
                self.content += event["text"]
            case "retry":
                self.content = self.content[: self.turn_start]
            case "tool_call":
                self.steps.append(
                    {
                        "id": event["id"],
                        "name": event["name"],
                        "input": event["input"],
                        "summary": "",
                        "is_error": False,
                    }
                )
            case "tool_result":
                self.turn_start = len(self.content)
                for step in self.steps:
                    if step["id"] == event["id"]:
                        step.update(summary=event["summary"], is_error=event["is_error"])
            case "sources":
                self.sources.update((source["id"], source) for source in event["sources"])
            case "notice":
                self.notice = event["message"]
            case "error":
                self.error = event["message"]

    def save(self, chat_id: int) -> None:
        try:
            with SessionLocal() as session:
                # The chat may have been deleted while the answer was being written.
                if session.get(Chat, chat_id) is not None:
                    session.add(
                        ChatMessage(
                            chat_id=chat_id,
                            role="assistant",
                            content=self.content,
                            steps=self.steps,
                            sources=list(self.sources.values()),
                            notice=self.notice,
                            error=self.error,
                        )
                    )
                    session.commit()
        except PoolTimeout:
            # The pool that stopped the answer is still full. The user has the answer as far as
            # it got, on screen; the chat keeps the question alone.
            log.warning(
                "The answer in chat %s was not saved: every database connection is in use",
                chat_id,
            )
