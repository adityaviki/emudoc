"""Answers as jobs: the worker runs the agent and writes its events to a table, the API relays
them, and an answer outlives the request that asked for it.

A question is saved with an empty answer whose status is `streaming` and a job that names the
answer (`start_answer`). An answerer thread of the worker (app/worker.py) claims the job and
runs the agent (`answer`): each event the loop yields is appended to `chat_events` under the
job's lease, text deltas gathered into one event every TEXT_FLUSH_SECONDS, and when the loop
ends the row gets the content, steps and sources the events add up to and its final status.
The API's events route (app/api/chats.py) sends the rows past the seq a client last saw and
polls for more until the status is no longer `streaming`, so a reload, another tab or a new
API replica follows the same answer from the table, and no request thread waits on a model.

Stopping is the row's doing, not the worker's: the stop route folds the events so far into the
row (`fold`) and marks it `stopped`, which the next append notices and ends the run on. That
way a stop works at once whether or not a worker is alive. A run that was lost (its worker
died, or was stopped and handed the job back) starts the answer over on the next claim: it
appends a `restart` event first, so a follower drops what it had, and a run lost `max_attempts`
times ends the answer `failed` with a sentence that says to ask again (`abandon_answer`).

Whatever ends a run, the model calls it made are recorded as the organisation's usage
(app/usage.py) before it returns: the loop counts them as they are answered, and the run
records the count at its end, after a stop or a lost lease as much as after the answer.
"""

import json
import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import Connection, bindparam, func, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.orm import Session

from app.agent.loop import run_agent
from app.config import get_settings
from app.db import BUSY, SessionLocal
from app.indexing.pipeline import Services
from app.jobs import ANSWER_QUESTION, LIVE, Lease, LeaseLost, enqueue, renew
from app.models import Chat, ChatMessage, Job, Project
from app.usage import ANSWER, CHAT_MESSAGE, ModelUsage, record

log = logging.getLogger(__name__)

STOPPED = "Stopped."
FAILED = "The answer failed unexpectedly."
# How often the text the model is streaming is written out while it keeps coming: each write is
# a commit, and a token at a time would be hundreds of them per answer for nothing a reader can
# see. The first text after a pause goes out at once.
TEXT_FLUSH_SECONDS = 0.1
# How long the events of a finished answer are kept. The row holds the answer once it is
# finished; the events only serve a follower that is still catching up.
EVENT_HISTORY = timedelta(days=1)

# A new event takes its place only while the answer is still being written: a row that was
# stopped, or removed with its chat, takes none, and the run that tried finds out so from the
# count. The lock on the row orders the append with a stop that is folding the events (`stop`
# in app/api/chats.py), so that neither misses the other's write.
_APPEND = text(
    """
    WITH answer AS (
        SELECT id FROM chat_messages WHERE id = :id AND status = 'streaming' FOR UPDATE
    )
    INSERT INTO chat_events (message_id, seq, event)
    SELECT id, :seq, :event FROM answer
    """
).bindparams(bindparam("event", type_=JSONB))
_SETTLE = text(
    """
    UPDATE chat_messages
    SET content = :content, steps = :steps, sources = :sources, notice = :notice,
        error = :error, status = :status
    WHERE id = :id AND status = 'streaming'
    """
).bindparams(bindparam("steps", type_=JSONB), bindparam("sources", type_=JSONB))
_EVENTS = text(
    "SELECT seq, event FROM chat_events WHERE message_id = :id AND seq > :after ORDER BY seq"
)
_NEXT_SEQ = text("SELECT coalesce(max(seq) + 1, 0) FROM chat_events WHERE message_id = :id")
_PRUNE = text(
    """
    DELETE FROM chat_events USING chat_messages
    WHERE chat_messages.id = chat_events.message_id
      AND chat_messages.status <> 'streaming' AND chat_messages.created_at < now() - :age
    """
)


@dataclass
class Reply:
    """The answer as the events add up to it: the server's copy of `applyEvent` in
    frontend/src/chat.ts, for the row that holds the answer once it is finished."""

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
            case "restart":
                # The run that wrote the events so far was lost; this one starts over.
                self.content, self.steps, self.sources = "", [], {}
                self.notice = self.error = None
                self.turn_start = 0
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

    def columns(self, status: str) -> dict[str, Any]:
        """The row's values for this answer, ending with `status`."""
        return {
            "content": self.content,
            "steps": self.steps,
            "sources": list(self.sources.values()),
            "notice": self.notice,
            "error": self.error,
            "status": status,
        }


def start_answer(session: Session, chat: Chat, question: str) -> tuple[ChatMessage, ChatMessage]:
    """Add the question and its empty answer to the chat and queue the job that writes the
    answer; all three are the caller's to commit together."""
    asked = ChatMessage(chat_id=chat.id, role="user", content=question)
    answer = ChatMessage(chat_id=chat.id, role="assistant", content="", status="streaming")
    session.add_all([asked, answer])
    session.flush()
    project = session.get(Project, chat.project_id)
    enqueue(
        session,
        ANSWER_QUESTION,
        org_id=project.org_id,
        project_id=project.id,
        payload={"message_id": answer.id},
        priority=LIVE,
    )
    return asked, answer


def answers_in_flight(session: Session, org_id: int) -> int:
    """How many answers the organisation has queued or being written."""
    return session.scalar(
        select(func.count())
        .select_from(ChatMessage)
        .join(Chat, Chat.id == ChatMessage.chat_id)
        .join(Project, Project.id == Chat.project_id)
        .where(Project.org_id == org_id, ChatMessage.status == "streaming")
    )


def events_after(session: Session, message_id: int, after: int) -> list[tuple[int, dict]]:
    """The answer's events with a seq past `after`, in order."""
    return [
        (seq, event) for seq, event in session.execute(_EVENTS, {"id": message_id, "after": after})
    ]


def fold(session: Session, message_id: int) -> Reply:
    """The answer as its events so far add up to."""
    reply = Reply()
    for _, event in events_after(session, message_id, -1):
        reply.apply(event)
    return reply


def settle(session: Session, message_id: int, reply: Reply, status: str) -> bool:
    """Write the answer into its row with its final status, in the caller's transaction.

    False when the row is no longer streaming: it was stopped or removed meanwhile, and what
    it holds stands.
    """
    return session.execute(_SETTLE, {"id": message_id, **reply.columns(status)}).rowcount == 1


def prune_events(connection: Connection, age: timedelta = EVENT_HISTORY) -> int:
    """Remove the events of answers that finished and were asked longer ago than `age`;
    returns how many."""
    return connection.execute(_PRUNE, {"age": age}).rowcount


class _Settled(Exception):
    """The answer was stopped by the user or removed with its chat while it was being
    written: the run ends, and the row stands as it is."""


class _Writer:
    """Appends an answer's events as the agent yields them, one commit each under the lease."""

    def __init__(self, session: Session, lease: Lease | None, message_id: int):
        self._session = session
        self._lease = lease
        self.message_id = message_id
        self.seq = session.scalar(_NEXT_SEQ, {"id": message_id})
        self._text = ""
        self._flushed_at = time.monotonic()

    @property
    def resumed(self) -> bool:
        """Whether a run before this one wrote events: this run starts the answer over."""
        return self.seq > 0

    def emit(self, event: dict[str, Any]) -> None:
        if event["type"] != "text":
            self.flush()
            self._append(event)
            return
        self._text += event["text"]
        if time.monotonic() - self._flushed_at >= TEXT_FLUSH_SECONDS:
            self.flush()

    def flush(self) -> None:
        """Write out the text gathered since the last event."""
        if self._text:
            pending, self._text = self._text, ""
            self._append({"type": "text", "text": pending})

    def _append(self, event: dict[str, Any]) -> None:
        params = {"id": self.message_id, "seq": self.seq, "event": event}
        if self._session.execute(_APPEND, params).rowcount == 0:
            self._session.rollback()
            raise _Settled
        if self._lease is not None and not renew(self._session, self._lease):
            self._session.rollback()
            raise LeaseLost(self._lease)
        self._session.commit()
        self.seq += 1
        self._flushed_at = time.monotonic()


def answer(message_id: int, services: Services, lease: Lease | None = None) -> None:
    """Write the answer to the question before this message, event by event, and settle it.

    `lease` is the job's hold on the answer (app/jobs.py): every commit renews it, and one that
    finds the job no longer this worker's ends the run with `LeaseLost`, with the answer the
    next worker's to start over. Without a lease the run is unguarded, as the tests run it.
    """
    with SessionLocal() as session:
        message = session.get(ChatMessage, message_id)
        if message is None or message.status != "streaming":
            # Removed with its chat, or stopped before any worker got to it.
            return
        chat = session.get(Chat, message.chat_id)
        project = session.get(Project, chat.project_id)
        project_id = project.id
        # Whose usage the run's model calls are, read now: the rollback below expires the
        # rows, and reading one afterwards would take a connection back for the whole run.
        payer = {"org_id": project.org_id, "project_id": project_id, "user_id": chat.user_id}
        # Answers without text, such as failed ones, are left out of the conversation.
        history = [
            {"role": earlier.role, "content": earlier.content}
            for earlier in chat.messages
            if earlier.id < message.id and earlier.content.strip()
        ]
        writer = _Writer(session, lease, message_id)
        # The reads are done, and the agent loop opens sessions of its own per tool call
        # (app/agent/loop.py), so from here this session is the writer's alone: its connection
        # goes back now and is taken for each event it writes, never held while the model
        # thinks.
        session.rollback()
        usage = ModelUsage()
        try:
            _write(session, writer, run_agent(services, project_id, history, usage), lease)
        finally:
            # The calls the run made were made whatever ended it: a stop, a lost lease and a
            # crash all leave the run through here, and are counted like an answer.
            _charge(session, usage, message_id, payer)


def _write(session: Session, writer: _Writer, events: Iterator[dict], lease: Lease | None) -> None:
    """Append the events to the answer as they come, and settle its row when they end."""
    reply = Reply()
    message_id = writer.message_id
    try:
        if writer.resumed:
            writer.emit({"type": "restart"})
        for event in events:
            reply.apply(event)
            writer.emit(event)
        writer.flush()
        status = "failed" if reply.error else "done"
    except _Settled:
        log.info("Answer %s was stopped or removed while it was being written", message_id)
        return
    except PoolTimeout:
        # Every database connection was in use when a tool or an append needed one (a
        # request is refused the same way, `pool_exhausted` in app/main.py): the answer
        # ends with the sentence that says to try again, and the operator sees the pool,
        # not a traceback.
        log.warning("Answer %s stopped: every database connection is in use", message_id)
        session.rollback()
        reply.error = BUSY
        try:
            writer.emit({"type": "error", "message": BUSY})
        except PoolTimeout:
            pass
        status = "failed"
    if lease is not None and not renew(session, lease):
        session.rollback()
        raise LeaseLost(lease)
    settle(session, message_id, reply, status)
    session.commit()


def _charge(session: Session, usage: ModelUsage, message_id: int, payer: dict[str, int]) -> None:
    """Record the run's model calls as the usage of `payer` (its organisation, project and
    user), in a transaction of its own: the run's last one is committed, rolled back or
    broken by now, whichever way the run ended."""
    if not usage.calls:
        return
    session.rollback()
    try:
        record(
            session,
            **payer,
            kind=ANSWER,
            measures=usage.measures,
            model=get_settings().agent_model,
            ref=(CHAT_MESSAGE, message_id),
        )
        session.commit()
    except PoolTimeout:
        log.warning(
            "The usage of answer %s was not recorded: every database connection is in use",
            message_id,
        )


def run_answer(job: Job, services: Services) -> str | None:
    """The worker's handler: write the answer the job names, and if the run itself breaks,
    leave the answer failed with a sentence before the job fails with the traceback."""
    message_id = job.payload["message_id"]
    try:
        answer(message_id, services, Lease(job.id, job.worker_id))
    except LeaseLost:
        # The answer is another worker's now, which starts it over.
        raise
    except Exception as error:
        if isinstance(error, PoolTimeout):
            log.warning("Answer %s failed: every database connection is in use", message_id)
        else:
            log.exception("Answer %s failed", message_id)
        try:
            fail_answer(message_id, BUSY if isinstance(error, PoolTimeout) else FAILED)
        except PoolTimeout:
            log.warning("Answer %s could not be marked failed either; stop it by hand", message_id)
        raise
    return None


def abandon_answer(job: Job, services: Services) -> None:
    """The job was lost `max_attempts` times to a dying process: the answer ends failed with
    what arrived and a sentence that says to ask again."""
    fail_answer(
        job.payload["message_id"],
        f"The answer stopped unexpectedly {job.max_attempts} times and was not tried again. "
        "Ask the question again.",
    )


def fail_answer(message_id: int, error: str) -> None:
    """End the answer failed with `error`, keeping what its events so far add up to."""
    with SessionLocal() as session:
        reply = fold(session, message_id)
        reply.error = error
        settle(session, message_id, reply, "failed")
        session.commit()


def sse(seq: int | None, event: dict[str, Any]) -> str:
    """One server-sent event; the seq is its id, which a client sends back to resume."""
    head = f"id: {seq}\n" if seq is not None else ""
    return f"{head}data: {json.dumps(event, default=str)}\n\n"
