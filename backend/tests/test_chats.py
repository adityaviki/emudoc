"""Chats: created, listed, renamed, shared and deleted; a question saved with an empty answer,
which the worker writes (app/answers.py) and the events route streams from the table."""

import json

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart
from sqlalchemy import func, select
from sqlalchemy.exc import TimeoutError as PoolTimeout

from app.agent.tools import TOOLS
from app.answers import STOPPED, answer
from app.api.chats import STILL_ANSWERING
from app.config import get_settings
from app.db import BUSY
from app.jobs import ANSWER_QUESTION, LIVE
from app.llm import LLMUnavailable
from app.models import ChatEvent, Job
from tests.conftest import MISSING_KEY, FakeTurn


def sse_of(body: str) -> list[tuple[int | None, dict]]:
    """The (id, event) pairs of a server-sent event body, in order."""
    pairs = []
    for block in body.split("\n\n"):
        seq, data = None, None
        for line in block.splitlines():
            if line.startswith("id: "):
                seq = int(line[4:])
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if data is not None:
            pairs.append((seq, data))
    return pairs


def events_of(response) -> list[dict]:
    return [event for _, event in sse_of(response.text)]


def new_chat(client, project_id) -> dict:
    response = client.post(f"/api/projects/{project_id}/chats")
    assert response.status_code == 201
    return response.json()


def ask(client, chat_id, question) -> dict:
    """Ask, and get the question and its empty answer back; a worker writes the answer."""
    response = client.post(f"/api/chats/{chat_id}/messages", json={"content": question})
    assert response.status_code == 201, response.text
    return response.json()


def follow(client, chat_id, message_id, after: int | None = None) -> list[dict]:
    """The answer's events as the route streams them, from the start or past `after`."""
    headers = {"Last-Event-ID": str(after)} if after is not None else {}
    response = client.get(f"/api/chats/{chat_id}/messages/{message_id}/events", headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    return events_of(response)


def answered(client, services, chat_id, question) -> list[dict]:
    """Ask, write the answer as the worker would, and return its events."""
    asked = ask(client, chat_id, question)
    answer(asked["answer"]["id"], services)
    return follow(client, chat_id, asked["answer"]["id"])


def test_chat_crud(client, indexed):
    project_id = indexed.project_id
    assert client.get(f"/api/projects/{project_id}/chats").json() == []
    assert client.post("/api/projects/999/chats").status_code == 404

    chat = new_chat(client, project_id)
    assert chat["title"] == "" and chat["project_id"] == project_id
    assert chat["visibility"] == "private"
    assert client.get(f"/api/chats/{chat['id']}").json()["messages"] == []
    assert [c["id"] for c in client.get(f"/api/projects/{project_id}/chats").json()] == [chat["id"]]

    renamed = client.patch(f"/api/chats/{chat['id']}", json={"title": "Notice periods"}).json()
    assert renamed["title"] == "Notice periods"
    assert client.patch(f"/api/chats/{chat['id']}", json={"title": ""}).status_code == 422

    shared = client.patch(f"/api/chats/{chat['id']}", json={"visibility": "project"}).json()
    assert shared["visibility"] == "project" and shared["title"] == "Notice periods"
    public = client.patch(f"/api/chats/{chat['id']}", json={"visibility": "public"})
    assert public.status_code == 422

    assert client.delete(f"/api/chats/{chat['id']}").status_code == 204
    assert client.get(f"/api/chats/{chat['id']}").status_code == 404

    # A project takes its chats with it.
    chat = new_chat(client, project_id)
    assert client.delete(f"/api/projects/{project_id}").status_code == 204
    assert client.get(f"/api/chats/{chat['id']}").status_code == 404


def test_asking_saves_the_question_and_queues_its_answer(client, session, indexed):
    chat = new_chat(client, indexed.project_id)
    asked = ask(client, chat["id"], "  What is the\nnotice period?  ")
    question, empty = asked["question"], asked["answer"]
    assert question["role"] == "user" and question["content"] == "What is the\nnotice period?"
    assert question["status"] == "done"
    assert empty["role"] == "assistant" and empty["content"] == ""
    assert empty["status"] == "streaming" and empty["steps"] == [] and empty["sources"] == []

    # The job that writes the answer went in with the question, ahead of any upload.
    (job,) = session.scalars(select(Job).where(Job.kind == ANSWER_QUESTION)).all()
    assert job.payload == {"message_id": empty["id"]}
    assert job.status == "queued" and job.priority == LIVE
    assert job.org_id == indexed.project.org_id and job.project_id == indexed.project_id

    detail = client.get(f"/api/chats/{chat['id']}").json()
    assert detail["title"] == "What is the notice period?"
    assert [(m["id"], m["status"]) for m in detail["messages"]] == [
        (question["id"], "done"),
        (empty["id"], "streaming"),
    ]
    blank = client.post(f"/api/chats/{chat['id']}/messages", json={"content": " "})
    assert blank.status_code == 422


def test_a_question_past_the_length_limit_is_refused_before_it_is_saved(
    client, session, indexed, monkeypatch
):
    monkeypatch.setattr(get_settings(), "max_question_chars", 40)
    chat = new_chat(client, indexed.project_id)
    question = "What does the contract say about " + "late deliveries? " * 3
    refused = client.post(f"/api/chats/{chat['id']}/messages", json={"content": question})
    assert refused.status_code == 413
    assert refused.json() == {
        "detail": (
            f"This question is {len(question.strip()):,} characters long, and a question can "
            "be 40 at most. Shorten it, or upload the text as a document and ask about that."
        )
    }
    assert client.get(f"/api/chats/{chat['id']}").json()["messages"] == []
    assert (
        session.scalar(select(func.count()).select_from(Job).where(Job.kind == ANSWER_QUESTION))
        == 0
    )

    # The limit is on the question as asked, not on the whitespace around it.
    ask(client, chat["id"], "  " + "late deliveries? " * 2 + "  ")


def test_the_worker_writes_the_answer_and_the_route_streams_it(client, services, indexed):
    services.llm.agent_turns = [
        FakeTurn(ToolCallPart("query_facts", {"names": ["notice_period"]}, "call_1")),
        FakeTurn(TextPart("It is "), TextPart("90 days.")),
    ]
    chat = new_chat(client, indexed.project_id)
    events = answered(client, services, chat["id"], "What is the notice period?")
    # Text that arrives within a moment is written out as one event; the stream ends with
    # the answer's final status.
    assert [event["type"] for event in events] == [
        "tool_call",
        "sources",
        "tool_result",
        "text",
        "done",
        "end",
    ]
    assert events[3]["text"] == "It is 90 days."
    assert events[-1] == {"type": "end", "status": "done"}

    detail = client.get(f"/api/chats/{chat['id']}").json()
    question, reply = detail["messages"]
    assert reply["content"] == "It is 90 days." and reply["status"] == "done"
    (step,) = reply["steps"]
    assert step["name"] == "query_facts" and step["input"] == {"names": ["notice_period"]}
    assert step["is_error"] is False and "facts match" in step["summary"]
    assert any("ninety (90) days" in source["excerpt"] for source in reply["sources"])
    assert reply["notice"] is None and reply["error"] is None

    # The next question goes to the model with the whole conversation, and keeps the title.
    services.llm.agent_turns = [FakeTurn(TextPart("Ninety days, as I said."))]
    answered(client, services, chat["id"], "And in days?")
    history = services.llm.agent_requests[-1]
    assert [type(message) for message in history] == [ModelRequest, ModelResponse, ModelRequest]
    assert history[1].parts[0].content == "It is 90 days."
    assert history[2].parts[0].content == "And in days?"
    detail = client.get(f"/api/chats/{chat['id']}").json()
    assert detail["title"] == "What is the notice period?" and len(detail["messages"]) == 4


def test_a_follower_resumes_from_the_last_event_it_saw(client, services, indexed):
    services.llm.agent_turns = [
        FakeTurn(ToolCallPart("search", {"query": "notice"}, "call_1")),
        FakeTurn(TextPart("90 days.")),
    ]
    chat = new_chat(client, indexed.project_id)
    asked = ask(client, chat["id"], "Notice period?")
    answer(asked["answer"]["id"], services)
    response = client.get(f"/api/chats/{chat['id']}/messages/{asked['answer']['id']}/events")
    pairs = sse_of(response.text)
    assert [seq for seq, _ in pairs] == [0, 1, 2, 3, 4, None]
    # After a dropped connection the client sends the id it saw last and gets the rest.
    later = follow(client, chat["id"], asked["answer"]["id"], after=2)
    assert later == [event for seq, event in pairs if seq is None or seq > 2]
    assert [event["type"] for event in later] == ["text", "done", "end"]
    # A header that is not a number counts as nothing seen.
    everything = client.get(
        f"/api/chats/{chat['id']}/messages/{asked['answer']['id']}/events",
        headers={"Last-Event-ID": "x"},
    )
    assert sse_of(everything.text) == pairs

    # Only an answer in this chat can be followed; a question has no events.
    other = new_chat(client, indexed.project_id)
    message_id = asked["answer"]["id"]
    assert client.get(f"/api/chats/{other['id']}/messages/{message_id}/events").status_code == 404
    question_id = asked["question"]["id"]
    assert client.get(f"/api/chats/{chat['id']}/messages/{question_id}/events").status_code == 404


def test_the_latest_chat_comes_first_and_long_titles_are_cut(client, indexed):
    first = new_chat(client, indexed.project_id)
    second = new_chat(client, indexed.project_id)
    ask(client, second["id"], "x")
    ask(client, first["id"], "Please list every payment obligation in the agreement and the dates")

    listed = client.get(f"/api/projects/{indexed.project_id}/chats").json()
    assert [chat["id"] for chat in listed] == [first["id"], second["id"]]
    assert listed[0]["title"] == "Please list every payment obligation in the agreement and…"


def test_a_chat_takes_one_question_at_a_time(client, services, indexed):
    services.llm.agent_turns = [FakeTurn(TextPart("A."))]
    chat = new_chat(client, indexed.project_id)
    asked = ask(client, chat["id"], "First?")
    # The answer is still being written (nothing has run it): the next question waits.
    refused = client.post(f"/api/chats/{chat['id']}/messages", json={"content": "Second?"})
    assert refused.status_code == 409
    assert refused.json() == {"detail": STILL_ANSWERING}
    assert len(client.get(f"/api/chats/{chat['id']}").json()["messages"]) == 2

    answer(asked["answer"]["id"], services)
    ask(client, chat["id"], "Second?")
    assert len(client.get(f"/api/chats/{chat['id']}").json()["messages"]) == 4


def test_stopping_an_answer_settles_it_at_once(client, session, services, indexed):
    chat = new_chat(client, indexed.project_id)
    asked = ask(client, chat["id"], "Notice period?")
    message_id = asked["answer"]["id"]
    # No worker has started on it, as when none is running: the stop still takes effect.
    stopped = client.post(f"/api/chats/{chat['id']}/messages/{message_id}/stop")
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "stopped" and stopped.json()["notice"] == STOPPED
    assert stopped.json()["content"] == "" and stopped.json()["error"] is None
    assert follow(client, chat["id"], message_id) == [{"type": "end", "status": "stopped"}]
    # Stopping again changes nothing, and the chat takes the next question.
    assert client.post(f"/api/chats/{chat['id']}/messages/{message_id}/stop").json() == (
        stopped.json()
    )
    ask(client, chat["id"], "Billing?")

    # The worker that gets to the stopped answer later writes nothing.
    services.llm.agent_turns = [FakeTurn(TextPart("Late."))]
    answer(message_id, services)
    assert session.scalars(select(ChatEvent).where(ChatEvent.message_id == message_id)).all() == []
    assert client.get(f"/api/chats/{chat['id']}").json()["messages"][1]["content"] == ""
    assert services.llm.agent_requests == []


def test_asking_without_a_model_saves_the_error(client, services, indexed):
    stream_agent = services.llm.stream_agent

    def unavailable(*args):
        raise LLMUnavailable(MISSING_KEY)

    services.llm.stream_agent = unavailable
    chat = new_chat(client, indexed.project_id)
    assert answered(client, services, chat["id"], "Hello") == [
        {"type": "error", "message": MISSING_KEY},
        {"type": "end", "status": "failed"},
    ]
    reply = client.get(f"/api/chats/{chat['id']}").json()["messages"][-1]
    assert reply["role"] == "assistant" and reply["content"] == ""
    assert reply["error"] == MISSING_KEY and reply["status"] == "failed"

    # Once the model is back, the failed answer is not part of the conversation it sees.
    services.llm.stream_agent = stream_agent
    services.llm.agent_turns = [FakeTurn(TextPart("Hi."))]
    answered(client, services, chat["id"], "Hello again")
    (history,) = services.llm.agent_requests
    assert [part.content for message in history for part in message.parts] == [
        "Hello",
        "Hello again",
    ]


def test_an_answer_that_finds_every_connection_in_use_ends_with_the_busy_sentence(
    client, services, indexed, monkeypatch, caplog
):
    # What the pool raises when a tool's query waited DB_POOL_TIMEOUT for a connection. Not the
    # tool's fault and nothing the model can route around, so the answer ends as a refused
    # request does (test_pool.py) instead of telling the model to try a different approach.
    def every_connection_in_use(ctx, args):
        raise PoolTimeout("QueuePool limit of size 1 overflow 0 reached, connection timed out")

    monkeypatch.setattr(TOOLS["search"], "run", every_connection_in_use)
    services.llm.agent_turns = [FakeTurn(ToolCallPart("search", {"query": "notice"}, "call_1"))]
    chat = new_chat(client, indexed.project_id)
    with caplog.at_level("WARNING", logger="app.answers"):
        events = answered(client, services, chat["id"], "Notice period?")
    assert [event["type"] for event in events] == ["tool_call", "error", "end"]
    assert events[1]["message"] == BUSY and events[2]["status"] == "failed"
    assert "every database connection is in use" in caplog.text
    assert "Traceback" not in caplog.text
    reply = client.get(f"/api/chats/{chat['id']}").json()["messages"][-1]
    assert reply["role"] == "assistant" and reply["error"] == BUSY
