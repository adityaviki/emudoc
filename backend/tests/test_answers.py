"""Answers as jobs: the worker's answerer lane writes an answer's events under its lease, a
follower reads them as they land, a stop settles the row whoever holds the job, a lost run
starts the answer over, a try again is written by its own run alone, and the cap on answers in
flight refuses the question past it."""

import threading
import time
from datetime import timedelta

import pytest
from pydantic_ai.messages import TextPart, ToolCallPart
from sqlalchemy import select, text

import app.answers as answers_module
import app.worker as worker_module
from app.agent.tools import TOOLS
from app.answers import FAILED, STOPPED, abandon_answer, answer, fail_answer
from app.api.chats import TOO_MANY_ANSWERS
from app.api.deps import RETRY_AFTER
from app.config import get_settings
from app.jobs import ANSWER_QUESTION, MAX_ATTEMPTS, Lease, LeaseLost, claim
from app.maintenance import housekeeping
from app.models import ChatEvent, ChatMessage, Job
from app.worker import HANDLERS, Worker
from tests.conftest import FakeTurn
from tests.test_chats import answered, ask, follow, new_chat, try_again
from tests.test_jobs import lose, wait_until

ANSWERS = [ANSWER_QUESTION]


def answer_jobs(session) -> list[Job]:
    session.expire_all()
    return session.scalars(select(Job).where(Job.kind == ANSWER_QUESTION).order_by(Job.id)).all()


def events_in(session, message_id: int) -> list[dict]:
    session.expire_all()
    return session.scalars(
        select(ChatEvent.event).where(ChatEvent.message_id == message_id).order_by(ChatEvent.seq)
    ).all()


def message(session, message_id: int) -> ChatMessage:
    session.expire_all()
    return session.get(ChatMessage, message_id)


class Held(FakeTurn):
    """A model turn that waits to be released, as one does for the length of a real call."""

    def __init__(self, *parts):
        super().__init__(*parts)
        self.reached = threading.Event()
        self.release = threading.Event()

    def __iter__(self):
        self.reached.set()
        assert self.release.wait(10)
        yield from super().__iter__()


@pytest.fixture
def worker(services):
    """A worker with the answerer lane only, stopped after the test."""
    worker = Worker(services, concurrency=0, answer_concurrency=1)
    worker.start()
    yield worker
    worker.stop()


def test_the_answerer_lane_takes_the_question_at_once(
    client, session, services, indexed, monkeypatch, worker
):
    # The worker's threads poll seldom; the listener hears the question as it is committed.
    # It gets a moment to connect first: a worker that starts while questions are queued
    # claims them at start anyway, so nothing waits on that in practice.
    monkeypatch.setattr(worker_module, "POLL_SECONDS", 30)
    time.sleep(0.3)
    services.llm.agent_turns = [FakeTurn(TextPart("90 days."))]
    chat = new_chat(client, indexed.project_id)
    asked = ask(client, chat["id"], "Notice period?")
    wait_until(lambda: message(session, asked["answer"]["id"]).status == "done", timeout=5)
    assert message(session, asked["answer"]["id"]).content == "90 days."
    # The answer is settled a moment before its job is marked done, in a commit of its own.
    wait_until(lambda: answer_jobs(session)[0].status == "done", timeout=5)
    (job,) = answer_jobs(session)
    assert job.worker_id == worker.id
    names = {thread.name for thread in worker._threads}
    assert "answerer-0" in names and "listener" in names and "indexer-0" not in names


def test_a_follower_gets_the_events_as_they_are_written(client, services, indexed):
    services.llm.agent_turns = [
        FakeTurn(ToolCallPart("search", {"query": "notice"}, "call_1")),
        FakeTurn(TextPart("90 days.")),
    ]
    chat = new_chat(client, indexed.project_id)
    asked = ask(client, chat["id"], "Notice period?")
    message_id = asked["answer"]["id"]
    seen: list[list[dict]] = []
    # The client connects before anything was written and reads until the answer ends.
    thread = threading.Thread(target=lambda: seen.append(follow(client, chat["id"], message_id)))
    thread.start()
    answer(message_id, services)
    thread.join(10)
    (events,) = seen
    assert [event["type"] for event in events] == [
        "tool_call",
        "sources",
        "tool_result",
        "text",
        "done",
        "end",
    ]


def test_a_stop_mid_answer_keeps_what_arrived_and_ends_the_run(
    client, session, services, indexed, caplog
):
    held = Held(TextPart("The notice period is 90 days."))
    services.llm.agent_turns = [
        FakeTurn(ToolCallPart("search", {"query": "notice"}, "call_1")),
        held,
    ]
    chat = new_chat(client, indexed.project_id)
    asked = ask(client, chat["id"], "Notice period?")
    message_id = asked["answer"]["id"]
    run = threading.Thread(target=answer, args=(message_id, services))
    run.start()
    try:
        assert held.reached.wait(10)
        # The search ran; the model is thinking. The stop settles the row from the events.
        with caplog.at_level("INFO", logger="app.answers"):
            stopped = client.post(f"/api/chats/{chat['id']}/messages/{message_id}/stop").json()
            assert stopped["status"] == "stopped" and stopped["notice"] == STOPPED
            (step,) = stopped["steps"]
            assert step["name"] == "search" and "passage" in step["summary"]
            assert stopped["sources"] and stopped["content"] == ""
            held.release.set()
            run.join(10)
    finally:
        held.release.set()
    # The run found the row settled at its next write and ended there: the text it had is
    # not in the events, and the row is as the stop left it.
    assert [event["type"] for event in events_in(session, message_id)] == [
        "tool_call",
        "sources",
        "tool_result",
    ]
    assert "stopped, removed or tried again while it was being written" in caplog.text
    final = message(session, message_id)
    assert final.status == "stopped" and final.content == "" and final.notice == STOPPED
    assert follow(client, chat["id"], message_id)[-1] == {"type": "end", "status": "stopped"}


def test_a_chat_deleted_for_good_mid_answer_ends_the_run(client, session, services, indexed):
    held = Held(TextPart("Gone."))
    services.llm.agent_turns = [held]
    chat = new_chat(client, indexed.project_id)
    asked = ask(client, chat["id"], "Notice period?")
    run = threading.Thread(target=answer, args=(asked["answer"]["id"], services))
    run.start()
    try:
        assert held.reached.wait(10)
        # In the trash an answer would finish; deleted from there, the run has nowhere to write.
        assert client.delete(f"/api/chats/{chat['id']}").status_code == 204
        assert client.delete(f"/api/trash/chats/{chat['id']}").status_code == 204
    finally:
        held.release.set()
    run.join(10)
    assert not run.is_alive()
    assert session.scalar(select(ChatEvent.seq)) is None


def test_a_lost_run_starts_the_answer_over(client, session, services, indexed, monkeypatch):
    services.llm.agent_turns = [
        FakeTurn(ToolCallPart("search", {"query": "notice"}, "call_1")),
        FakeTurn(ToolCallPart("search", {"query": "notice"}, "call_2")),
        FakeTurn(TextPart("90 days.")),
    ]
    chat = new_chat(client, indexed.project_id)
    asked = ask(client, chat["id"], "Notice period?")
    message_id = asked["answer"]["id"]
    job = claim("w:1", ANSWERS)
    search = TOOLS["search"].run

    def search_while_taken_over(ctx, args):
        output = search(ctx, args)
        # The heartbeat could not reach the database for LEASE: the next claim took the job
        # while the search was running.
        lose(session, job.id)
        session.commit()
        return output

    monkeypatch.setattr(TOOLS["search"], "run", search_while_taken_over)
    with pytest.raises(LeaseLost):
        HANDLERS[ANSWER_QUESTION].run(job, services)
    # The first write after the search, the sources it found, was rolled back with the lease
    # test, and nothing of that run came after it.
    assert [event["type"] for event in events_in(session, message_id)] == ["tool_call"]
    assert message(session, message_id).status == "streaming"

    # The next worker claims the job and starts the answer over, telling the followers so.
    monkeypatch.setattr(TOOLS["search"], "run", search)
    taken = claim("w:2", ANSWERS)
    assert taken.id == job.id and taken.attempts == 2
    assert HANDLERS[ANSWER_QUESTION].run(taken, services) is None
    assert [event["type"] for event in events_in(session, message_id)] == [
        "tool_call",
        "restart",
        "tool_call",
        "sources",
        "tool_result",
        "text",
        "done",
    ]
    final = message(session, message_id)
    assert final.status == "done" and final.content == "90 days."
    assert [step["id"] for step in final.steps] == ["call_2"]
    # A client that saw the first run's events drops them at the restart.
    events = follow(client, chat["id"], message_id)
    assert events[1] == {"type": "restart"} and events[-1] == {"type": "end", "status": "done"}


def test_an_answer_lost_on_every_attempt_is_given_up(client, session, services, indexed):
    chat = new_chat(client, indexed.project_id)
    asked = ask(client, chat["id"], "Notice period?")
    message_id = asked["answer"]["id"]
    session.execute(
        text("INSERT INTO chat_events (message_id, seq, event) VALUES (:id, 0, :event)"),
        {"id": message_id, "event": '{"type": "text", "text": "So far"}'},
    )
    (job,) = answer_jobs(session)
    lose(session, job.id, attempts=MAX_ATTEMPTS)
    session.commit()
    worker = Worker(services, concurrency=0, answer_concurrency=1)
    worker.start()
    try:
        wait_until(lambda: answer_jobs(session)[0].status == "failed")
    finally:
        worker.stop()
    assert answer_jobs(session)[0].last_error == f"Given up after {MAX_ATTEMPTS} lost attempts"
    final = message(session, message_id)
    assert final.status == "failed" and final.content == "So far"
    assert final.error == (
        f"The answer stopped unexpectedly {MAX_ATTEMPTS} times. Try again in a moment."
    )
    assert services.llm.agent_requests == []


def test_a_crash_in_the_run_fails_the_answer_and_the_job(
    client, session, services, indexed, monkeypatch
):
    def broken(*args):
        raise RuntimeError("the loop itself broke")

    monkeypatch.setattr(answers_module, "run_agent", broken)
    chat = new_chat(client, indexed.project_id)
    asked = ask(client, chat["id"], "Notice period?")
    job = claim("w:1", ANSWERS)
    with pytest.raises(RuntimeError):
        HANDLERS[ANSWER_QUESTION].run(job, services)
    final = message(session, asked["answer"]["id"])
    assert final.status == "failed" and final.error == FAILED
    # The lease guards the settling too: a run whose job was taken over settles nothing.
    monkeypatch.setattr(answers_module, "run_agent", lambda *args: iter([]))
    other = new_chat(client, indexed.project_id)
    asked = ask(client, other["id"], "Billing?")
    job = claim("w:1", ANSWERS)
    lose(session, job.id)
    session.commit()
    with pytest.raises(LeaseLost):
        answer(asked["answer"]["id"], services, Lease(job.id, "w:1"))
    assert message(session, asked["answer"]["id"]).status == "streaming"


def test_a_question_past_the_cap_is_refused_with_429_and_a_sentence(
    client, services, indexed, make_account, act_as, monkeypatch, caplog
):
    monkeypatch.setattr(get_settings(), "max_answers", 1)
    first = new_chat(client, indexed.project_id)
    second = new_chat(client, indexed.project_id)
    asked = ask(client, first["id"], "Notice period?")
    with caplog.at_level("WARNING", logger="app.api.chats"):
        response = client.post(f"/api/chats/{second['id']}/messages", json={"content": "Billing?"})
    assert response.status_code == 429
    assert response.json() == {"detail": TOO_MANY_ANSWERS}
    assert response.headers["Retry-After"] == str(RETRY_AFTER)
    assert "refused: organisation 1 has 1 answers in flight" in caplog.text
    # The refused question is not in the chat: nothing waits there for an answer.
    assert client.get(f"/api/chats/{second['id']}").json()["messages"] == []
    assert client.get(f"/api/chats/{second['id']}").json()["title"] == ""

    # The cap is each organisation's own: another one's questions go through.
    act_as(make_account("ann@globex.example"))
    project = client.post("/api/projects", json={"name": "Theirs"}).json()
    theirs = new_chat(client, project["id"])
    ask(client, theirs["id"], "Hello?")

    # With the first answer written, its place is free again.
    services.llm.agent_turns = [FakeTurn(TextPart("90 days.")), FakeTurn(TextPart("Monthly."))]
    client.app.dependency_overrides.clear()
    answer(asked["answer"]["id"], services)
    assert [event["type"] for event in answered(client, services, second["id"], "Billing?")] == [
        "text",
        "done",
        "end",
    ]


def test_text_is_written_out_in_batches(client, session, services, indexed, monkeypatch):
    services.llm.agent_turns = [FakeTurn(TextPart("It is "), TextPart("90 "), TextPart("days."))]
    chat = new_chat(client, indexed.project_id)
    # Text that keeps coming is gathered for a moment; with no wait, each piece goes out alone.
    monkeypatch.setattr(answers_module, "TEXT_FLUSH_SECONDS", 0)
    assert [event["type"] for event in answered(client, services, chat["id"], "Notice?")] == [
        "text",
        "text",
        "text",
        "done",
        "end",
    ]
    monkeypatch.setattr(answers_module, "TEXT_FLUSH_SECONDS", 60)
    services.llm.agent_turns = [FakeTurn(TextPart("It is "), TextPart("90 "), TextPart("days."))]
    asked = ask(client, chat["id"], "Again?")
    answer(asked["answer"]["id"], services)
    assert events_in(session, asked["answer"]["id"]) == [
        {"type": "text", "text": "It is 90 days."},
        {
            "type": "done",
            "usage": {
                "model": get_settings().agent_model,
                "calls": 1,
                "input_tokens": 10,
                "output_tokens": 5,
                "cost_micros": None,
            },
        },
    ]


def test_a_try_made_while_the_stopped_run_waits_on_the_model_is_its_own_runs_alone(
    client, session, services, indexed, caplog
):
    held = Held(TextPart("The first try's words."))
    services.llm.agent_turns = [
        FakeTurn(ToolCallPart("search", {"query": "notice"}, "call_1")),
        held,
        FakeTurn(TextPart("The second try's words.")),
    ]
    chat = new_chat(client, indexed.project_id)
    message_id = ask(client, chat["id"], "Notice period?")["answer"]["id"]
    first = threading.Thread(target=answer, args=(message_id, services))
    first.start()
    try:
        assert held.reached.wait(10)
        # The member stops the answer while the model thinks, and tries it again at once.
        client.post(f"/api/chats/{chat['id']}/messages/{message_id}/stop")
        retried = try_again(client, chat["id"], message_id).json()
        assert retried["last_event_id"] == 2
        # Then the first run's model answers, into an answer that is the second try's now.
        with caplog.at_level("INFO", logger="app.answers"):
            held.release.set()
            first.join(10)
    finally:
        held.release.set()
    assert not first.is_alive()
    assert "stopped, removed or tried again while it was being written" in caplog.text
    before = ["tool_call", "sources", "tool_result"]
    assert [event["type"] for event in events_in(session, message_id)] == before
    row = message(session, message_id)
    assert (row.status, row.attempt, row.content) == ("streaming", 2, "")

    answer(message_id, services, attempt=2)
    assert [event["type"] for event in events_in(session, message_id)] == [
        *before,
        "restart",
        "text",
        "done",
    ]
    row = message(session, message_id)
    assert row.status == "done" and row.content == "The second try's words."
    assert row.steps == [] and row.sources == [] and row.notice is None
    # Every run's model calls were paid for, and the answer shows them all: the first try's
    # search and its words, the second try's words.
    assert row.usage["answer"]["calls"] == 3


def test_the_job_of_an_earlier_try_writes_nothing(client, session, services, indexed):
    services.llm.agent_turns = [FakeTurn(TextPart("Answered."))]
    chat = new_chat(client, indexed.project_id)
    message_id = ask(client, chat["id"], "Notice period?")["answer"]["id"]
    # Stopped before any worker got to it, so its job is still queued when the member tries
    # again.
    client.post(f"/api/chats/{chat['id']}/messages/{message_id}/stop")
    assert try_again(client, chat["id"], message_id).json()["last_event_id"] is None
    earlier, later = answer_jobs(session)
    assert (earlier.payload["attempt"], later.payload["attempt"]) == (1, 2)

    taken = claim("w:1", ANSWERS)
    assert taken.id == earlier.id
    assert HANDLERS[ANSWER_QUESTION].run(taken, services) is None
    assert services.llm.agent_requests == [] and events_in(session, message_id) == []
    # Nor does the earlier try's failing, or its job given up, settle the later one.
    fail_answer(message_id, 1, FAILED)
    abandon_answer(earlier, services)
    assert message(session, message_id).status == "streaming"

    HANDLERS[ANSWER_QUESTION].run(claim("w:1", ANSWERS), services)
    # No earlier try left an event, so the new one's start at the beginning, without a restart.
    assert [event["type"] for event in events_in(session, message_id)] == ["text", "done"]
    row = message(session, message_id)
    assert row.status == "done" and row.content == "Answered."


def test_the_events_of_old_answers_are_pruned(client, session, services, indexed):
    services.llm.agent_turns = [FakeTurn(TextPart("A.")), FakeTurn(TextPart("B."))]
    chat = new_chat(client, indexed.project_id)
    answered(client, services, chat["id"], "First?")
    old = client.get(f"/api/chats/{chat['id']}").json()["messages"][-1]["id"]
    answered(client, services, chat["id"], "Second?")
    recent = client.get(f"/api/chats/{chat['id']}").json()["messages"][-1]["id"]
    # A third is still being written, however old its question.
    asked = ask(client, chat["id"], "Third?")
    live = asked["answer"]["id"]
    session.execute(
        text("INSERT INTO chat_events (message_id, seq, event) VALUES (:id, 0, '{}')"),
        {"id": live},
    )
    session.execute(
        text("UPDATE chat_messages SET created_at = now() - :age WHERE id IN (:old, :live)"),
        {"age": timedelta(days=2), "old": old, "live": live},
    )
    session.commit()

    housekeeping(services.storage)
    assert events_in(session, old) == []
    assert len(events_in(session, recent)) == 2
    assert len(events_in(session, live)) == 1
    # The finished answer is read from its row, which holds it.
    assert client.get(f"/api/chats/{chat['id']}").json()["messages"][1]["content"] == "A."
    assert follow(client, chat["id"], old) == [{"type": "end", "status": "done"}]


def test_the_listener_recovers_from_a_lost_connection(services, monkeypatch, caplog):
    calls = []
    lost = threading.Event()

    def flaky_listen(stop, woken, timeout, listening=None):
        calls.append(timeout)
        listening.set()
        if len(calls) == 1:
            lost.wait(10)
            raise ConnectionError("the database went away")
        stop.wait(10)

    monkeypatch.setattr(worker_module, "listen", flaky_listen)
    monkeypatch.setattr(worker_module, "RETRY_SECONDS", 0.5)
    worker = Worker(services, concurrency=0)
    with caplog.at_level("ERROR", logger="app.worker"):
        worker.start()
        try:
            wait_until(worker.listening)
            lost.set()
            # Polling while the retry waits, which the metrics say (emudoc_worker_listening).
            wait_until(lambda: not worker.listening())
            assert len(calls) == 1
            wait_until(lambda: len(calls) >= 2 and worker.listening())
        finally:
            worker.stop()
    assert "Listening for queued jobs failed" in caplog.text
