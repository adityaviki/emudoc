"""Embeddings from any OpenAI-compatible /embeddings endpoint (Ollama by default)."""

import threading
import time

import httpx
from pydantic_ai.usage import RunUsage

from app.config import Settings
from app.errors import AppError, provider_codes
from app.logs import bare_url
from app.usage import ModelUsage

BATCH_SIZE = 32
# Passages are far shorter; this only guards the model's context window.
MAX_INPUT_CHARS = 6000
# The statuses an endpoint answers with when it, not the request, is the problem for the moment:
# these and the 5xx family, but for 501, which says the endpoint cannot do this at all (Ollama's
# answer for a model that makes no embeddings).
TRANSIENT_STATUSES = {408, 429}
NOT_IMPLEMENTED = 501
# How long one request may take, in seconds. A batch of passages embeds in seconds even on a
# small local machine, so only an endpoint that is stuck gets this far.
TIMEOUT = 120
# How long the answer of `check` is kept, in seconds, and how long its request may take. The
# status line asks on every page a member opens and every minute while it stays open, in every
# tab; kept, that is one request a minute per process, however many ask. And the request is a
# single word, which an endpoint that works answers in well under the limit: one that takes
# longer is reported as not answering rather than holding the status line, and every thread
# waiting on it, for TIMEOUT.
CHECK_SECONDS = 60
CHECK_TIMEOUT = 10


class EmbeddingError(AppError):
    pass


class Embedder:
    def __init__(self, settings: Settings):
        self.model = settings.embedding_model
        # Where the endpoint is, as the log and the start's messages may name it.
        self.address = bare_url(settings.embedding_base_url)
        self.dim = settings.embedding_dim
        self._query_prefix = settings.embedding_query_prefix
        self._document_prefix = settings.embedding_document_prefix
        headers = {}
        if settings.embedding_api_key:
            headers["Authorization"] = f"Bearer {settings.embedding_api_key}"
        self._client = httpx.Client(
            base_url=settings.embedding_base_url.rstrip("/"),
            headers=headers,
            timeout=TIMEOUT,
        )
        # When `check` last asked and what it learnt, and the lock that makes the threads that
        # ask meanwhile wait for that one request rather than send their own.
        self._checked: tuple[float, str | None] | None = None
        self._checking = threading.Lock()

    def embed_documents(
        self, texts: list[str], usage: ModelUsage | None = None
    ) -> list[list[float]]:
        """The vectors of `texts`, in order. The tokens the endpoint says each batch took are
        added to `usage` as the batch is answered, so a run that fails halfway has counted
        the batches it was sent; an endpoint that says nothing adds nothing."""
        vectors: list[list[float]] = []
        for start in range(0, len(texts), BATCH_SIZE):
            batch = [self._document_prefix + text for text in texts[start : start + BATCH_SIZE]]
            vectors.extend(self._embed(batch, usage=usage))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self._embed([self._query_prefix + text])[0]

    def check(self) -> str | None:
        """Return None when the endpoint works, else a short reason it does not, as learnt at
        most CHECK_SECONDS ago."""
        with self._checking:
            if self._checked is None or time.monotonic() - self._checked[0] >= CHECK_SECONDS:
                try:
                    self.probe()
                except EmbeddingError:
                    pass
            return self._checked[1]

    def probe(self) -> None:
        """Ask the endpoint for the vector of one word, as a search would, waiting CHECK_TIMEOUT
        at most; raise `EmbeddingError` when that fails, with `transient` saying whether it may
        pass by itself (app/startup.py refuses a deployment whose endpoint cannot). What it
        learns is what `check` says for the next CHECK_SECONDS, so the start's question answers
        the status line's first."""
        try:
            self._embed([self._query_prefix + "ping"], timeout=CHECK_TIMEOUT)
        except EmbeddingError as error:
            self._checked = (time.monotonic(), str(error))
            raise
        self._checked = (time.monotonic(), None)

    def _embed(
        self, inputs: list[str], timeout: float = TIMEOUT, usage: ModelUsage | None = None
    ) -> list[list[float]]:
        payload = {
            "model": self.model,
            "input": [text[:MAX_INPUT_CHARS] for text in inputs],
        }
        try:
            response = self._client.post("/embeddings", json=payload, timeout=timeout)
            response.raise_for_status()
            body = response.json()
            data = body["data"]
        except httpx.HTTPStatusError as error:
            # Told by its status and codes: the body of an endpoint's error may quote the
            # passages it was sent, and the message reaches the log (app/errors.py).
            status = error.response.status_code
            codes = provider_codes(_json_or_none(error.response))
            detail = f" (error code {', '.join(codes)})" if codes else ""
            raise EmbeddingError(
                f"Embedding endpoint returned {status}{detail}.",
                transient=status in TRANSIENT_STATUSES
                or (status >= 500 and status != NOT_IMPLEMENTED),
            ) from error
        except httpx.TimeoutException as error:
            raise EmbeddingError(
                f"Embedding endpoint at {bare_url(str(self._client.base_url))} did not answer "
                f"within {timeout:g} seconds.",
                transient=True,
            ) from error
        except httpx.HTTPError as error:
            # Not reached, or the connection dropped: the endpoint may well be back in a while.
            raise EmbeddingError(
                f"Embedding endpoint unreachable at {bare_url(str(self._client.base_url))}.",
                transient=True,
            ) from error
        except (KeyError, TypeError, ValueError) as error:
            raise EmbeddingError("Embedding endpoint returned an unexpected response.") from error

        vectors = [item["embedding"] for item in sorted(data, key=lambda item: item["index"])]
        if len(vectors) != len(inputs):
            raise EmbeddingError("Embedding endpoint returned the wrong number of vectors.")
        if vectors and len(vectors[0]) != self.dim:
            raise EmbeddingError(
                f"{self.model} returns {len(vectors[0])}-dimensional vectors but EMBEDDING_DIM "
                f"is {self.dim}."
            )
        if usage is not None and (tokens := _tokens(body)):
            # Tokens without a model call: an embedding is priced for what it was sent alone.
            usage.add(self.model, RunUsage(input_tokens=tokens))
        return vectors


def _tokens(body: dict) -> int:
    """The tokens the endpoint says the request took, as OpenAI's API, Ollama and
    text-embeddings-inference report them; zero when it says nothing usable."""
    usage = body.get("usage")
    tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else None
    return tokens if isinstance(tokens, int) and tokens > 0 else 0


def _json_or_none(response: httpx.Response) -> object:
    try:
        return response.json()
    except ValueError:
        return None
