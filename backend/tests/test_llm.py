"""The model layer, run through Pydantic AI against scripted models and recorded HTTP.

No request leaves the machine. Scripted models check what the app asks of a model and how it
reads the replies, whichever provider is configured. Recorded transports check that those
requests reach Gemini and Claude, through the real SDKs, in the form each API takes.
"""

import asyncio
import json
import os
import re
import socket
import threading
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.messages import (
    BinaryContent,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models import Model
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from pydantic_ai.profiles import ModelProfile

from app.agent.loop import run_agent
from app.config import Settings, export_env_file
from app.errors import shown
from app.indexing.enrich import DocumentAnalysis
from app.indexing.pipeline import Services
from app.llm import (
    INDEX_SYSTEM,
    LLM,
    MODEL_FAILED,
    MODEL_UNAVAILABLE,
    OCR_PROMPT,
    PASS_OVER_SECONDS,
    LLMError,
    LLMUnavailable,
)
from app.usage import Calls, ModelUsage

GEMINI = "google:gemini-2.5-flash-lite"
CLAUDE = "anthropic:claude-opus-5-5"
OPENROUTER = "openrouter:google/gemini-2.5-flash-lite"
VERTEX = "google-cloud:gemini-2.5-flash-lite"
SCRIPTED = "scripted:model"
# A second scripted model, of a provider of its own so that the kill switch can tell them apart.
BACKUP = "backup:model"
TOOL_NAMES = ["list_documents", "get_outline", "search", "query_facts", "read"]
QUESTION = [ModelRequest(parts=[UserPromptPart("What is the notice period?")])]

ANALYSIS = {
    "title": "Master Services Agreement",
    "doc_type": "contract",
    "doc_date": "2024-03-01",
    "summary": "A services agreement.",
    "sections": [{"section": 7, "summary": "Defines the terms."}],
    "facts": [
        {
            "name": "notice_period",
            "value": "90 days",
            "type": "number",
            "number": 90,
            "date": None,
            "currency": None,
            "block": 12,
            "quote": "ninety (90) days",
        }
    ],
}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fail any lookup or connection that would leave this machine."""
    local = ("127.0.0.1", "::1", "localhost")
    resolve, connect = socket.getaddrinfo, socket.socket.connect

    def local_lookup(host, *args, **kwargs):
        name = host.decode() if isinstance(host, bytes) else host
        if name not in local:
            raise RuntimeError(f"A test tried to look up {name}")
        return resolve(host, *args, **kwargs)

    def local_connect(sock, address, *args):
        if isinstance(address, tuple) and address[0] not in local:
            raise RuntimeError(f"A test tried to connect to {address}")
        return connect(sock, address, *args)

    monkeypatch.setattr(socket, "getaddrinfo", local_lookup)
    monkeypatch.setattr(socket.socket, "connect", local_connect)


# Scripted models: what the app asks for, and how it reads the reply.


class Script:
    """A model function that answers from a queue and keeps every request it saw."""

    def __init__(self, *replies: ModelResponse | Exception):
        self.replies = list(replies)
        self.requests: list[tuple[list, AgentInfo]] = []

    def __call__(self, messages: list, info: AgentInfo) -> ModelResponse:
        self.requests.append((messages, info))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def scripted_llm(model: Model, **settings) -> LLM:
    llm = LLM(Settings(_env_file=None, index_model=SCRIPTED, agent_model=SCRIPTED, **settings))
    llm._models[SCRIPTED] = model
    return llm


def fallback_llm(primary: Model, backup: Model, switched_off=frozenset, **settings) -> LLM:
    """An LLM whose every kind of work is SCRIPTED's, with BACKUP as its fallback."""
    llm = LLM(
        Settings(
            _env_file=None,
            index_model=SCRIPTED,
            agent_model=SCRIPTED,
            index_fallback_model=BACKUP,
            agent_fallback_model=BACKUP,
            **settings,
        ),
        switched_off,
    )
    llm._models.update({SCRIPTED: primary, BACKUP: backup})
    return llm


def text_reply(payload: dict) -> ModelResponse:
    return ModelResponse(parts=[TextPart(json.dumps(payload))])


def timed_out_api_error() -> ModelAPIError:
    """A request that timed out as the Anthropic adapter reports it: the HTTP library's
    exception, wrapped in the SDK's, wrapped in Pydantic AI's."""
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    sdk_error = anthropic.APITimeoutError(request=request)
    sdk_error.__cause__ = httpx2.ReadTimeout("timed out", request=request)
    error = ModelAPIError("m", sdk_error.message)
    error.__cause__ = sdk_error
    return error


def test_structured_asks_for_the_schema_and_parses_the_reply():
    script = Script(text_reply(ANALYSIS))
    thinker = ModelProfile(supports_json_schema_output=True, supports_thinking=True)
    llm = scripted_llm(FunctionModel(script, profile=thinker))
    result = llm.structured("the document", DocumentAnalysis)

    assert isinstance(result, DocumentAnalysis)
    assert result.title == "Master Services Agreement"
    assert result.facts[0].number == 90 and result.sections[0].section == 7

    ((messages, info),) = script.requests
    assert [part.content for part in messages[0].parts] == ["the document"]
    assert info.instructions == INDEX_SYSTEM
    assert info.model_settings == {
        "max_tokens": 16000,
        "timeout": 300,
        "openrouter_provider": {"data_collection": "deny"},
    }
    parameters = info.model_request_parameters
    # Effort travels as the library's thinking level, which each provider maps to its own knob.
    assert parameters.thinking == "low"
    assert parameters.output_mode == "native"
    properties = parameters.output_object.json_schema["properties"]
    assert {"title", "doc_type", "doc_date", "summary", "sections", "facts"} <= set(properties)


def test_effort_can_be_left_to_the_model():
    script = Script(text_reply(ANALYSIS))
    thinker = ModelProfile(supports_json_schema_output=True, supports_thinking=True)
    llm = scripted_llm(FunctionModel(script, profile=thinker), index_effort="")
    llm.structured("the document", DocumentAnalysis)
    assert script.requests[0][1].model_request_parameters.thinking is None


def test_a_model_that_cannot_take_a_schema_answers_through_a_tool_call():
    def answer(messages: list, info: AgentInfo) -> ModelResponse:
        assert info.model_request_parameters.output_mode == "tool"
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, ANALYSIS)])

    model = FunctionModel(answer, profile=ModelProfile(supports_json_schema_output=False))
    result = scripted_llm(model).structured("the document", DocumentAnalysis)
    assert result.title == "Master Services Agreement" and result.facts[0].number == 90


def test_ocr_sends_the_image_before_the_instruction():
    script = Script(text_reply({"blocks": [{"kind": "paragraph", "level": None, "text": "Total"}]}))
    blocks = scripted_llm(FunctionModel(script)).ocr_page(b"\x89PNG fake", "image/png")

    assert [(block.kind, block.text) for block in blocks] == [("paragraph", "Total")]
    ((messages, info),) = script.requests
    image, instruction = messages[0].parts[0].content
    assert isinstance(image, BinaryContent)
    assert (image.data, image.media_type) == (b"\x89PNG fake", "image/png")
    assert instruction == OCR_PROMPT
    assert info.model_request_parameters.output_object.name == "OcrPage"


@pytest.mark.parametrize(
    ("replies", "expected", "transient"),
    [
        ([ModelResponse(parts=[], finish_reason="content_filter")], "declined", False),
        # A reply cut off at the output limit is asked for once more before giving up.
        (
            [ModelResponse(parts=[TextPart('{"title": "cut')], finish_reason="length")] * 2,
            "cut off",
            False,
        ),
        (
            [ModelHTTPError(401, "m", {"error": {"message": "bad key"}})],
            r"^The API key for m was rejected.$",
            False,
        ),
        # OpenAI says so with a 401 whose words show part of the key; its code says enough.
        (
            [
                ModelHTTPError(
                    401,
                    "m",
                    {
                        "error": {
                            "message": "Incorrect API key provided: sk-proj-****wxyz.",
                            "type": "invalid_request_error",
                            "code": "invalid_api_key",
                        }
                    },
                )
            ],
            r"^The API key for m was rejected. The provider's error code: "
            r"invalid_request_error, invalid_api_key.$",
            False,
        ),
        (
            [ModelHTTPError(402, "m", {"error": {"message": "Insufficient credits", "code": 402}})],
            r"^The account that pays for m has no credit left.$",
            False,
        ),
        ([ModelHTTPError(403, "m")], "This API key is not allowed to use m.", False),
        ([ModelHTTPError(404, "m")], "The model m was not found", False),
        # The provider, not the request, is the problem: worth asking again in a while.
        (
            [ModelHTTPError(429, "m", {"message": "Quota exceeded for org-1234."})],
            r"^The rate limit for m was reached. Try again shortly.$",
            True,
        ),
        ([ModelHTTPError(503, "m", "Overloaded.")], r"^The request to m failed \(503\).$", True),
        (
            [ModelHTTPError(400, "m", "No such field.")],
            r"^The request to m failed \(400\).$",
            False,
        ),
        # OpenRouter's moderation quotes the text it flagged; the message keeps to the codes.
        (
            [
                ModelHTTPError(
                    403,
                    "m",
                    {
                        "error": {
                            "code": 403,
                            "message": "Your input was flagged",
                            "metadata": {"flagged_input": "the termination clause says"},
                        }
                    },
                )
            ],
            r"^This API key is not allowed to use m.$",
            False,
        ),
        ([ModelAPIError("m", "Connection error.")], "Could not reach the provider of m", True),
        # The provider took too long: the SDK's request timed out, which every adapter wraps
        # (Google's since Pydantic AI 2.55), and the HTTP library's error as it was raised.
        ([timed_out_api_error()], "took too long to answer. Try again.", True),
        ([httpx2.ReadTimeout("timed out")], "took too long to answer. Try again.", True),
        ([RuntimeError("boom")], "failed unexpectedly", False),
    ],
)
def test_failures_become_readable_errors(replies, expected, transient):
    llm = scripted_llm(FunctionModel(Script(*replies)))
    with pytest.raises(LLMError, match=expected) as error:
        llm.structured("the document", DocumentAnalysis)
    assert error.value.transient is transient


@pytest.mark.parametrize(
    ("model", "problem"),
    [
        (GEMINI, f"Set GOOGLE_API_KEY in .env to use {GEMINI}."),
        (CLAUDE, f"Set ANTHROPIC_API_KEY in .env to use {CLAUDE}."),
        (OPENROUTER, f"Set OPENROUTER_API_KEY in .env to use {OPENROUTER}."),
        ("groq:llama-3.3-70b", "Run `uv add 'pydantic-ai-slim[groq]'` in backend/."),
        ("gemini-2.5-flash-lite", "'gemini-2.5-flash-lite' cannot be used. Unknown model"),
    ],
)
def test_a_model_that_cannot_be_set_up_says_what_to_do(monkeypatch, model, problem):
    for name in ("GOOGLE_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    llm = LLM(Settings(_env_file=None, index_model=model, agent_model=model))

    assert llm.available is False
    assert problem in llm.problem()
    with pytest.raises(LLMUnavailable) as raised:
        llm.structured("x", DocumentAnalysis)
    assert str(raised.value) == llm.problem()


def test_vertex_without_credentials_says_how_to_sign_in(monkeypatch, tmp_path):
    # Vertex AI signs in as a Cloud identity rather than with a key; with none to be found, the
    # message names what a service account needs instead of Google's page on the subject.
    for name in (
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_CLOUD_PROJECT",
    ):
        monkeypatch.delenv(name, raising=False)
    # Neither a `gcloud auth application-default login` on this machine nor a Cloud machine.
    monkeypatch.setenv("CLOUDSDK_CONFIG", str(tmp_path))
    monkeypatch.setenv("NO_GCE_CHECK", "true")
    llm = LLM(Settings(_env_file=None, index_model=VERTEX, agent_model=VERTEX))
    assert llm.problem() == (
        "Set GOOGLE_APPLICATION_CREDENTIALS to a service account's key file and "
        f"GOOGLE_CLOUD_PROJECT to its project in .env to use {VERTEX}."
    )


def test_both_models_need_their_keys(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    llm = LLM(Settings(_env_file=None, index_model=GEMINI, agent_model=GEMINI))
    assert llm.available is True and llm.problem() is None

    mixed = LLM(Settings(_env_file=None, index_model=GEMINI, agent_model=CLAUDE))
    assert mixed.problem() == f"Set ANTHROPIC_API_KEY in .env to use {CLAUDE}."


def test_in_production_a_missing_key_is_set_in_the_environment(monkeypatch):
    # A production process is given its keys by the deployment, never by `.env`, so the fix
    # the message names must be the environment.
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    settings = Settings(_env_file=None, environment="production", index_model=GEMINI)
    assert LLM(settings).problem() == f"Set GOOGLE_API_KEY in the environment to use {GEMINI}."


def test_a_switched_off_provider_stops_its_models_as_a_passing_problem(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    llm = LLM(
        Settings(
            _env_file=None, index_model=GEMINI, agent_model=CLAUDE, disabled_providers=" Google ,x"
        )
    )
    off = (
        f"{GEMINI} is switched off: DISABLED_PROVIDERS names its provider, google. Remove it "
        "there and restart the app to use the model again."
    )
    assert llm.problem() == off and llm.available is False
    with pytest.raises(LLMUnavailable) as raised:
        llm.structured("x", DocumentAnalysis)
    assert str(raised.value) == off
    # By design a passing problem, unlike a missing key: the stages that meet it retry.
    assert raised.value.transient is True
    assert raised.value.members == MODEL_UNAVAILABLE
    # The other provider's model is untouched.
    assert llm._model(CLAUDE).model_name.startswith("claude")

    with pytest.raises(ValueError, match="DISABLED_PROVIDERS"):
        Settings(_env_file=None, disabled_providers="goo gle")


# The settings need a database; these tests replace the environment, which held one.
DB = "postgresql+psycopg://app:secret@db.internal:5432/emudoc"


def env_file_in(tmp_path, monkeypatch) -> dict[str, str]:
    """A `.env` with keys beside a shell that set one of them; returns the shell's environment."""
    (tmp_path / ".env").write_text(
        "GOOGLE_API_KEY=from-file\nANTHROPIC_API_KEY=\nOPENROUTER_API_KEY=from-file\n"
    )
    environ = {"OPENROUTER_API_KEY": "from-shell"}
    monkeypatch.setattr(os, "environ", environ)
    monkeypatch.setattr("app.config.ROOT_DIR", tmp_path)
    return environ


def test_in_development_the_env_file_supplies_keys_the_environment_does_not_have(
    tmp_path, monkeypatch
):
    environ = env_file_in(tmp_path, monkeypatch)

    export_env_file(Settings(_env_file=None, environment="development", database_url=DB))
    # What the shell set wins, and a blank entry counts as no key at all.
    assert environ == {"OPENROUTER_API_KEY": "from-shell", "GOOGLE_API_KEY": "from-file"}


def test_in_production_the_env_file_is_not_copied_into_the_environment(tmp_path, monkeypatch):
    environ = env_file_in(tmp_path, monkeypatch)

    export_env_file(Settings(_env_file=None, environment="production", database_url=DB))
    # Whatever the file holds, a production process only has the keys its deployment gave it.
    assert environ == {"OPENROUTER_API_KEY": "from-shell"}


def test_environment_is_development_or_production():
    assert Settings(_env_file=None).environment == "development"
    with pytest.raises(ValueError, match="environment"):
        Settings(_env_file=None, environment="staging")


def on_new_thread(function, *args):
    result = []
    thread = threading.Thread(target=lambda: result.append(function(*args)))
    thread.start()
    thread.join()
    return result[0]


def test_a_stream_can_be_read_from_a_different_thread_each_time():
    async def answer(messages: list, info: AgentInfo):
        for words in ("The notice ", "period is ", "90 days."):
            yield words

    llm = scripted_llm(FunctionModel(stream_function=answer))
    # The chat response is iterated by a thread pool, which may hand each step to another thread.
    with llm.stream_agent("Be brief.", QUESTION, []) as stream:
        events = iter(stream)
        count = 0
        while on_new_thread(next, events, None) is not None:
            count += 1
        assert count >= 3
        assert on_new_thread(stream.response).text == "The notice period is 90 days."


def test_leaving_a_stream_early_stops_the_request():
    stopped = threading.Event()

    async def endless(messages: list, info: AgentInfo):
        try:
            yield "The notice "
            await asyncio.sleep(60)
            yield "never sent"
        finally:
            stopped.set()

    llm = scripted_llm(FunctionModel(stream_function=endless))
    with llm.stream_agent("Be brief.", QUESTION, []) as stream:
        next(iter(stream))
    assert stopped.wait(timeout=5)


def test_a_failed_stream_raises_as_it_ends():
    async def fail(messages: list, info: AgentInfo):
        raise ModelHTTPError(429, "m")
        yield ""

    llm = scripted_llm(FunctionModel(stream_function=fail))
    with pytest.raises(LLMError, match="rate limit"), llm.stream_agent("s", QUESTION, []) as stream:
        list(stream)


# No SDK bounds a whole call, so the app gives up on one itself: a provider that hangs would
# otherwise hold an indexing thread or a request thread for good.


def test_an_indexing_request_that_runs_too_long_is_given_up(monkeypatch):
    stopped = threading.Event()

    async def slow(messages: list, info: AgentInfo) -> ModelResponse:
        try:
            await asyncio.sleep(60)
            return text_reply(ANALYSIS)
        finally:
            stopped.set()

    monkeypatch.setattr("app.llm.INDEX_TIMEOUT", 0.2)
    llm = scripted_llm(FunctionModel(slow))
    with pytest.raises(LLMError, match="did not answer within 0.2 seconds. Try again.") as error:
        llm.structured("the document", DocumentAnalysis)
    # Transient, so the enrich stage is retried; and the request is stopped, not left running.
    assert error.value.transient is True
    assert stopped.wait(timeout=5)


def test_a_stream_that_goes_quiet_is_given_up(monkeypatch):
    stopped = threading.Event()

    async def quiet(messages: list, info: AgentInfo):
        try:
            yield "The notice "
            await asyncio.sleep(60)
            yield "never sent"
        finally:
            stopped.set()

    monkeypatch.setattr("app.llm.STREAM_STALL", 0.2)
    llm = scripted_llm(FunctionModel(stream_function=quiet))
    received = []
    with pytest.raises(LLMError, match="sent nothing for 0.2 seconds. Try again.") as error:
        with llm.stream_agent("Be brief.", QUESTION, []) as stream:
            received.extend(stream)
    # What did arrive was passed on before the silence was given up on.
    assert error.value.transient is True and received
    assert stopped.wait(timeout=5)


def test_a_turn_that_runs_too_long_is_given_up(monkeypatch):
    stopped = threading.Event()

    async def endless(messages: list, info: AgentInfo):
        try:
            for _ in range(200):
                yield "more "
                await asyncio.sleep(0.02)
        finally:
            stopped.set()

    monkeypatch.setattr("app.llm.AGENT_TIMEOUT", 0.3)
    llm = scripted_llm(FunctionModel(stream_function=endless))
    received = []
    with pytest.raises(LLMError, match="did not finish within 0.3 seconds. Try again.") as error:
        with llm.stream_agent("Be brief.", QUESTION, []) as stream:
            received.extend(stream)
    # The stream never went quiet; it was cut off for running past the turn's time.
    assert error.value.transient is True and 1 <= len(received) < 200
    assert stopped.wait(timeout=5)


# Fallbacks: the next model is asked what the first could not answer, and only then.

GEMINI_KEY_REJECTED = {
    "error": {
        "code": 400,
        "status": "INVALID_ARGUMENT",
        "details": [{"reason": "API_KEY_INVALID"}],
    }
}


@pytest.mark.parametrize(
    ("error", "said"),
    [
        (ModelHTTPError(503, "m", "Overloaded."), r"The request to m failed \(503\)."),
        (ModelHTTPError(429, "m"), "The rate limit for m was reached."),
        # A retired model is not found.
        (ModelHTTPError(404, "m"), "The model m was not found, or is served no longer."),
        (ModelHTTPError(401, "m"), "The API key for m was rejected."),
        (ModelHTTPError(402, "m"), "The account that pays for m has no credit left."),
        (ModelHTTPError(403, "m"), "This API key is not allowed to use m."),
        (ModelHTTPError(400, "m", GEMINI_KEY_REJECTED), "The API key for m was rejected."),
        (ModelAPIError("m", "Connection error."), "Could not reach the provider of m."),
    ],
)
def test_the_fallback_answers_what_the_first_model_cannot(error, said, caplog):
    primary, backup = Script(error), Script(text_reply(ANALYSIS))
    llm = fallback_llm(FunctionModel(primary), FunctionModel(backup))
    usage = ModelUsage()
    result = llm.structured("the document", DocumentAnalysis, usage)

    assert result.title == "Master Services Agreement"
    # The backup was asked the same request, and is what the run is charged for.
    assert [part.content for part in backup.requests[0][0][0].parts] == ["the document"]
    assert list(usage.models) == [BACKUP] and usage.calls == 1
    (record,) = [r for r in caplog.records if r.name == "app.llm"]
    assert record.levelname == "WARNING"
    assert record.getMessage().startswith(
        f"INDEX_MODEL {SCRIPTED} failed, so INDEX_FALLBACK_MODEL {BACKUP} is asked instead: "
    )
    assert re.search(said, record.getMessage())


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        # The request, not the model, was refused: the next model would refuse it too, or the
        # app has a fault the fallback would hide.
        (ModelHTTPError(400, "m", "No such field."), r"^The request to m failed \(400\).$"),
        (ModelHTTPError(422, "m"), r"^The request to m failed \(422\).$"),
        (ModelResponse(parts=[], finish_reason="content_filter"), "declined"),
        (RuntimeError("boom"), "failed unexpectedly"),
        # Slow: a document too large for the time would be as slow on the next model.
        (timed_out_api_error(), "took too long to answer"),
    ],
)
def test_a_request_the_first_model_refused_is_not_asked_of_the_fallback(reply, expected):
    backup = Script(text_reply(ANALYSIS))
    llm = fallback_llm(FunctionModel(Script(reply)), FunctionModel(backup))
    with pytest.raises(LLMError, match=expected):
        llm.structured("the document", DocumentAnalysis)
    assert backup.requests == []


def test_an_indexing_request_that_runs_too_long_is_not_asked_of_the_fallback(monkeypatch):
    async def slow(messages: list, info: AgentInfo) -> ModelResponse:
        await asyncio.sleep(60)
        return text_reply(ANALYSIS)

    monkeypatch.setattr("app.llm.INDEX_TIMEOUT", 0.2)
    backup = Script(text_reply(ANALYSIS))
    llm = fallback_llm(FunctionModel(slow), FunctionModel(backup))
    with pytest.raises(LLMError, match="did not answer within 0.2 seconds"):
        llm.structured("the document", DocumentAnalysis)
    assert backup.requests == []


def test_when_every_model_fails_the_error_says_what_each_failed_with():
    llm = fallback_llm(
        FunctionModel(Script(ModelHTTPError(503, "first"))),
        FunctionModel(Script(ModelHTTPError(401, "second"))),
    )
    with pytest.raises(LLMError) as raised:
        llm.structured("the document", DocumentAnalysis)
    assert str(raised.value) == (
        "The request to first failed (503). The API key for second was rejected."
    )
    # The first model is down for now and may answer the next attempt.
    assert raised.value.transient is True
    # A deployment's members are told why the last model failed, without naming either.
    assert raised.value.members == MODEL_UNAVAILABLE

    llm = fallback_llm(
        FunctionModel(Script(ModelHTTPError(404, "first"))),
        FunctionModel(Script(ModelHTTPError(400, "second"))),
    )
    with pytest.raises(LLMError) as raised:
        llm.structured("the document", DocumentAnalysis)
    assert raised.value.transient is False
    assert raised.value.members == MODEL_FAILED

    # The fallback's own sentence names no model, but the message names the first.
    llm = fallback_llm(
        FunctionModel(Script(ModelHTTPError(503, "first"))),
        FunctionModel(Script(ModelResponse(parts=[], finish_reason="content_filter"))),
    )
    with pytest.raises(LLMError) as raised:
        llm.structured("the document", DocumentAnalysis)
    assert str(raised.value).startswith("The request to first failed (503). ")
    assert raised.value.members == "The model declined to process this content."


@pytest.mark.parametrize(
    ("reply", "members"),
    [
        # The deployment's to fix or to wait out: the key, the account, the model, the provider.
        (ModelHTTPError(401, "m"), MODEL_UNAVAILABLE),
        (ModelHTTPError(400, "m", GEMINI_KEY_REJECTED), MODEL_UNAVAILABLE),
        (ModelHTTPError(402, "m"), MODEL_UNAVAILABLE),
        (ModelHTTPError(403, "m"), MODEL_UNAVAILABLE),
        (ModelHTTPError(404, "m"), MODEL_UNAVAILABLE),
        (ModelHTTPError(429, "m"), MODEL_UNAVAILABLE),
        (ModelHTTPError(503, "m", "Overloaded."), MODEL_UNAVAILABLE),
        (ModelAPIError("m", "Connection error."), MODEL_UNAVAILABLE),
        # The request itself was refused, which its status says to the operator alone.
        (ModelHTTPError(400, "m", "No such field."), MODEL_FAILED),
        (ModelHTTPError(422, "m"), MODEL_FAILED),
        # Said without the model's name already, so the members read it as it is.
        (ModelResponse(parts=[], finish_reason="content_filter"), None),
        (timed_out_api_error(), None),
        (RuntimeError("boom"), None),
    ],
)
def test_a_deployments_members_are_told_of_a_failure_without_the_model(reply, members):
    llm = scripted_llm(FunctionModel(Script(reply)))
    with pytest.raises(LLMError) as raised:
        llm.structured("the document", DocumentAnalysis)
    error = raised.value
    assert error.members == members
    assert shown(error, development=False) == (members or str(error))
    # A developer runs the app they use, and reads the operator's sentence.
    assert shown(error, development=True) == str(error)


def test_a_model_that_failed_is_asked_after_its_fallback_for_a_minute(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("app.llm.time", SimpleNamespace(monotonic=lambda: clock[0]))
    primary = Script(ModelHTTPError(503, "m"))
    backup = Script(text_reply(ANALYSIS), text_reply(ANALYSIS))
    llm = fallback_llm(FunctionModel(primary), FunctionModel(backup))

    def asked() -> tuple[int, int]:
        return len(primary.requests), len(backup.requests)

    llm.structured("first", DocumentAnalysis)
    # A provider that is down would otherwise cost every request its retries first.
    llm.structured("second", DocumentAnalysis)
    assert asked() == (1, 2)
    # A minute after it failed, it is asked first again.
    clock[0] += PASS_OVER_SECONDS
    primary.replies.append(text_reply(ANALYSIS))
    llm.structured("third", DocumentAnalysis)
    assert asked() == (2, 2)
    # Passed over, it is still asked when its fallback fails too.
    primary.replies += [ModelHTTPError(503, "m"), text_reply(ANALYSIS)]
    backup.replies += [text_reply(ANALYSIS), ModelHTTPError(503, "b")]
    llm.structured("fourth", DocumentAnalysis)
    usage = ModelUsage()
    llm.structured("fifth", DocumentAnalysis, usage)
    assert asked() == (4, 4) and list(usage.models) == [SCRIPTED]


def test_a_switched_off_model_leaves_its_work_to_the_fallback(caplog):
    backup = Script(text_reply(ANALYSIS))
    llm = fallback_llm(
        FunctionModel(Script()), FunctionModel(backup), disabled_providers="scripted"
    )
    # What the switch and the fallback are there for: the app keeps working.
    assert llm.problem() is None and llm.available is True
    usage = ModelUsage()
    assert llm.structured("the document", DocumentAnalysis, usage).facts[0].number == 90
    assert list(usage.models) == [BACKUP]
    assert f"{SCRIPTED} is switched off: DISABLED_PROVIDERS names" in caplog.text

    both = fallback_llm(
        FunctionModel(Script()), FunctionModel(Script()), disabled_providers="scripted,backup"
    )
    assert both.problem().startswith(f"{SCRIPTED} is switched off")
    with pytest.raises(LLMUnavailable) as raised:
        both.structured("the document", DocumentAnalysis)
    assert raised.value.transient is True
    assert f"{SCRIPTED} is switched off" in str(raised.value)
    assert f"{BACKUP} is switched off" in str(raised.value)


def test_a_fallback_that_cannot_be_set_up_stops_the_app_as_its_primary_would(monkeypatch):
    # A fallback that cannot be built is none, and production refuses to start without it
    # rather than find out at the first outage.
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    llm = LLM(
        Settings(
            _env_file=None, index_model=GEMINI, agent_model=GEMINI, agent_fallback_model=CLAUDE
        )
    )
    assert llm.problem() == f"Set ANTHROPIC_API_KEY in .env to use {CLAUDE}."
    with pytest.raises(LLMUnavailable) as raised:
        llm.check()
    assert raised.value.transient is False


def test_a_turn_the_first_model_cannot_open_is_asked_of_the_fallback():
    async def overloaded(messages: list, info: AgentInfo):
        raise ModelHTTPError(503, "m")
        yield ""

    async def answer(messages: list, info: AgentInfo):
        yield "Ninety days."

    llm = fallback_llm(
        FunctionModel(stream_function=overloaded), FunctionModel(stream_function=answer)
    )
    with llm.stream_agent("Be brief.", QUESTION, []) as stream:
        assert list(stream)
        assert stream.response().text == "Ninety days."
    assert stream.model == BACKUP


def test_a_turn_that_failed_after_its_first_event_is_not_asked_of_the_fallback():
    # What the first model sent may have been shown; a second answer would follow it.
    asked = []

    async def breaks_off(messages: list, info: AgentInfo):
        yield "The notice "
        raise ModelHTTPError(503, "m")

    async def answer(messages: list, info: AgentInfo):
        asked.append(messages)
        yield "Ninety days."

    llm = fallback_llm(
        FunctionModel(stream_function=breaks_off), FunctionModel(stream_function=answer)
    )
    received = []
    with pytest.raises(LLMError, match=r"^The request to m failed \(503\).$"):
        with llm.stream_agent("Be brief.", QUESTION, []) as stream:
            received.extend(stream)
    assert received and asked == []


def test_an_answer_the_fallback_finished_is_charged_to_each_model_that_answered(services, indexed):
    async def searches_then_is_overloaded(messages: list, info: AgentInfo):
        if len(messages) > 1:
            raise ModelHTTPError(529, "m", "Overloaded.")
        yield {
            0: DeltaToolCall(
                name="search",
                json_args=json.dumps({"query": "termination notice"}),
                tool_call_id="c1",
            )
        }

    async def answers(messages: list, info: AgentInfo):
        yield "The notice period is 90 days."

    llm = fallback_llm(
        FunctionModel(stream_function=searches_then_is_overloaded),
        FunctionModel(stream_function=answers),
    )
    usage = ModelUsage()
    question = [{"role": "user", "content": "What is the notice period?"}]
    recorded = Services(llm, services.embedder, services.storage)
    events = list(run_agent(recorded, indexed.project_id, question, usage))

    assert "".join(e["text"] for e in events if e["type"] == "text") == (
        "The notice period is 90 days."
    )
    # The first turn was the primary's, the second its fallback's once the primary was
    # overloaded: each model's calls are counted under its own name, for its own price, and
    # the answer names the model that ended it.
    assert list(usage.models) == [SCRIPTED, BACKUP]
    assert usage.models[SCRIPTED].calls == usage.models[BACKUP].calls == 1
    assert events[-1]["usage"] == usage.spent() and usage.spent()["model"] == BACKUP


# Recorded HTTP: the requests as Gemini and Claude receive them, through the real SDKs.


class Recorder:
    """A transport that answers from a queue and keeps every request it saw. An error in the
    queue is raised in place of an answer, as the HTTP library raises a connection that fails."""

    def __init__(self, *responses: httpx2.Response | httpx2.TransportError):
        self.responses = list(responses)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, httpx2.TransportError):
            response.request = request
            raise response
        return response

    def body(self, index: int = 0) -> dict:
        return json.loads(self.requests[index].content)


# The module in which each provider looks up the function that builds its HTTP client.
CLIENT_FACTORY = {
    "google": "pydantic_ai.providers.google",
    "google-cloud": "pydantic_ai.providers.google",
    "anthropic": "pydantic_ai.providers.anthropic",
    "openrouter": "pydantic_ai.providers._openai_compatible",
}


def recorded_llm(monkeypatch, recorder: Recorder, model: str, **settings) -> LLM:
    """An LLM built as the app builds it, except that its HTTP client answers from a recording."""
    return recorded_models(
        monkeypatch, {model: recorder}, index_model=model, agent_model=model, **settings
    )


def recorded_models(monkeypatch, recorders: dict[str, Recorder], **settings) -> LLM:
    """An LLM built as the app builds it, each model's provider answering from its own
    recording."""
    # Vertex AI takes Gemini's variable for a key of its own (express mode), and a service
    # account's file would win over it.
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    for model, recorder in recorders.items():
        provider = model.split(":")[0]
        monkeypatch.setenv(f"{provider.partition('-')[0].upper()}_API_KEY", "test-key")
        monkeypatch.setattr(
            f"{CLIENT_FACTORY[provider]}.create_async_httpx2_client",
            lambda recorder=recorder: httpx2.AsyncClient(transport=httpx2.MockTransport(recorder)),
        )
    return LLM(Settings(_env_file=None, **settings))


def ask(llm: LLM, services, document) -> list[dict]:
    """Run the agent on one question about the indexed sample contract."""
    recorded = Services(llm, services.embedder, services.storage)
    question = [{"role": "user", "content": "What is the notice period?"}]
    return list(run_agent(recorded, document.project_id, question))


def assert_searched_then_answered(events: list[dict]) -> None:
    assert [event["type"] for event in events] == [
        "thinking",
        "tool_call",
        "sources",
        "tool_result",
        "text",
        "text",
        "done",
    ]
    assert events[1]["name"] == "search"
    assert events[1]["input"] == {"query": "termination notice"}
    assert any("ninety (90) days" in source["excerpt"] for source in events[2]["sources"])
    assert "".join(e["text"] for e in events if e["type"] == "text") == (
        "The notice period is 90 days."
    )
    usage = events[-1]["usage"]
    assert (usage["calls"], usage["input_tokens"], usage["output_tokens"]) == (2, 50, 60)


def gemini(*parts: dict, finish: str | None = "STOP") -> dict:
    """One Gemini response, or one chunk of a streamed one."""
    candidate = {"content": {"role": "model", "parts": list(parts)}, "index": 0}
    if finish:
        candidate["finishReason"] = finish
    return {
        "candidates": [candidate],
        "usageMetadata": {"promptTokenCount": 25, "candidatesTokenCount": 30},
        "modelVersion": "gemini-2.5-flash-lite",
        "responseId": "response_01",
    }


def gemini_stream(*chunks: dict) -> httpx2.Response:
    body = "".join(f"data: {json.dumps(chunk)}\r\n\r\n" for chunk in chunks)
    headers = {"content-type": "text/event-stream"}
    return httpx2.Response(200, headers=headers, content=body.encode())


def test_gemini_structured_request(monkeypatch):
    recorder = Recorder(httpx2.Response(200, json=gemini({"text": json.dumps(ANALYSIS)})))
    usage = ModelUsage()
    result = recorded_llm(monkeypatch, recorder, GEMINI).structured(
        "the document", DocumentAnalysis, usage
    )

    assert result.title == "Master Services Agreement" and result.facts[0].number == 90
    # The run's one request, with the token counts the provider reported, for the bill, under
    # the model's name as the settings give it, which is how MODEL_PRICES prices it.
    assert usage.models == {GEMINI: Calls(calls=1, input_tokens=25, output_tokens=30)}
    request = recorder.requests[0]
    assert request.url.path.endswith("/models/gemini-2.5-flash-lite:generateContent")
    assert request.headers["x-goog-api-key"] == "test-key"
    # The SDK gives each HTTP request the indexing timeout, and tells the server too.
    assert request.extensions["timeout"]["read"] == 300
    assert request.headers["x-server-timeout"] == "300"
    body = recorder.body()
    assert body["systemInstruction"]["parts"] == [{"text": INDEX_SYSTEM}]
    assert body["contents"] == [{"parts": [{"text": "the document"}], "role": "user"}]
    config = body["generationConfig"]
    assert config["maxOutputTokens"] == 16000
    # The reply is held to the schema by the API itself.
    assert config["responseMimeType"] == "application/json"
    properties = config["responseJsonSchema"]["properties"]
    assert {"title", "doc_type", "doc_date", "summary", "sections", "facts"} <= set(properties)
    assert "thinkingConfig" in config
    # OpenRouter's data policy is OpenRouter's alone.
    assert "provider" not in body


def test_gemini_without_effort_sends_no_thinking_settings(monkeypatch):
    recorder = Recorder(httpx2.Response(200, json=gemini({"text": json.dumps(ANALYSIS)})))
    llm = recorded_llm(monkeypatch, recorder, GEMINI, index_effort="")
    llm.structured("the document", DocumentAnalysis)
    assert "thinkingConfig" not in recorder.body()["generationConfig"]


def test_agent_loop_over_recorded_gemini_streams(monkeypatch, services, indexed):
    recorder = Recorder(
        gemini_stream(
            gemini({"text": "Find the termination clause.", "thought": True}, finish=None),
            gemini({"functionCall": {"name": "search", "args": {"query": "termination notice"}}}),
        ),
        gemini_stream(
            gemini({"text": "The notice period is "}, finish=None),
            gemini({"text": "90 days."}),
        ),
    )
    events = ask(recorded_llm(monkeypatch, recorder, GEMINI), services, indexed)
    assert_searched_then_answered(events)

    opening = recorder.body(0)
    path = recorder.requests[0].url.path
    assert path.endswith("/models/gemini-2.5-flash-lite:streamGenerateContent")
    assert "Project: Test project" in opening["systemInstruction"]["parts"][0]["text"]
    assert [tool["name"] for tool in opening["tools"][0]["functionDeclarations"]] == TOOL_NAMES
    # Agent replies run to the model's own output limit.
    assert "maxOutputTokens" not in opening["generationConfig"]
    assert "thinkingConfig" in opening["generationConfig"]

    # The follow-up returns the model's turn as it came, then answers the call in one message.
    follow_up = recorder.body(1)["contents"]
    assert [content["role"] for content in follow_up] == ["user", "model", "user"]
    thought, call = follow_up[1]["parts"]
    assert thought == {"text": "Find the termination clause.", "thought": True}
    assert call["functionCall"]["name"] == "search"
    assert call["functionCall"]["args"] == {"query": "termination notice"}
    (result,) = follow_up[2]["parts"]
    assert result["functionResponse"]["name"] == "search"
    assert "ninety (90) days" in json.dumps(result["functionResponse"]["response"])


def test_gemini_is_asked_again_when_it_is_overloaded(monkeypatch):
    error = {"code": 503, "message": "The model is overloaded.", "status": "UNAVAILABLE"}
    recorder = Recorder(
        httpx2.Response(503, json={"error": error}),
        httpx2.Response(200, json=gemini({"text": json.dumps(ANALYSIS)})),
    )
    result = recorded_llm(monkeypatch, recorder, GEMINI).structured(
        "the document", DocumentAnalysis
    )
    assert result.title == "Master Services Agreement" and len(recorder.requests) == 2


def test_vertex_is_asked_again_when_it_is_overloaded(monkeypatch):
    # The production path for Gemini retries as the Developer API does.
    error = {"code": 503, "message": "The model is overloaded.", "status": "UNAVAILABLE"}
    recorder = Recorder(
        httpx2.Response(503, json={"error": error}),
        httpx2.Response(200, json=gemini({"text": json.dumps(ANALYSIS)})),
    )
    result = recorded_llm(monkeypatch, recorder, VERTEX).structured(
        "the document", DocumentAnalysis
    )
    assert result.title == "Master Services Agreement" and len(recorder.requests) == 2
    request = recorder.requests[1]
    assert request.url.host == "aiplatform.googleapis.com"
    assert request.url.path.endswith(
        "/publishers/google/models/gemini-2.5-flash-lite:generateContent"
    )


def test_gemini_errors_are_told_by_their_codes(monkeypatch, caplog):
    # Gemini refuses a wrong key with a 400 whose details name the reason.
    error = {
        "code": 400,
        "message": "API key not valid. Please pass a valid API key.",
        "status": "INVALID_ARGUMENT",
        "details": [
            {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "API_KEY_INVALID"}
        ],
    }
    recorder = Recorder(httpx2.Response(400, json={"error": error}))
    llm = recorded_llm(monkeypatch, recorder, GEMINI)
    with pytest.raises(LLMError) as raised:
        llm.structured("the document", DocumentAnalysis)
    assert str(raised.value) == (
        "The API key for gemini-2.5-flash-lite was rejected. The provider's error code: "
        "INVALID_ARGUMENT, API_KEY_INVALID."
    )
    assert len(recorder.requests) == 1
    # The log says the same, and nothing of the body's words.
    assert str(raised.value) in caplog.text and "Please pass" not in caplog.text


def test_indexing_and_chat_share_one_model(monkeypatch):
    recorder = Recorder(
        httpx2.Response(200, json=gemini({"text": json.dumps(ANALYSIS)})),
        gemini_stream(gemini({"text": "Ninety days."})),
    )
    llm = recorded_llm(monkeypatch, recorder, GEMINI)
    llm.structured("the document", DocumentAnalysis)
    # The indexing run must leave the model's HTTP client open for the chat that follows.
    with llm.stream_agent("Be brief.", QUESTION, []) as stream:
        list(stream)
        assert stream.response().text == "Ninety days."


def test_openrouter_structured_request(monkeypatch):
    reply = {
        "id": "gen-01",
        "object": "chat.completion",
        "created": 0,
        "model": "google/gemini-2.5-flash-lite",
        "provider": "Google AI Studio",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": json.dumps(ANALYSIS)},
            }
        ],
        "usage": {"prompt_tokens": 25, "completion_tokens": 30, "total_tokens": 55},
    }
    recorder = Recorder(httpx2.Response(200, json=reply))
    llm = recorded_llm(monkeypatch, recorder, OPENROUTER)
    usage = ModelUsage()
    result = llm.structured("the document", DocumentAnalysis, usage)

    assert result.title == "Master Services Agreement" and result.facts[0].number == 90
    assert usage.models == {OPENROUTER: Calls(calls=1, input_tokens=25, output_tokens=30)}
    request = recorder.requests[0]
    assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer test-key"
    body = recorder.body()
    assert body["model"] == "google/gemini-2.5-flash-lite" and body["max_tokens"] == 16000
    assert body["messages"][0] == {"role": "system", "content": INDEX_SYSTEM}
    assert body["response_format"]["type"] == "json_schema"
    # Only to providers of the model that neither store the request nor train on it.
    assert body["provider"] == {"data_collection": "deny"}


def openrouter_stream(*chunks: dict) -> httpx2.Response:
    body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
    headers = {"content-type": "text/event-stream"}
    return httpx2.Response(200, headers=headers, content=body.encode())


def test_an_openrouter_turn_keeps_to_providers_that_store_nothing(monkeypatch):
    chunk = {
        "id": "gen-02",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "google/gemini-2.5-flash-lite",
        "provider": "Google",
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "content": "Ninety days."},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 25, "completion_tokens": 3, "total_tokens": 28},
    }
    recorder = Recorder(openrouter_stream(chunk))
    llm = recorded_llm(monkeypatch, recorder, OPENROUTER)
    with llm.stream_agent("Be brief.", QUESTION, []) as stream:
        list(stream)
        assert stream.response().text == "Ninety days."
    assert recorder.body()["stream"] is True
    assert recorder.body()["provider"] == {"data_collection": "deny"}


@pytest.mark.parametrize("region", ["eu", "us"])
def test_openrouter_is_reached_at_the_region_s_own_address(monkeypatch, region):
    # Its regional address sends a request only to providers in the region, and fails it when
    # none serves the model rather than leave the region.
    chunk = {
        "id": "gen-03",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "google/gemini-2.5-flash-lite",
        "choices": [{"index": 0, "delta": {"content": "Ninety days."}, "finish_reason": "stop"}],
    }
    recorder = Recorder(openrouter_stream(chunk))
    llm = recorded_llm(monkeypatch, recorder, OPENROUTER, data_region=region)
    with llm.stream_agent("Be brief.", QUESTION, []) as stream:
        list(stream)
    request = recorder.requests[0]
    assert str(request.url) == f"https://{region}.openrouter.ai/api/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer test-key"
    assert recorder.body()["provider"] == {"data_collection": "deny"}


def message(content, stop_reason="end_turn", **extra) -> dict:
    return {
        "id": "msg_01",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 12, "output_tokens": 34},
        **extra,
    }


def claude_stream(*blocks: tuple[dict, list[dict]], stop_reason: str) -> httpx2.Response:
    """A streamed Claude message made of (block, deltas) pairs."""
    events = [
        {
            "type": "message_start",
            "message": message([], stop_reason=None)
            | {"usage": {"input_tokens": 25, "output_tokens": 1}},
        }
    ]
    for index, (block, deltas) in enumerate(blocks):
        events.append({"type": "content_block_start", "index": index, "content_block": block})
        events.extend(
            {"type": "content_block_delta", "index": index, "delta": delta} for delta in deltas
        )
        events.append({"type": "content_block_stop", "index": index})
    events.append(
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": 30},
        }
    )
    events.append({"type": "message_stop"})
    body = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events)
    headers = {"content-type": "text/event-stream"}
    return httpx2.Response(200, headers=headers, content=body.encode())


def test_claude_structured_request(monkeypatch):
    reply = message([{"type": "text", "text": json.dumps(ANALYSIS)}])
    recorder = Recorder(httpx2.Response(200, json=reply))
    result = recorded_llm(monkeypatch, recorder, CLAUDE).structured(
        "the document", DocumentAnalysis
    )

    assert result.title == "Master Services Agreement" and result.facts[0].number == 90
    request = recorder.requests[0]
    assert request.url.path == "/v1/messages" and request.headers["x-api-key"] == "test-key"
    assert request.extensions["timeout"]["read"] == 300
    body = recorder.body()
    assert body["model"] == "claude-opus-5-5" and body["max_tokens"] == 16000
    assert body["system"][0]["text"] == INDEX_SYSTEM
    assert body["output_config"]["effort"] == "low"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert "cache_control" not in body
    assert "provider" not in body
    # Without a region the workspace's default decides where Claude runs.
    assert "inference_geo" not in body


def test_claude_is_told_to_run_in_the_us_when_the_data_is_kept_there(monkeypatch):
    reply = message([{"type": "text", "text": json.dumps(ANALYSIS)}])
    recorder = Recorder(
        httpx2.Response(200, json=reply),
        claude_stream(({"type": "text", "text": "Ninety days."}, []), stop_reason="end_turn"),
    )
    llm = recorded_llm(monkeypatch, recorder, CLAUDE, data_region="us")
    llm.structured("the document", DocumentAnalysis)
    with llm.stream_agent("Be brief.", QUESTION, []) as stream:
        list(stream)
    assert [recorder.body(index)["inference_geo"] for index in (0, 1)] == ["us", "us"]


def test_only_claude_is_told_where_to_run(monkeypatch):
    # The field rides in the request's body, which Gemini's API would refuse.
    reply = gemini({"text": json.dumps(ANALYSIS)})
    recorder = Recorder(httpx2.Response(200, json=reply))
    llm = recorded_llm(monkeypatch, recorder, GEMINI, data_region="us")
    llm.structured("the document", DocumentAnalysis)
    assert "inference_geo" not in recorder.requests[0].content.decode()


def test_agent_loop_over_recorded_claude_streams(monkeypatch, services, indexed):
    recorder = Recorder(
        claude_stream(
            (
                {"type": "thinking", "thinking": "", "signature": ""},
                [{"type": "signature_delta", "signature": "sig-abc"}],
            ),
            (
                {"type": "tool_use", "id": "toolu_01", "name": "search", "input": {}},
                [
                    {"type": "input_json_delta", "partial_json": '{"query": "termination'},
                    {"type": "input_json_delta", "partial_json": ' notice"}'},
                ],
            ),
            stop_reason="tool_use",
        ),
        claude_stream(
            (
                {"type": "text", "text": ""},
                [
                    {"type": "text_delta", "text": "The notice period is "},
                    {"type": "text_delta", "text": "90 days."},
                ],
            ),
            stop_reason="end_turn",
        ),
    )
    events = ask(recorded_llm(monkeypatch, recorder, CLAUDE), services, indexed)
    assert_searched_then_answered(events)

    opening = recorder.body(0)
    assert opening["stream"] is True and opening["model"] == "claude-opus-5-5"
    # A stream's read timeout is the longest pause between its chunks.
    assert recorder.requests[0].extensions["timeout"]["read"] == 60
    # The growing prefix is cached, so each loop iteration re-reads it at cache rates.
    assert opening["cache_control"]["type"] == "ephemeral"
    assert opening["output_config"] == {"effort": "medium"}
    assert [tool["name"] for tool in opening["tools"]] == TOOL_NAMES
    assert "Project: Test project" in opening["system"][0]["text"]

    # The follow-up echoes the assistant turn unchanged, thinking block and signature
    # included, then answers the tool call in a single user message.
    follow_up = recorder.body(1)["messages"]
    assert [m["role"] for m in follow_up] == ["user", "assistant", "user"]
    thinking, call = follow_up[1]["content"]
    assert thinking["type"] == "thinking" and thinking["signature"] == "sig-abc"
    assert call == {
        "type": "tool_use",
        "id": "toolu_01",
        "name": "search",
        "input": {"query": "termination notice"},
    }
    (result,) = follow_up[2]["content"]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "toolu_01"
    assert "ninety (90) days" in json.dumps(result["content"])


# A retired model, as each provider says so: not found.
GEMINI_RETIRED = {
    "error": {
        "code": 404,
        "message": "models/gemini-2.5-flash-lite is not found for API version v1beta.",
        "status": "NOT_FOUND",
    }
}
CLAUDE_RETIRED = {
    "type": "error",
    "error": {"type": "not_found_error", "message": "model: claude-opus-5-5"},
}


def test_a_retired_gemini_model_leaves_its_requests_to_claude(monkeypatch, caplog):
    gemini_recorder = Recorder(httpx2.Response(404, json=GEMINI_RETIRED))
    claude_recorder = Recorder(
        httpx2.Response(200, json=message([{"type": "text", "text": json.dumps(ANALYSIS)}]))
    )
    llm = recorded_models(
        monkeypatch,
        {GEMINI: gemini_recorder, CLAUDE: claude_recorder},
        index_model=GEMINI,
        agent_model=GEMINI,
        index_fallback_model=CLAUDE,
    )
    usage = ModelUsage()
    result = llm.structured("the document", DocumentAnalysis, usage)

    assert result.title == "Master Services Agreement"
    # A 404 is not retried: Gemini was asked once, and Claude the request as Claude takes it.
    assert len(gemini_recorder.requests) == 1
    body = claude_recorder.body()
    assert body["model"] == "claude-opus-5-5" and body["system"][0]["text"] == INDEX_SYSTEM
    assert body["messages"][0]["content"][0]["text"] == "the document"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert usage.models == {CLAUDE: Calls(calls=1, input_tokens=12, output_tokens=34)}
    assert (
        f"INDEX_MODEL {GEMINI} failed, so INDEX_FALLBACK_MODEL {CLAUDE} is asked instead: The "
        "model gemini-2.5-flash-lite was not found, or is served no longer."
    ) in caplog.text
    # Nothing of the provider's words reaches the log.
    assert "API version" not in caplog.text


def test_a_gemini_out_of_reach_leaves_its_requests_to_claude(monkeypatch, caplog):
    # Pydantic AI reports Google's connection that could not be made as it reports any
    # provider's (a ModelAPIError since 2.55; the HTTP library's own error before, which was
    # no reason to ask the next model). Gemini is asked once rather than again after a second,
    # as an overload would be.
    monkeypatch.setattr("app.llm.GOOGLE_ATTEMPTS", 1)
    gemini_recorder = Recorder(httpx2.ConnectError("Connection refused"))
    claude_recorder = Recorder(
        httpx2.Response(200, json=message([{"type": "text", "text": json.dumps(ANALYSIS)}]))
    )
    llm = recorded_models(
        monkeypatch,
        {GEMINI: gemini_recorder, CLAUDE: claude_recorder},
        index_model=GEMINI,
        agent_model=GEMINI,
        index_fallback_model=CLAUDE,
    )
    result = llm.structured("the document", DocumentAnalysis)

    assert result.title == "Master Services Agreement"
    assert len(gemini_recorder.requests) == len(claude_recorder.requests) == 1
    assert (
        f"INDEX_MODEL {GEMINI} failed, so INDEX_FALLBACK_MODEL {CLAUDE} is asked instead: Could "
        "not reach the provider of gemini-2.5-flash-lite."
    ) in caplog.text


def test_each_model_is_asked_with_its_own_provider_s_settings(monkeypatch):
    # Claude is told where to run in the body of the request, which another provider's API
    # would refuse; OpenRouter sends whatever extra body it is given.
    chunk = {
        "id": "gen-04",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "google/gemini-2.5-flash-lite",
        "choices": [{"index": 0, "delta": {"content": "Ninety days."}, "finish_reason": "stop"}],
    }
    reply = {
        "id": "gen-05",
        "object": "chat.completion",
        "created": 0,
        "model": "google/gemini-2.5-flash-lite",
        "provider": "Google",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": json.dumps(ANALYSIS)},
                "finish_reason": "stop",
            }
        ],
    }
    claude_recorder = Recorder(*[httpx2.Response(404, json=CLAUDE_RETIRED) for _ in range(2)])
    openrouter_recorder = Recorder(httpx2.Response(200, json=reply), openrouter_stream(chunk))
    llm = recorded_models(
        monkeypatch,
        {CLAUDE: claude_recorder, OPENROUTER: openrouter_recorder},
        index_model=CLAUDE,
        agent_model=CLAUDE,
        index_fallback_model=OPENROUTER,
        agent_fallback_model=OPENROUTER,
        data_region="us",
    )
    clock = [1000.0]
    monkeypatch.setattr("app.llm.time", SimpleNamespace(monotonic=lambda: clock[0]))
    llm.structured("the document", DocumentAnalysis)
    # A minute on, Claude is asked first again, and fails again.
    clock[0] += PASS_OVER_SECONDS
    with llm.stream_agent("Be brief.", QUESTION, []) as stream:
        list(stream)
        assert stream.response().text == "Ninety days."
    assert stream.model == OPENROUTER
    assert [claude_recorder.body(index)["inference_geo"] for index in (0, 1)] == ["us", "us"]
    assert "cache_control" in claude_recorder.body(1)
    for index in (0, 1):
        assert "inference_geo" not in openrouter_recorder.requests[index].content.decode()
        assert openrouter_recorder.body(index)["provider"] == {"data_collection": "deny"}
