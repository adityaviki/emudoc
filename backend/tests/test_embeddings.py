"""The embedder's errors: what the endpoint answered, in words, and whether asking again in a
while may help, which the pipeline reads to run the embed stage again."""

import httpx
import pytest

from app.config import get_settings
from app.embeddings import Embedder, EmbeddingError


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
