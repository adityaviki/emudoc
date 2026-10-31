"""Model access for indexing (structured outputs, OCR) and the agent (streaming with tools).

Requests go through Pydantic AI, which speaks each provider's API, so `INDEX_MODEL` and
`AGENT_MODEL` can name any model it supports as `provider:model`.

A provider named in DISABLED_PROVIDERS (app/config.py) is switched off: its models raise
`LLMUnavailable` before a request is made, as a missing key would, except that the problem is
a passing one by design. The app then runs as it does without a key, the status route says
why, and a production process starts regardless (app/startup.py), which is what makes the
setting a kill switch rather than a misconfiguration. An operator switches a provider off the
same way from the operator's area, without a restart (app/switches.py), which `LLM` asks
before each model it builds or uses; a provider is off while either names it.

Every request asks OpenRouter to forward it only to providers that neither store nor train on
what they are sent (`DATA_POLICY`), so that a model reached through it keeps the terms the app
promises its users; the other providers' terms come with the key (deploying.md).

A deployment that keeps its data in one region (DATA_REGION, app/residency.py) holds to it the
two providers that let a request choose: OpenRouter is reached at the region's own address
(`openrouter_url`), which sends a request only to providers in the region, and Claude is told to
run in the US when that is the region (`inference_geo`). The other providers are placed by their
own settings, which the start-up checks read.

Each kind of work may have a fallback (AGENT_FALLBACK_MODEL, INDEX_FALLBACK_MODEL): the model
asked when the first cannot answer because its provider is down, overloaded or out of reach, or
refuses the key, the account or the model itself, as it does a model it retired (`_falls_back`).
It is not asked when the request was refused, since another model would refuse it too or it
hides a fault of the app's, nor when a reply took too long. A model that failed so is asked
after its fallback for PASS_OVER_SECONDS, so that a provider that is down costs one request a
minute its retries rather than every request. Each model is asked with the settings of its own
provider (Claude's `inference_geo` goes to Claude alone), which is why the app tries them itself
rather than through Pydantic AI's FallbackModel, which sends one set to all. What a fallback
answered is counted under its own name and price (app/usage.py), and is logged with what made
the first model fail.
"""

import asyncio
import logging
import queue
import re
import threading
import time
from collections.abc import Callable, Collection, Coroutine, Iterator, Sequence
from concurrent.futures import Future
from functools import partial
from typing import Any, Literal, TypeVar

import httpx
import httpx2
import pydantic_ai
from google.auth.exceptions import DefaultCredentialsError
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
from pydantic_ai.providers.google_cloud import GoogleCloudProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import RequestUsage, RunUsage

from app.config import AGENT, INDEX, ModelSetting, Settings, model_provider
from app.errors import AppError, provider_codes
from app.metrics import MODEL_FALLBACKS, model_request
from app.prompts import prompt_version
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
# The statuses by which a provider refuses the key, the account or the model rather than the
# request (a model it retired is not found): another model, best another provider's, may well
# answer what this one will not, so a fallback is asked.
REFUSALS = frozenset({401, 402, 403, 404})
# How long, in seconds, a model that failed in a way that asks its fallback is asked after it.
PASS_OVER_SECONDS = 60
# What OpenRouter is told about where it may send a request: only to providers of the model that
# keep no data and train on none (OpenRouter's `data_collection`). Its default allows providers
# that store prompts and may train on them; a model that no such provider serves fails with
# OpenRouter's own sentence rather than reach one.
DATA_POLICY = {"data_collection": "deny"}
# OpenRouter's address when no region is asked for.
OPENROUTER_URL = "https://openrouter.ai/api/v1"
# The codes by which providers say the key is wrong (Anthropic, OpenAI, Google's 400) or the
# account has nothing left to pay with (OpenAI), whatever the status.
KEY_REJECTED = frozenset({"authentication_error", "invalid_api_key", "API_KEY_INVALID"})
NO_CREDIT = frozenset({"insufficient_quota"})
# What a deployment's members are told of a model that failed for a reason of the deployment's
# (its key, its account, its settings, its provider being down or switched off), and of one
# that refused the request itself, in place of a sentence that names the model or a setting
# (`_told`). The operator reads which model and why in the log and the alerts.
MODEL_UNAVAILABLE = "The AI model is unavailable just now. Try again later."
MODEL_FAILED = "The AI model could not answer this request."

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
    """The model cannot be used as the deployment is set up, or is switched off, which every
    message says by its name and the setting to change."""

    members = MODEL_UNAVAILABLE


class OcrBlock(BaseModel):
    kind: Literal["heading", "paragraph", "list_item", "table"]
    level: int | None
    text: str


class OcrPage(BaseModel):
    blocks: list[OcrBlock]


# A scan is read as the model sees it: a page photographed sideways is not turned first, a form
# filled in by hand is read as it was written, and a table without rules is still a table.
OCR_PROMPT = (
    "Transcribe this page into blocks in reading order, a page set in columns a column at a "
    "time. If the page is turned sideways or upside down, read it the way its text runs. Copy "
    "the text exactly as it stands, printed or handwritten; do not correct, summarise or "
    "translate it. Write a word you cannot read as [illegible], a signature as [signature] "
    "followed by the name if it can be read, a ticked box as [x] and an empty one as [ ]. Use "
    "kind heading (with level 1 for the largest headings), paragraph, list_item, or table. A "
    "table, ruled or not, is one block whose text is a Markdown pipe table: its header row "
    "first, then a row a line, a cell the page leaves empty left empty, a cell's lines joined "
    "with a space, and a cell that spans columns written in the first of them. For every kind "
    "except heading set level to null. Leave out page numbers and running headers or footers. "
    "If the page has no text, return an empty list."
)
# The version of what a page is asked (app/prompts.py), which the `ocr` usage rows carry.
OCR_PROMPT_VERSION = prompt_version(INDEX_SYSTEM, OCR_PROMPT, OcrPage)


def describe_error(error: Exception) -> str:
    """A short message for a failed model request, safe to log and to show a developer; a
    deployment's members are told `_told`'s sentence instead where this one names the model.

    A provider's error is told by its status and the codes in its body (`provider_codes`), not
    by its words: those may echo what was sent (OpenRouter's moderation quotes the flagged
    text), show part of the key (OpenAI's 401), or name the platform's account with the
    provider, and the message reaches the log and the operator's digest as well as development's
    members.
    """
    if isinstance(error, ModelHTTPError):
        model = error.model_name
        status = error.status_code
        codes = provider_codes(error.body)
        if status == 401 or KEY_REJECTED & set(codes):
            message = f"The API key for {model} was rejected."
        elif status == 403:
            message = f"This API key is not allowed to use {model}."
        elif status == 404:
            message = (
                f"The model {model} was not found, or is served no longer. Check AGENT_MODEL, "
                "INDEX_MODEL and their fallbacks."
            )
        elif status == 402 or NO_CREDIT & set(codes):
            message = f"The account that pays for {model} has no credit left."
        elif status == 429:
            message = f"The rate limit for {model} was reached. Try again shortly."
        else:
            message = f"The request to {model} failed ({status})."
        return f"{message} The provider's error code: {', '.join(codes)}." if codes else message
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


def _failure(error: Exception, earlier: Sequence[Exception] = ()) -> LLMError:
    """The app's error for a failed request. The log gets the same sentence and the kind of
    error, and the traceback of an unexpected one without its message (app/logs.py): the
    message of a malformed reply carries the reply, and a provider's body may echo the request.

    `earlier` holds what the models asked before failed with, each logged as its fallback was
    asked (`LLM._fell_back`). The error then says every model's failure, in the order they were
    asked, and is transient when any was: the model that is down for now may answer the next
    attempt, though its fallback refused the key.
    """
    if not isinstance(error, LLMError):
        expected = isinstance(error, ModelAPIError | UnexpectedModelBehavior | UserError)
        expected = expected or _timed_out(error)
        log.warning(
            "Model request failed (%s): %s",
            type(error).__name__,
            describe_error(error),
            exc_info=None if expected else error,
        )
    failures = [*earlier, error]
    if len(failures) == 1 and isinstance(error, LLMError):
        return error
    message = " ".join(_said(failure) for failure in failures)
    transient = any(_passing(failure) for failure in failures)
    # Every model could not be set up or is switched off: the work is unavailable, as for one.
    unavailable = all(isinstance(failure, LLMUnavailable) for failure in failures)
    # The members are told why the last model asked failed, which is why the work did; with
    # several models the message names each of them, whatever their sentences say.
    members = _told(error) or (_said(error) if len(failures) > 1 else None)
    return (LLMUnavailable if unavailable else LLMError)(
        message, transient=transient, members=members
    )


def _said(error: Exception) -> str:
    """The sentence a failure is told by: the app's own error says it, a provider's is
    described (`describe_error`)."""
    return str(error) if isinstance(error, LLMError) else describe_error(error)


def _told(error: Exception) -> str | None:
    """What a deployment's members are told of a failure in place of its sentence (`_said`),
    or None when that names nothing of the deployment: a content filter, a malformed reply and
    a timeout are said without the model's name. A provider that refused the key, the account
    or the model, or was out of reach, is the deployment's to fix or wait out; one that refused
    the request is said to have, without its status."""
    if isinstance(error, LLMError):
        return error.members
    if isinstance(error, ModelHTTPError):
        return MODEL_UNAVAILABLE if _falls_back(error) else MODEL_FAILED
    if _timed_out(error) or not isinstance(error, ModelAPIError | UserError):
        return None
    return MODEL_UNAVAILABLE


def _passing(error: Exception) -> bool:
    return error.transient if isinstance(error, LLMError) else _transient(error)


def _falls_back(error: Exception) -> bool:
    """Whether the next model is asked the request this error ended: the model could not be
    set up or is switched off, or its provider was down, overloaded or out of reach, or refused
    the key, the account or the model, as it does a model it retired. Not for a request it
    refused (a 400 and the like, a content filter, a reply that would not validate), which the
    next model would refuse too or which hides a fault of the app's, nor for one that took too
    long, as a document too large for the time would on the next model as well."""
    if isinstance(error, LLMUnavailable):
        return True
    if _timed_out(error):
        return False
    if isinstance(error, ModelHTTPError):
        codes = set(provider_codes(error.body))
        return (
            _transient(error)
            or error.status_code in REFUSALS
            or bool((KEY_REJECTED | NO_CREDIT) & codes)
        )
    return isinstance(error, ModelAPIError)


def _transient(error: Exception) -> bool:
    """Whether the same request may succeed in a while: the provider was overloaded, rate
    limiting or out of reach, rather than refusing the key, the model or the request."""
    if isinstance(error, ModelHTTPError):
        return error.status_code in TRANSIENT_STATUSES or error.status_code >= 500
    return isinstance(error, ModelAPIError) or _timed_out(error)


def _timed_out(error: BaseException | None) -> bool:
    """Whether a request ended because the provider took too long. Each adapter reports that in
    a `ModelAPIError` raised from the SDK's exception or the HTTP library's (Google's let the
    library's through as it was before Pydantic AI 2.55); the chain of causes tells for all."""
    while error is not None:
        if isinstance(error, TimeoutError | httpx.TimeoutException | httpx2.TimeoutException):
            return True
        error = error.__cause__
    return False


def _outcome(error: BaseException) -> str:
    """How the metrics call the way a request ended (app/metrics.py): the provider's HTTP
    status when it answered with one, else a word for what came instead."""
    if isinstance(error, ModelHTTPError):
        return str(error.status_code)
    if isinstance(error, ContentFilterError):
        return "filtered"
    if isinstance(error, UnexpectedModelBehavior):
        return "malformed"
    # The app's own error inside a request is the one it raises when it stops waiting.
    if _timed_out(error) or isinstance(error, LLMError):
        return "timeout"
    if isinstance(error, ModelAPIError):
        return "unreachable"
    if isinstance(error, asyncio.CancelledError):
        return "stopped"
    return "failed"


def _give_up(future: Future[Any], message: str) -> LLMError:
    """Stop a request the app has waited long enough for, and the error to raise in its place."""
    future.cancel()
    log.warning("Model request given up: %s", message)
    return LLMError(f"{message} Try again.", transient=True)


def _switched_off(name: str, provider: str) -> str:
    """What stops the model `name` while DISABLED_PROVIDERS names its provider."""
    return (
        f"{name} is switched off: DISABLED_PROVIDERS names its provider, {provider}. Remove it "
        "there and restart the app to use the model again."
    )


def _switched_off_by_operator(name: str, provider: str) -> str:
    """What stops the model `name` while an operator has its provider switched off."""
    return (
        f"{name} is switched off: an operator switched its provider, {provider}, off in the "
        "operator's area. Switch it on there to use the model again."
    )


def _setup_problem(name: str, error: Exception, settings: Settings) -> str:
    """What stops the model `name` from being set up, said as the step that fixes it."""
    message = str(error)
    if variable := re.search(r"Set the `(\w+)` environment variable", message):
        # Only a development process reads its keys from `.env`; a production one is given
        # them by its deployment, so the fix is to be found there.
        where = ".env" if settings.environment == "development" else "the environment"
        return f"Set {variable[1]} in {where} to use {name}."
    if isinstance(error, DefaultCredentialsError):
        # Vertex AI signs in with a Cloud identity, not a key: a service account's key file, or
        # the one the machine runs as.
        where = ".env" if settings.environment == "development" else "the environment"
        return (
            "Set GOOGLE_APPLICATION_CREDENTIALS to a service account's key file and "
            f"GOOGLE_CLOUD_PROJECT to its project in {where} to use {name}."
        )
    if extra := re.search(r"pydantic-ai-slim\[[\w-]+\]", message):
        return (
            f"{name} needs a package that is not installed. Run `uv add '{extra[0]}'` in backend/."
        )
    return f"{name!r} cannot be used. {message}"


def inference_geo(name: str, region: str) -> str | None:
    """Where Claude is told to run a request for the model `name` when the deployment keeps its
    data in `region` (DATA_REGION): in the US for the US, the one region Anthropic's API offers
    besides anywhere. None leaves it to the workspace's default, and is all there is for any
    other provider or region; app/residency.py refuses a Claude model under the EU."""
    return "us" if region == "us" and model_provider(name) == "anthropic" else None


def openrouter_url(region: str) -> str:
    """Where OpenRouter is reached for a deployment that keeps its data in `region`: the
    region's own address, which forwards a request only to providers in the region and fails it
    when none serves the model, or the usual one without a region."""
    return f"https://{region}.openrouter.ai/api/v1" if region else OPENROUTER_URL


class _RegionalOpenRouter(OpenRouterProvider):
    """OpenRouter at a region's address (`openrouter_url`), which the library has no setting
    for: it builds its client from `base_url`, so the address is set before that happens."""

    def __init__(self, region: str):
        self._region = region
        super().__init__()

    @property
    def base_url(self) -> str:
        return openrouter_url(self._region)


def _provider(name: str, *, region: str = "") -> Provider[Any]:
    """The provider behind a model-name prefix, reading its key from the environment, held to
    `region` (DATA_REGION) where the provider lets the app choose."""
    # Google's SDK gives up at the first rate-limit or overload response unless told to retry,
    # on either of its APIs. Anthropic's and OpenAI's retry twice by default.
    if name == "google":
        return GoogleProvider(retry_options=HttpRetryOptions(attempts=GOOGLE_ATTEMPTS))
    if name == "google-cloud":
        return GoogleCloudProvider(retry_options=HttpRetryOptions(attempts=GOOGLE_ATTEMPTS))
    if name == "openrouter" and region:
        return _RegionalOpenRouter(region)
    return infer_provider(name)


def _settings(effort: str, geo: str | None = None, **settings: Any) -> dict[str, Any]:
    """Model settings for one request. An empty `effort` leaves the model's own default.

    OpenRouter's data policy goes on every request; the other providers ignore it. `geo` is
    where Claude is to run the request (`inference_geo`); it goes only to a Claude model, since
    the field rides in the request's body, which another provider would refuse.
    """
    settings["openrouter_provider"] = dict(DATA_POLICY)
    if geo:
        settings["extra_body"] = {"inference_geo": geo}
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
# A model ready for a turn with its settings, or what stops it from being used.
type _Prepared = tuple[Model, dict[str, Any]] | LLMUnavailable


class ModelStream:
    """One streamed model turn: a context manager to iterate for events, then ask for `response()`.

    The request runs on the model loop and passes its events through a queue, so the stream
    can be read from any thread, even a different one for each event. The models are asked in
    turn while each fails before its first event in a way that asks the next (`_falls_back`);
    once one has sent an event, the turn is its own, since what it sent may have been shown.
    `model` then names the one that answered, as the settings name it, and `used()` what the
    turn has used so far, for a reader that leaves before the end.
    """

    def __init__(
        self,
        llm: "LLM",
        candidates: Sequence[tuple[ModelSetting, "_Prepared"]],
        messages: Sequence[ModelMessage],
        parameters: ModelRequestParameters,
    ):
        self._llm = llm
        self._candidates = list(candidates)
        self._messages = list(messages)
        self._parameters = parameters
        self._events: queue.SimpleQueue[Any] = queue.SimpleQueue()
        self._future: Future[ModelResponse] | None = None
        self._deadline = 0.0
        self._ended = False
        # What the models asked before the last one failed with, for the error that says all.
        self._failures: list[Exception] = []
        # Whether the reader stopped waiting for the model, which cancels its request as a
        # reader that went away does; the metrics tell the two apart by it.
        self._gave_up = False
        self.model: str | None = None
        self._used = RequestUsage()

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

    def used(self) -> RequestUsage:
        """What the turn has used: the whole reply's usage once it ended, else what the
        provider has reported by the last event read, which a reader that leaves mid-turn is
        charged for (`run_agent`): the request was made and is paid for all the same. Some
        providers report the tokens only at the end, and the call then counts without them."""
        return self._used

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
            self._gave_up = True
            raise _give_up(self._future, message) from None

    def _reply(self) -> ModelResponse:
        # Having sent `_END`, the pump has nothing left to wait for, so this returns at once.
        try:
            return self._future.result()
        except Exception as error:
            raise _failure(error, self._failures) from error

    async def _pump(self) -> ModelResponse:
        try:
            for position, (candidate, prepared) in enumerate(self._candidates):
                sent = False
                try:
                    if isinstance(prepared, LLMUnavailable):
                        raise prepared
                    model, settings = prepared
                    with model_request(candidate.name, AGENT, self._outcome) as request:
                        async with model_request_stream(
                            model,
                            self._messages,
                            model_settings=settings,
                            model_request_parameters=self._parameters,
                        ) as stream:
                            async for event in stream:
                                request.first_event()
                                # The turn is this model's from its first event on.
                                self.model = candidate.name
                                sent = True
                                self._used = stream.usage
                                self._events.put(event)
                            response = stream.get()
                        self._used = response.usage
                        request.used(response.usage.input_tokens, response.usage.output_tokens)
                except Exception as error:
                    following = self._candidates[position + 1 : position + 2]
                    if sent or not following or not _falls_back(error):
                        raise
                    self._failures.append(error)
                    self._llm._fell_back(candidate, error, following[0][0])
                    continue
                self.model = candidate.name
                return response
            raise LLMUnavailable("No model is set for answering; set AGENT_MODEL.")
        finally:
            self._events.put(_END)

    def _outcome(self, error: BaseException) -> str:
        return "timeout" if self._gave_up else _outcome(error)


class LLM:
    def __init__(self, settings: Settings, switched_off: Callable[[], Collection[str]] = frozenset):
        """The models the settings name. `switched_off` gives the providers an operator
        switched off from the operator's area (app/switches.py), asked before each model is;
        none by default, as for the `models` command, which connects to no database."""
        self._settings = settings
        self._switches = switched_off
        self._models: dict[str, Model] = {}
        # Until when each model that failed in a way that asks its fallback is asked after it
        # (`time.monotonic`), shared by every thread of the process.
        self._passed_over: dict[str, float] = {}
        self._lock = threading.Lock()

    def check(self) -> None:
        """Raise `LLMUnavailable` with what stops the models from being used: one that cannot
        be set up (a key or a package missing, a name unknown), whichever work it does, since a
        fallback that cannot be built is none; or work whose every model is switched off
        (DISABLED_PROVIDERS), which the error marks transient. A model switched off while its
        fallback is not stops nothing: the fallback answers."""
        off: dict[str, LLMUnavailable] = {}
        for model in self._settings.models:
            try:
                self._model(model.name)
            except LLMUnavailable as error:
                if not error.transient:
                    raise
                off[model.name] = error
        for role in (INDEX, AGENT):
            names = [model.name for model in self._settings.models_for(role)]
            if all(name in off for name in names):
                raise off[names[0]]

    def problem(self) -> str | None:
        """What stops the configured models from being used, or None when nothing does."""
        try:
            self.check()
        except LLMUnavailable as error:
            return str(error)
        return None

    @property
    def available(self) -> bool:
        return self.problem() is None

    def _model(self, name: str) -> Model:
        """The adapter for a `provider:model` name, built on first use, and refused while the
        provider is switched off (DISABLED_PROVIDERS, or an operator from the operator's area),
        whether or not it was built before."""
        if (provider := model_provider(name)) in self._settings.disabled:
            raise LLMUnavailable(_switched_off(name, provider), transient=True)
        if provider in self._switches():
            raise LLMUnavailable(_switched_off_by_operator(name, provider), transient=True)
        with self._lock:
            if name not in self._models:
                try:
                    provider = partial(_provider, region=self._settings.data_region)
                    self._models[name] = infer_model(name, provider)
                except Exception as error:
                    # An unknown name, a missing key, or a provider whose package is absent.
                    raise LLMUnavailable(_setup_problem(name, error, self._settings)) from error
            return self._models[name]

    def _geo(self, name: str) -> str | None:
        return inference_geo(name, self._settings.data_region)

    def _candidates(self, role: str) -> list[ModelSetting]:
        """The models for `role`'s work in the order to ask them: as the settings list them,
        except that one passed over (`_fell_back`) goes after the others, and is still asked
        when they fail too."""
        now = time.monotonic()
        with self._lock:
            passed = {name for name, until in self._passed_over.items() if until > now}
        return sorted(self._settings.models_for(role), key=lambda model: model.name in passed)

    def _fell_back(self, model: ModelSetting, error: Exception, following: ModelSetting) -> None:
        """Say that `model` failed with `error` and `following` is asked instead, and pass
        `model` over for PASS_OVER_SECONDS."""
        with self._lock:
            self._passed_over[model.name] = time.monotonic() + PASS_OVER_SECONDS
        MODEL_FALLBACKS.labels(model.name, model.role).inc()
        log.warning(
            "%s %s failed, so %s %s is asked instead: %s",
            model.variable,
            model.name,
            following.variable,
            following.name,
            _said(error),
        )

    def structured(
        self,
        content: str | Sequence[UserContent],
        schema: type[T],
        usage: ModelUsage | None = None,
    ) -> T:
        """One indexing request whose reply is validated against `schema`, asked of each model
        for indexing in turn while one fails in a way that asks the next (`_falls_back`). What
        it cost is added to `usage` once it is answered, under the model that answered
        (app/usage.py)."""
        candidates = self._candidates(INDEX)
        failures: list[Exception] = []
        for position, candidate in enumerate(candidates):
            try:
                output, spent = self._structured(candidate.name, content, schema)
            except Exception as error:
                following = candidates[position + 1 : position + 2]
                if not following or not _falls_back(error):
                    raise _failure(error, failures) from error
                failures.append(error)
                self._fell_back(candidate, error, following[0])
                continue
            if usage is not None:
                # Every request of the run: the provider's reply that failed validation and
                # was asked again was paid for too.
                usage.add(candidate.name, spent)
            return output
        raise LLMUnavailable("No model is set for indexing; set INDEX_MODEL.")

    def _structured(
        self, name: str, content: str | Sequence[UserContent], schema: type[T]
    ) -> tuple[T, RunUsage]:
        """Ask the model `name` once, as `structured` does, and return the reply with what the
        run used; raise what the request failed with as it was raised."""
        model = self._model(name)
        # A provider that can hold its reply to a JSON schema is asked to. For the rest,
        # Pydantic AI requests the schema as a tool call instead.
        constrained = model.profile.get("supports_json_schema_output", False)
        agent = Agent(
            model,
            output_type=NativeOutput(schema) if constrained else schema,
            instructions=INDEX_SYSTEM,
            model_settings=_settings(
                self._settings.index_effort,
                self._geo(name),
                max_tokens=INDEX_MAX_TOKENS,
                timeout=INDEX_TIMEOUT,
            ),
        )
        with model_request(name, INDEX, _outcome) as request:
            future = _submit(agent.run(content))
            try:
                # The SDK's timeout above bounds each HTTP request; this bounds the whole call,
                # which may make several of them when the provider retries.
                result = future.result(timeout=INDEX_TIMEOUT)
            except TimeoutError:
                message = f"The model did not answer within {INDEX_TIMEOUT:g} seconds."
                raise _give_up(future, message) from None
            request.used(result.usage.input_tokens, result.usage.output_tokens)
        return result.output, result.usage

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
        # Built here, on the caller's thread: building a model may read credentials, which
        # would hold up every other request on the model loop.
        return ModelStream(
            self,
            [
                (candidate, self._agent_model(candidate.name))
                for candidate in self._candidates(AGENT)
            ],
            messages,
            ModelRequestParameters(
                function_tools=list(tools),
                instruction_parts=[InstructionPart(content=instructions)],
            ),
        )

    def _agent_model(self, name: str) -> "_Prepared":
        """The model `name` and the settings of an agent turn asked of it, or what stops the
        model from being used, raised when its turn comes (`ModelStream._pump`)."""
        # Anthropic caches a prompt prefix only when asked, and the loop resends a growing one
        # every turn. Other providers ignore the setting. The SDK's timeout is the HTTP
        # library's, which for a stream is per read, so it is the longest pause allowed; the
        # whole turn is bounded by `ModelStream`.
        settings = _settings(
            self._settings.agent_effort, self._geo(name), anthropic_cache=True, timeout=STREAM_STALL
        )
        try:
            return self._model(name), settings
        except LLMUnavailable as error:
            return error
