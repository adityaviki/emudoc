import json

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart
from sqlalchemy import select

from app.api.chats import stream_answer
from app.llm import LLMUnavailable
from app.models import Chat, ChatMessage
from tests.conftest import MISSING_KEY, FakeTurn


def events_of(response) -> list[dict]:
    return [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]


def new_chat(client, project_id) -> dict:
    response = client.post(f"/api/projects/{project_id}/chats")
    assert response.status_code == 201
    return response.json()


def ask(client, chat_id, question) -> list[dict]:
    response = client.post(f"/api/chats/{chat_id}/messages", json={"content": question})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    return events_of(response)


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


def test_asking_saves_the_question_and_the_answer(client, services, indexed):
    services.llm.agent_turns = [
        FakeTurn(ToolCallPart("query_facts", {"names": ["notice_period"]}, "call_1")),
        FakeTurn(TextPart("It is "), TextPart("90 days.")),
    ]
    chat = new_chat(client, indexed.project_id)
    events = ask(client, chat["id"], "  What is the\nnotice period?  ")
    assert [event["type"] for event in events] == [
        "tool_call",
        "sources",
        "tool_result",
        "text",
        "text",
        "done",
    ]

    detail = client.get(f"/api/chats/{chat['id']}").json()
    assert detail["title"] == "What is the notice period?"
    question, answer = detail["messages"]
    assert question["role"] == "user" and question["content"] == "What is the\nnotice period?"
    assert answer["role"] == "assistant" and answer["content"] == "It is 90 days."
    (step,) = answer["steps"]
    assert step["name"] == "query_facts" and step["input"] == {"names": ["notice_period"]}
    assert step["is_error"] is False and "facts match" in step["summary"]
    assert any("ninety (90) days" in source["excerpt"] for source in answer["sources"])
    assert answer["notice"] is None and answer["error"] is None

    # The next question goes to the model with the whole conversation, and keeps the title.
    services.llm.agent_turns = [FakeTurn(TextPart("Ninety days, as I said."))]
    ask(client, chat["id"], "And in days?")
    history = services.llm.agent_requests[-1]
    assert [type(message) for message in history] == [ModelRequest, ModelResponse, ModelRequest]
    assert history[1].parts[0].content == "It is 90 days."
    assert history[2].parts[0].content == "And in days?"
    detail = client.get(f"/api/chats/{chat['id']}").json()
    assert detail["title"] == "What is the notice period?" and len(detail["messages"]) == 4


def test_the_latest_chat_comes_first_and_long_titles_are_cut(client, services, indexed):
    services.llm.agent_turns = [FakeTurn(TextPart("A.")), FakeTurn(TextPart("B."))]
    first = new_chat(client, indexed.project_id)
    second = new_chat(client, indexed.project_id)
    ask(client, second["id"], "x")
    ask(client, first["id"], "Please list every payment obligation in the agreement and the dates")

    listed = client.get(f"/api/projects/{indexed.project_id}/chats").json()
    assert [chat["id"] for chat in listed] == [first["id"], second["id"]]
    assert listed[0]["title"] == "Please list every payment obligation in the agreement and…"


def test_a_stopped_answer_keeps_what_arrived(session, services, account, indexed):
    services.llm.agent_turns = [FakeTurn(TextPart("The notice period "), TextPart("is 90 days."))]
    chat = Chat(project_id=indexed.project_id, user_id=account.user.id, title="Notice")
    session.add(chat)
    session.flush()
    session.add(ChatMessage(chat_id=chat.id, role="user", content="Notice period?"))
    session.commit()

    stream = stream_answer(chat.id, services)
    assert json.loads(next(stream)[6:]) == {"type": "text", "text": "The notice period "}
    # The client went away: Starlette closes the generator where it last yielded.
    stream.close()

    saved = session.scalars(
        select(ChatMessage).where(ChatMessage.chat_id == chat.id).order_by(ChatMessage.id)
    ).all()
    assert [message.role for message in saved] == ["user", "assistant"]
    assert saved[1].content == "The notice period "
    assert saved[1].notice == "Stopped." and saved[1].error is None


def test_asking_without_a_model_saves_the_error(client, services, indexed):
    stream_agent = services.llm.stream_agent

    def unavailable(*args):
        raise LLMUnavailable(MISSING_KEY)

    services.llm.stream_agent = unavailable
    chat = new_chat(client, indexed.project_id)
    assert ask(client, chat["id"], "Hello") == [{"type": "error", "message": MISSING_KEY}]
    answer = client.get(f"/api/chats/{chat['id']}").json()["messages"][-1]
    assert answer["role"] == "assistant" and answer["content"] == ""
    assert answer["error"] == MISSING_KEY

    # Once the model is back, the failed answer is not part of the conversation it sees.
    services.llm.stream_agent = stream_agent
    services.llm.agent_turns = [FakeTurn(TextPart("Hi."))]
    ask(client, chat["id"], "Hello again")
    (history,) = services.llm.agent_requests
    assert [part.content for message in history for part in message.parts] == [
        "Hello",
        "Hello again",
    ]
