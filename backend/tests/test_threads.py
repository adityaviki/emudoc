"""The request threads: the answers a process streams at once are capped, the question past
the cap is refused with 429 and a sentence before it is saved, and the thread limiter every
request runs on is sized for the cap plus the rest."""

import threading

import anyio.to_thread
import httpx
from fastapi.testclient import TestClient
from pydantic_ai.messages import TextPart

from app.api.chats import TOO_MANY_ANSWERS, answers
from app.api.deps import RETRY_AFTER
from app.config import get_settings
from app.main import THREAD_HEADROOM, create_app
from tests.conftest import APP_HEADERS, FakeTurn
from tests.test_chats import ask, events_of, new_chat


def test_a_question_past_the_cap_is_refused_with_429_and_a_sentence(
    client, services, indexed, monkeypatch, caplog
):
    monkeypatch.setattr(get_settings(), "max_answers", 1)
    reached = threading.Event()
    release = threading.Event()

    class Held(FakeTurn):
        """A model turn that waits, as one does for the length of a real model call."""

        def __iter__(self):
            reached.set()
            assert release.wait(10)
            yield from super().__iter__()

    services.llm.agent_turns = [Held(TextPart("90 days.")), FakeTurn(TextPart("Monthly."))]
    first = new_chat(client, indexed.project_id)
    second = new_chat(client, indexed.project_id)
    responses: list[httpx.Response] = []

    def stream_the_first() -> None:
        # The client reads the whole body, so this returns when the answer ends.
        responses.append(
            client.post(f"/api/chats/{first['id']}/messages", json={"content": "Notice period?"})
        )

    thread = threading.Thread(target=stream_the_first)
    thread.start()
    try:
        assert reached.wait(10)
        assert answers.live == 1
        with caplog.at_level("WARNING", logger="app.api.chats"):
            response = client.post(
                f"/api/chats/{second['id']}/messages", json={"content": "Billing?"}
            )
        assert response.status_code == 429
        assert response.json() == {"detail": TOO_MANY_ANSWERS}
        assert response.headers["Retry-After"] == str(RETRY_AFTER)
        assert "refused: this process is answering 1 already" in caplog.text
        # The refused question is not in the chat: nothing waits there for an answer.
        assert client.get(f"/api/chats/{second['id']}").json()["messages"] == []
    finally:
        release.set()
        thread.join(10)
    (streamed,) = responses
    assert streamed.status_code == 200
    assert events_of(streamed)[-1]["type"] == "done"
    # With the first answer over, its place is free again.
    assert answers.live == 0
    assert [event["type"] for event in ask(client, second["id"], "Billing?")] == ["text", "done"]
    assert answers.live == 0


def test_a_refused_question_keeps_the_chat_as_it_was(client, indexed, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_answers", 1)
    chat = new_chat(client, indexed.project_id)
    # A place taken and never given back, as if another answer ran for the whole test.
    assert answers.start(1)
    try:
        response = client.post(f"/api/chats/{chat['id']}/messages", json={"content": "Terms?"})
        assert response.status_code == 429
        detail = client.get(f"/api/chats/{chat['id']}").json()
        assert detail["messages"] == [] and detail["title"] == ""
    finally:
        answers.end()


def test_the_thread_limiter_is_sized_for_the_cap_and_the_rest(
    services, session, mailer, identity_provider, monkeypatch
):
    monkeypatch.setattr(get_settings(), "max_answers", 3)
    http = httpx.Client(transport=httpx.MockTransport(identity_provider.handler))
    app = create_app(services=services, mailer=mailer, http=http)
    with TestClient(app, headers=APP_HEADERS) as client:
        # The limiter belongs to the event loop; the portal runs the call on it.
        limiter = client.portal.call(anyio.to_thread.current_default_thread_limiter)
        assert limiter.total_tokens == 3 + THREAD_HEADROOM
        assert client.get("/api/health").json() == {"status": "ok"}
