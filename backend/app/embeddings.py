"""Embeddings from any OpenAI-compatible /embeddings endpoint (Ollama by default)."""

import httpx

from app.config import Settings
from app.errors import AppError, provider_codes
from app.logs import bare_url

BATCH_SIZE = 32
# Passages are far shorter; this only guards the model's context window.
MAX_INPUT_CHARS = 6000
# The statuses an endpoint answers with when it, not the request, is the problem for the moment.
TRANSIENT_STATUSES = {408, 429}
# How long one request may take, in seconds. A batch of passages embeds in seconds even on a
# small local machine, so only an endpoint that is stuck gets this far.
TIMEOUT = 120


class EmbeddingError(AppError):
    pass


class Embedder:
    def __init__(self, settings: Settings):
        self.model = settings.embedding_model
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

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), BATCH_SIZE):
            batch = [self._document_prefix + text for text in texts[start : start + BATCH_SIZE]]
            vectors.extend(self._embed(batch))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self._embed([self._query_prefix + text])[0]

    def check(self) -> str | None:
        """Return None when the endpoint works, else a short reason it does not."""
        try:
            self.embed_query("ping")
        except EmbeddingError as error:
            return str(error)
        return None

    def _embed(self, inputs: list[str]) -> list[list[float]]:
        payload = {
            "model": self.model,
            "input": [text[:MAX_INPUT_CHARS] for text in inputs],
        }
        try:
            response = self._client.post("/embeddings", json=payload)
            response.raise_for_status()
            data = response.json()["data"]
        except httpx.HTTPStatusError as error:
            # Told by its status and codes: the body of an endpoint's error may quote the
            # passages it was sent, and the message reaches the log (app/errors.py).
            status = error.response.status_code
            codes = provider_codes(_json_or_none(error.response))
            detail = f" (error code {', '.join(codes)})" if codes else ""
            raise EmbeddingError(
                f"Embedding endpoint returned {status}{detail}.",
                transient=status in TRANSIENT_STATUSES or status >= 500,
            ) from error
        except httpx.TimeoutException as error:
            raise EmbeddingError(
                f"Embedding endpoint at {bare_url(str(self._client.base_url))} did not answer "
                f"within {TIMEOUT} seconds.",
                transient=True,
            ) from error
        except httpx.HTTPError as error:
            # Not reached, or the connection dropped: the endpoint may well be back in a while.
            raise EmbeddingError(
                f"Embedding endpoint unreachable at {bare_url(str(self._client.base_url))}.",
                transient=True,
            ) from error
        except (KeyError, ValueError) as error:
            raise EmbeddingError("Embedding endpoint returned an unexpected response.") from error

        vectors = [item["embedding"] for item in sorted(data, key=lambda item: item["index"])]
        if len(vectors) != len(inputs):
            raise EmbeddingError("Embedding endpoint returned the wrong number of vectors.")
        if vectors and len(vectors[0]) != self.dim:
            raise EmbeddingError(
                f"{self.model} returns {len(vectors[0])}-dimensional vectors but EMBEDDING_DIM "
                f"is {self.dim}."
            )
        return vectors


def _json_or_none(response: httpx.Response) -> object:
    try:
        return response.json()
    except ValueError:
        return None
