"""Model access for indexing (structured outputs, OCR) and the agent (streaming with tools).

Requests go through Pydantic AI, which speaks each provider's API, so `INDEX_MODEL` and
`AGENT_MODEL` can name any model it supports as `provider:model`.
"""

import asyncio
import logging
import queue
import re
import threading
import time
from collections.abc import Coroutine, Iterator, Sequence
from concurrent.futures import Future
from typing import Any, Literal, TypeVar

import httpx
import httpx2
import pydantic_ai
from google.genai.types import HttpRetryOptions
from pydantic import BaseModel
from pydantic_ai import Agent, BinaryContent, NativeOutput
from pydantic_ai.direct import model_request_stream
from pydantic_ai.exceptions import (
    ContentFilterError,
    ModelAPIError,
    ModelHTTPError,
    UnexpectedModelBehavior,
    UserError,
)
from pydantic_ai.messages import (
    InstructionPart,
    ModelMessage,
    ModelResponse,
    ModelResponseStreamEvent,
    UserContent,
)
from pydantic_ai.models import Model, ModelRequestParameters, infer_model
from pydantic_ai.providers import Provider, infer_provider
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.tools import ToolDefinition

from app.config import Settings
from app.errors import AppError
from app.usage import ModelUsage

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Output cap for one indexing reply. Agent replies use each model's own limit.
INDEX_MAX_TOKENS = 16000
# How long one indexing request may take, in seconds: a window analysis or an OCR page with
# thinking legitimately runs a minute or three. A request that outlives it is cancelled and
# fails as transient, so the stage is retried and a provider that hangs cannot hold a worker
# thread for good.
INDEX_TIMEOUT = 300
# How long one agent turn may take, and the longest silence inside it, in seconds. A turn that
# outlives either is cancelled and the chat shows the error; a provider that hangs would
# otherwise hold a request thread for good.
AGENT_TIMEOUT = 120
STREAM_STALL = 60
# Attempts at one Gemini request, the first included.
GOOGLE_ATTEMPTS = 3
# The statuses a provider answers with when it, not the request, is the problem for the moment.
TRANSIENT_STATUSES = {408, 429}

# The library greets its first run with a banner; this process keeps its output to its log.
pydantic_ai.BANNER_ENABLED = False

INDEX_SYSTEM = (
    "You index business documents for a retrieval system. Other models rely on what you "
    "write to find information, so be precise and literal: report what the text says, "
    "never what it might imply."
)


class LLMError(AppError):
    pass


class LLMUnavailable(LLMError):
    pass


class OcrBlock(BaseModel):
    kind: Literal["heading", "paragraph", "list_item", "table"]
    level: int | None
    text: str


class OcrPage(BaseModel):
    blocks: list[OcrBlock]


OCR_PROMPT = (
    "Transcribe this page into blocks in reading order. Copy the text exactly as printed; "
    "do not correct, summarise or translate it. Use kind heading (with level 1 for the "
    "largest headings), paragraph, list_item, or table (the table as a Markdown pipe table "
    "in text). For every kind except heading set level to null. Leave out page numbers and "
    "running headers or footers. If the page has no text, return an empty list."
)


def describe_error(error: Exception) -> str:
    """A short message, safe to show a user, for a failed model request."""
    if isinstance(error, ModelHTTPError):
        model = error.model_name
        if error.status_code == 401:
            message = f"The API key for {model} was rejected."
        elif error.status_code == 403:
            message = f"This API key is not allowed to use {model}."
        elif error.status_code == 404:
            message = f"The model {model} was not found. Check AGENT_MODEL and INDEX_MODEL."
        elif error.status_code == 429:
            message = f"The rate limit for {model} was reached. Try again shortly."
        else:
            message = f"The request to {model} failed ({error.status_code})."
        detail = _provider_message(error.body)
        return f"{message} The provider said: {detail}" if detail else message
    if isinstance(error, ContentFilterError):
        return "The model declined to process this content."
    if isinstance(error, UnexpectedModelBehavior):
        return "The model's reply was cut off or malformed."
    if _timed_out(error):
        return "The model took too long to answer. Try again."
    if isinstance(error, ModelAPIError):
        return f"Could not reach the provider of {error.model_name}."
    if isinstance(error, UserError):
        return error.message
    return "The model request failed unexpectedly."


def _provider_message(body: object) -> str:
    """The provider's own words from an error body, whose shape differs by provider."""
    if isinstance(body, dict):
        error = body.get("error", body)
        body = error.get("message") if isinstance(error, dict) else error
    return str(body)[:300] if body else ""


def _failure(error: Exception) -> LLMError:
    """The app's error for a failed request. The cause, which may be long, goes to the log."""
    if isinstance(error, LLMError):
        return error
    expected = isinstance(error, ModelAPIError | UnexpectedModelBehavior | UserError)
    expected = expected or _timed_out(error)
    log.warning("Model request failed: %.500s", error, exc_info=None if expected else error)
    return LLMError(describe_error(error), transient=_transient(error))


def _transient(error: Exception) -> bool:
    """Whether the same request may succeed in a while: the provider was overloaded, rate
    limiting or out of reach, rather than refusing the key, the model or the request."""
    if isinstance(error, ModelHTTPError):
        return error.status_code in TRANSIENT_STATUSES or error.status_code >= 500
    return isinstance(error, ModelAPIError) or _timed_out(error)


def _timed_out(error: BaseException | None) -> bool:
    """Whether a request ended because the provider took too long. Each SDK reports that in an
    exception of its own, raised from the HTTP library's, and Google's lets the library's
    through as it is; the chain of causes tells for all of them."""
    while error is not None:
        if isinstance(error, TimeoutError | httpx.TimeoutException | httpx2.TimeoutException):
            return True
        error = error.__cause__
    return False


def _give_up(future: Future[Any], message: str) -> LLMError:
    """Stop a request the app has waited long enough for, and the error to raise in its place."""
    future.cancel()
    log.warning("Model request given up: %s", message)
    return LLMError(f"{message} Try again.", transient=True)


def _setup_problem(name: str, error: Exception, settings: Settings) -> str:
    """What stops the model `name` from being set up, said as the step that fixes it."""
    message = str(error)
    if variable := re.search(r"Set the `(\w+)` environment variable", message):
        # Only a development process reads its keys from `.env`; a production one is given
        # them by its deployment, so the fix is to be found there.
        where = ".env" if settings.environment == "development" else "the environment"
        return f"Set {variable[1]} in {where} to use {name}."
    if extra := re.search(r"pydantic-ai-slim\[[\w-]+\]", message):
        return (
            f"{name} needs a package that is not installed. Run `uv add '{extra[0]}'` in backend/."
        )
    return f"{name!r} cannot be used. {message}"


def _provider(name: str) -> Provider[Any]:
    """The provider behind a model-name prefix, reading its key from the environment."""
    if name == "google":
        # Google's SDK gives up at the first rate-limit or overload response unless told to
        # retry. Anthropic's and OpenAI's retry twice by default.
        return GoogleProvider(retry_options=HttpRetryOptions(attempts=GOOGLE_ATTEMPTS))
    return infer_provider(name)


def _settings(effort: str, **settings: Any) -> dict[str, Any]:
    """Model settings for one request. An empty `effort` leaves the model's own default."""
    if effort:
        settings["thinking"] = effort
    return settings


_loop: asyncio.AbstractEventLoop | None = None
_loop_lock = threading.Lock()


def _submit[R](coroutine: Coroutine[Any, Any, R]) -> Future[R]:
    """Start `coroutine` on the model loop and return a handle any thread can wait on.

    Provider clients are async and stay tied to the event loop that first drives them, while
    the app calls the model from indexing and request threads. One loop, on its own thread,
    serves them all.
    """
    global _loop
    with _loop_lock:
        if _loop is None:
            _loop = asyncio.new_event_loop()
            threading.Thread(target=_loop.run_forever, name="model-loop", daemon=True).start()
    return asyncio.run_coroutine_threadsafe(coroutine, _loop)


_END = object()


class ModelStream:
    """One streamed model turn: a context manager to iterate for events, then ask for `response()`.

    The request runs on the model loop and passes its events through a queue, so the stream
    can be read from any thread, even a different one for each event.
    """

    def __init__(
        self,
        model: Model,
        messages: Sequence[ModelMessage],
        settings: dict[str, Any],
        parameters: ModelRequestParameters,
    ):
        self._model = model
        self._messages = list(messages)
        self._settings = settings
        self._parameters = parameters
        self._events: queue.SimpleQueue[Any] = queue.SimpleQueue()
        self._future: Future[ModelResponse] | None = None
        self._deadline = 0.0
        self._ended = False

    def __enter__(self) -> "ModelStream":
        self._deadline = time.monotonic() + AGENT_TIMEOUT
        self._future = _submit(self._pump())
        return self

    def __exit__(self, *exc_info: object) -> None:
        # Stops a request that is still running, as when the reader went away mid-answer.
        self._future.cancel()

    def __iter__(self) -> Iterator[ModelResponseStreamEvent]:
        while not self._ended:
            event = self._next()
            if event is _END:
                self._ended = True
            else:
                yield event
        self._reply()  # a failed request raises here, as the stream ends

    def response(self) -> ModelResponse:
        """The whole reply, once the stream has ended."""
        for _ in self:
            pass
        return self._reply()

    def _next(self) -> Any:
        """The next event, or `_END`. Waits at most `STREAM_STALL`, and no longer than the turn
        has left: a model that has gone quiet, or has run too long, is given up on."""
        remaining = self._deadline - time.monotonic()
        wait = min(STREAM_STALL, remaining)
        try:
            return self._events.get(timeout=max(wait, 0.0))
        except queue.Empty:
            if wait < STREAM_STALL:
                message = f"The model did not finish within {AGENT_TIMEOUT:g} seconds."
            else:
                message = f"The model sent nothing for {STREAM_STALL:g} seconds."
            raise _give_up(self._future, message) from None

    def _reply(self) -> ModelResponse:
        # Having sent `_END`, the pump has nothing left to wait for, so this returns at once.
        try:
            return self._future.result()
        except Exception as error:
            raise _failure(error) from error

    async def _pump(self) -> ModelResponse:
        try:
            async with model_request_stream(
                self._model,
                self._messages,
                model_settings=self._settings,
                model_request_parameters=self._parameters,
            ) as stream:
                async for event in stream:
                    self._events.put(event)
                return stream.get()
        finally:
            self._events.put(_END)


class LLM:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._models: dict[str, Model] = {}
        self._lock = threading.Lock()

    def problem(self) -> str | None:
        """What stops the configured models from being used, or None when nothing does."""
        try:
            for name in (self._settings.index_model, self._settings.agent_model):
                self._model(name)
        except LLMUnavailable as error:
            return str(error)
        return None

    @property
    def available(self) -> bool:
        return self.problem() is None

    def _model(self, name: str) -> Model:
        """The adapter for a `provider:model` name, built on first use."""
        with self._lock:
            if name not in self._models:
                try:
                    self._models[name] = infer_model(name, _provider)
                except Exception as error:
                    # An unknown name, a missing key, or a provider whose package is absent.
                    raise LLMUnavailable(_setup_problem(name, error, self._settings)) from error
            return self._models[name]

    def structured(
        self,
        content: str | Sequence[UserContent],
        schema: type[T],
        usage: ModelUsage | None = None,
    ) -> T:
        """One indexing request whose reply is validated against `schema`. What it cost is
        added to `usage` once it is answered (app/usage.py)."""
        model = self._model(self._settings.index_model)
        # A provider that can hold its reply to a JSON schema is asked to. For the rest,
        # Pydantic AI requests the schema as a tool call instead.
        constrained = model.profile.get("supports_json_schema_output", False)
        agent = Agent(
            model,
            output_type=NativeOutput(schema) if constrained else schema,
            instructions=INDEX_SYSTEM,
            model_settings=_settings(
                self._settings.index_effort, max_tokens=INDEX_MAX_TOKENS, timeout=INDEX_TIMEOUT
            ),
        )
        future = _submit(agent.run(content))
        try:
            # The SDK's timeout above bounds each HTTP request; this bounds the whole call,
            # which may make several of them when the provider retries.
            result = future.result(timeout=INDEX_TIMEOUT)
        except TimeoutError:
            message = f"The model did not answer within {INDEX_TIMEOUT:g} seconds."
            raise _give_up(future, message) from None
        except Exception as error:
            raise _failure(error) from error
        if usage is not None:
            # Every request of the run: the provider's reply that failed validation and was
            # asked again was paid for too.
            usage.add(result.usage)
        return result.output

    def ocr_page(
        self, image: bytes, media_type: str, usage: ModelUsage | None = None
    ) -> list[OcrBlock]:
        """Transcribe one scanned page or image into blocks."""
        content = [BinaryContent(image, media_type=media_type), OCR_PROMPT]
        return self.structured(content, OcrPage, usage).blocks

    def stream_agent(
        self, instructions: str, messages: Sequence[ModelMessage], tools: Sequence[ToolDefinition]
    ) -> ModelStream:
        """Open one streamed agent turn; use as a context manager."""
        return ModelStream(
            self._model(self._settings.agent_model),
            messages,
            # Anthropic caches a prompt prefix only when asked, and the loop resends a growing
            # one every turn. Other providers ignore the setting. The SDK's timeout is the HTTP
            # library's, which for a stream is per read, so it is the longest pause allowed; the
            # whole turn is bounded by `ModelStream`.
            _settings(self._settings.agent_effort, anthropic_cache=True, timeout=STREAM_STALL),
            ModelRequestParameters(
                function_tools=list(tools),
                instruction_parts=[InstructionPart(content=instructions)],
            ),
        )
