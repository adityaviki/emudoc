"""A chat's last answer written again in its place, from its question as it is or as its member
edited it, which counts as a question asked (`regenerate` in app/api/chats.py)."""

from pydantic_ai.messages import TextPart
from sqlalchemy import select

from app.api.chats import NOT_THE_LAST_QUESTION, STILL_ANSWERING
from app.jobs import ANSWER_QUESTION
from app.models import Job
from tests.conftest import FakeTurn
from tests.test_chats import ask, follow, new_chat, questions_counted, run_next


def regenerate(client, chat_id, message_id, question: str | None = None):
    """Ask for the answer to be written again, its question edited when one is given; the
    response, whatever it says."""
    body = {} if question is None else {"question": question}
    return client.post(f"/api/chats/{chat_id}/messages/{message_id}/regenerate", json=body)


def test_a_finished_answer_is_written_again_and_counts_a_question(
    client, session, services, indexed
):
    services.llm.agent_turns = [FakeTurn(TextPart("90 days.")), FakeTurn(TextPart("Ninety days."))]
    chat = new_chat(client, indexed.project_id)
    asked = ask(client, chat["id"], "Notice period?")
    message_id = asked["answer"]["id"]
    run_next(services)
    assert questions_counted(session) == 1

    written = regenerate(client, chat["id"], message_id)
    assert written.status_code == 200, written.text
    body = written.json()
    assert body["question"]["id"] == asked["question"]["id"]
    assert body["question"]["content"] == "Notice period?"
    assert body["answer"]["id"] == message_id and body["answer"]["status"] == "streaming"
    assert body["answer"]["content"] == ""
    # The member asked for another answer: a question, as asking one is.
    assert questions_counted(session) == 2
    (job,) = session.scalars(
        select(Job).where(Job.kind == ANSWER_QUESTION, Job.status == "queued")
    ).all()
    assert (job.payload["message_id"], job.payload["attempt"]) == (message_id, 2)

    # Meanwhile the chat takes no other question, nor another regeneration.
    refused = regenerate(client, chat["id"], message_id)
    assert refused.status_code == 409 and refused.json() == {"detail": STILL_ANSWERING}

    run_next(services)
    events = follow(client, chat["id"], message_id, after=body["last_event_id"])
    assert [event["type"] for event in events] == ["restart", "text", "done", "end"]
    detail = client.get(f"/api/chats/{chat['id']}").json()
    assert [m["content"] for m in detail["messages"]] == ["Notice period?", "Ninety days."]
    # The model was asked the question alone, as the first time, without the first answer.
    second = services.llm.agent_requests[-1]
    assert [part.content for message in second for part in message.parts] == ["Notice period?"]
    # Each run is the organisation's to pay for: the answer shows both.
    assert detail["messages"][-1]["usage"]["answer"]["calls"] == 2


def test_the_last_question_edited_is_answered_in_its_place(client, session, services, indexed):
    services.llm.agent_turns = [FakeTurn(TextPart("A.")), FakeTurn(TextPart("B."))]
    chat = new_chat(client, indexed.project_id)
    asked = ask(client, chat["id"], "Notice periud?")
    run_next(services)

    written = regenerate(
        client, chat["id"], asked["answer"]["id"], "  What is the notice period?  "
    )
    assert written.status_code == 200, written.text
    assert written.json()["question"]["content"] == "What is the notice period?"
    run_next(services)
    detail = client.get(f"/api/chats/{chat['id']}").json()
    assert [m["content"] for m in detail["messages"]] == ["What is the notice period?", "B."]
    assert [m["id"] for m in detail["messages"]] == [
        asked["question"]["id"],
        asked["answer"]["id"],
    ]
    # The title the first question gave follows it.
    assert detail["title"] == "What is the notice period?"
    question = services.llm.agent_requests[-1][-1].parts[0].content
    assert question == "What is the notice period?"
    assert questions_counted(session) == 2

    # A title the member chose stays.
    client.patch(f"/api/chats/{chat['id']}", json={"title": "Notice"})
    services.llm.agent_turns = [FakeTurn(TextPart("C."))]
    regenerate(client, chat["id"], asked["answer"]["id"], "How long is the notice?")
    run_next(services)
    assert client.get(f"/api/chats/{chat['id']}").json()["title"] == "Notice"

    for blank, status in (("   ", 422), ("", 422), ("x" * 40_000, 413)):
        assert regenerate(client, chat["id"], asked["answer"]["id"], blank).status_code == status


def test_only_the_chats_last_answer_is_written_again(client, services, indexed):
    services.llm.agent_turns = [FakeTurn(TextPart("A.")), FakeTurn(TextPart("B."))]
    chat = new_chat(client, indexed.project_id)
    first = ask(client, chat["id"], "First?")
    run_next(services)
    second = ask(client, chat["id"], "Second?")
    run_next(services)

    refused = regenerate(client, chat["id"], first["answer"]["id"], "First, again?")
    assert refused.status_code == 409 and refused.json() == {"detail": NOT_THE_LAST_QUESTION}
    # A question is no answer, and an answer of another chat is not this one's.
    assert regenerate(client, chat["id"], second["question"]["id"]).status_code == 404
    other = new_chat(client, indexed.project_id)
    assert regenerate(client, other["id"], second["answer"]["id"]).status_code == 404
    # A stopped answer is written again too, and counts, unlike a try.
    third = ask(client, chat["id"], "Third?")
    client.post(f"/api/chats/{chat['id']}/messages/{third['answer']['id']}/stop")
    assert regenerate(client, chat["id"], third["answer"]["id"]).status_code == 200
