"""Chats with the agent: any number per project, each saved with its answers."""

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select

from app.agent.loop import run_agent
from app.api.deps import AccountDep, ChatDep, OwnChatDep, ProjectDep, ServicesDep, SessionDep
from app.db import SessionLocal
from app.indexing.pipeline import Services
from app.models import Chat, ChatMessage, Project
from app.schemas import ChatDetail, ChatOut, ChatPatch, QuestionIn

log = logging.getLogger(__name__)

router = APIRouter(tags=["chats"])

TITLE_LENGTH = 60


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
    if not chat.title:
        chat.title = _title(question)
    chat.updated_at = func.now()
    session.add(ChatMessage(chat_id=chat.id, role="user", content=question))
    session.commit()
    return StreamingResponse(
        stream_answer(chat.id, services),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
