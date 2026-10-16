"""The embedder's errors: what the endpoint answered, in words, and whether asking again in a
while may help, which the pipeline reads to run the embed stage again."""

import json
import threading

import httpx
import pytest

import app.embeddings as embeddings
from app.config import get_settings
from app.embeddings import BATCH_SIZE, Embedder, EmbeddingError
from app.usage import Calls, ModelUsage


def embedder(handler) -> Embedder:
    embedder = Embedder(get_settings())
    embedder._client = httpx.Client(
        base_url="http://embeddings", transport=httpx.MockTransport(handler)
    )
    return embedder


def refuse(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("Connection refused", request=request)


def stall(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("timed out", request=request)


def vector(request: httpx.Request) -> httpx.Response:
    embedding = [0.5] * get_settings().embedding_dim
    return httpx.Response(200, json={"data": [{"index": 0, "embedding": embedding}]})


@pytest.mark.parametrize(
    ("handler", "expected", "transient"),
    [
        (lambda request: httpx.Response(503, text="overloaded"), r"^[^:]* returned 503.$", True),
        # An endpoint's error may quote the passages it was sent: only its codes are kept.
        (
            lambda request: httpx.Response(
                400,
                json={
                    "error": {
                        "message": "Input 'the termination clause says ninety days' is too long",
                        "type": "invalid_request_error",
                    }
                },
            ),
            r"^Embedding endpoint returned 400 \(error code invalid_request_error\).$",
            False,
        ),
        (lambda request: httpx.Response(429, text="slow down"), "returned 429", True),
        (lambda request: httpx.Response(401, text="bad key"), "returned 401", False),
        # Ollama's answer for a model that makes no embeddings: asking again will not help.
        (
            lambda request: httpx.Response(
                501, json={"error": {"message": "does not support embeddings", "type": "api_error"}}
            ),
            r"returned 501 \(error code api_error\).$",
            False,
        ),
        (refuse, "unreachable at http://embeddings.", True),
        (stall, "at http://embeddings did not answer within 120 seconds.", True),
        (lambda request: httpx.Response(200, json={"nope": []}), "unexpected response", False),
        (
            lambda request: httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.5]}]}),
            "EMBEDDING_DIM",
            False,
        ),
    ],
)
def test_endpoint_failures_say_whether_to_ask_again(handler, expected, transient):
    with pytest.raises(EmbeddingError, match=expected) as error:
        embedder(handler).embed_query("ping")
    assert error.value.transient is transient


def test_the_endpoints_address_is_named_without_its_credentials():
    # The message is logged and shown on the status line; a user and password in
    # EMBEDDING_BASE_URL stay out of both.
    embedder = Embedder(get_settings())
    embedder._client = httpx.Client(
        base_url="https://bob:s3cret@embeddings.example.com/v1?key=k3y",
        transport=httpx.MockTransport(refuse),
    )
    with pytest.raises(EmbeddingError) as error:
        embedder.embed_query("ping")
    assert (
        str(error.value)
        == "Embedding endpoint unreachable at https://embeddings.example.com/v1?key=…."
    )


def test_the_tokens_the_endpoint_reports_are_counted_batch_by_batch():
    # A paid endpoint bills each request it answered, so a run that fails at its third batch
    # has paid for two.
    answered: list[int] = []

    def billing(request: httpx.Request) -> httpx.Response:
        if len(answered) == 2:
            return httpx.Response(503, text="overloaded")
        inputs = json.loads(request.content)["input"]
        answered.append(len(inputs))
        data = [
            {"index": i, "embedding": [0.5] * get_settings().embedding_dim}
            for i in range(len(inputs))
        ]
        return httpx.Response(200, json={"data": data, "usage": {"prompt_tokens": 7 * len(inputs)}})

    usage = ModelUsage()
    with pytest.raises(EmbeddingError, match="returned 503"):
        embedder(billing).embed_documents(["passage"] * (BATCH_SIZE * 2 + 1), usage)
    assert answered == [BATCH_SIZE, BATCH_SIZE]
    # Tokens under the embedding model's name, and no model call: the texts are no request
    # to a model, and the stage counts them itself.
    model = get_settings().embedding_model
    assert usage.models == {model: Calls(input_tokens=7 * BATCH_SIZE * 2)}

    # An endpoint that says nothing about tokens adds none; the texts are still counted by
    # the stage that sent them.
    usage = ModelUsage()
    embedder(vector).embed_documents(["passage"], usage)
    assert usage == ModelUsage()


# The check behind the status line, which every open tab polls.


def test_what_the_check_learnt_is_kept_for_a_minute(monkeypatch):
    asked: list[httpx.Request] = []
    endpoint = [refuse]

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        return endpoint[0](request)

    checked = embedder(handler)
    unreachable = "Embedding endpoint unreachable at http://embeddings."
    assert checked.check() == unreachable
    endpoint[0] = vector
    assert checked.check() == unreachable
    assert len(asked) == 1

    monkeypatch.setattr(embeddings, "CHECK_SECONDS", 0)
    assert checked.check() is None
    assert len(asked) == 2


def test_the_start_s_question_answers_the_check_for_a_minute():
    # A process asks at start (app/startup.py); the status line opened right after says what
    # it learnt rather than asking again.
    asked: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        return refuse(request)

    checked = embedder(handler)
    with pytest.raises(EmbeddingError, match="unreachable"):
        checked.probe()
    assert checked.check() == "Embedding endpoint unreachable at http://embeddings."
    assert len(asked) == 1


def test_threads_that_check_at_once_wait_for_one_request():
    asked: list[httpx.Request] = []
    answering = threading.Event()
    answer = threading.Event()

    def slow(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        answering.set()
        answer.wait(5)
        return vector(request)

    checked = embedder(slow)
    problems: list[str | None] = []
    threads = [threading.Thread(target=lambda: problems.append(checked.check())) for _ in range(5)]
    for thread in threads:
        thread.start()
    assert answering.wait(5)
    answer.set()
    for thread in threads:
        thread.join(5)
    assert problems == [None] * 5
    assert len(asked) == 1


def test_the_check_waits_ten_seconds_not_two_minutes():
    # An endpoint stuck for TIMEOUT would hold the status line, and every request waiting for
    # the check, that long; a single word is answered in well under a second when it works.
    timeouts: list[float] = []

    def stalled(request: httpx.Request) -> httpx.Response:
        timeouts.append(request.extensions["timeout"]["read"])
        return stall(request)

    assert (
        embedder(stalled).check()
        == "Embedding endpoint at http://embeddings did not answer within 10 seconds."
    )
    assert timeouts == [10]
