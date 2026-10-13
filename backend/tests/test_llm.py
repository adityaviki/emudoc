"""The model layer, run through Pydantic AI against scripted models and recorded HTTP.

No request leaves the machine. Scripted models check what the app asks of a model and how it
reads the replies, whichever provider is configured. Recorded transports check that those
requests reach Gemini and Claude, through the real SDKs, in the form each API takes.
"""

import asyncio
import json
import os
import socket
import threading

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
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.profiles import ModelProfile

from app.agent.loop import run_agent
from app.config import Settings, export_env_file
from app.indexing.enrich import DocumentAnalysis
from app.indexing.pipeline import Services
from app.llm import INDEX_SYSTEM, LLM, OCR_PROMPT, LLMError, LLMUnavailable
from app.usage import ModelUsage

GEMINI = "google:gemini-2.5-flash-lite"
CLAUDE = "anthropic:claude-opus-5-5"
OPENROUTER = "openrouter:google/gemini-2.5-flash-lite"
SCRIPTED = "scripted:model"
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
    assert info.model_settings == {"max_tokens": 16000, "timeout": 300}
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
            "The API key for m was rejected. The provider said: bad key",
            False,
        ),
        ([ModelHTTPError(403, "m")], "This API key is not allowed to use m.", False),
        ([ModelHTTPError(404, "m")], "The model m was not found", False),
        # The provider, not the request, is the problem: worth asking again in a while.
        (
            [ModelHTTPError(429, "m", {"message": "Quota exceeded."})],
            "rate limit .* Quota exceeded.",
            True,
        ),
        ([ModelHTTPError(503, "m", "Overloaded.")], r"failed \(503\). .* Overloaded.", True),
        ([ModelHTTPError(400, "m", "No such field.")], r"failed \(400\). .* No such field.", False),
        ([ModelAPIError("m", "Connection error.")], "Could not reach the provider of m", True),
        # The provider took too long: the SDK's request timed out, which the Anthropic and
        # OpenAI adapters wrap and the Google one lets through as the HTTP library raised it.
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


# Recorded HTTP: the requests as Gemini and Claude receive them, through the real SDKs.


class Recorder:
    """A transport that answers from a queue and keeps every request it saw."""

    def __init__(self, *responses: httpx2.Response):
        self.responses = list(responses)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self.responses.pop(0)

    def body(self, index: int = 0) -> dict:
        return json.loads(self.requests[index].content)


# The module in which each provider looks up the function that builds its HTTP client.
CLIENT_FACTORY = {
    "google": "pydantic_ai.providers.google",
    "anthropic": "pydantic_ai.providers.anthropic",
    "openrouter": "pydantic_ai.providers._openai_compatible",
}


def recorded_llm(monkeypatch, recorder: Recorder, model: str, **settings) -> LLM:
    """An LLM built as the app builds it, except that its HTTP client answers from a recording."""
    provider = model.split(":")[0]
    monkeypatch.setenv(f"{provider.upper()}_API_KEY", "test-key")
    monkeypatch.setattr(
        f"{CLIENT_FACTORY[provider]}.create_async_httpx2_client",
        lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(recorder)),
    )
    return LLM(Settings(_env_file=None, index_model=model, agent_model=model, **settings))


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
    # The run's one request, with the token counts the provider reported, for the bill.
    assert (usage.calls, usage.input_tokens, usage.output_tokens) == (1, 25, 30)
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


def test_gemini_errors_keep_the_api_s_own_words(monkeypatch):
    error = {"code": 400, "message": "API key not valid.", "status": "INVALID_ARGUMENT"}
    recorder = Recorder(httpx2.Response(400, json={"error": error}))
    llm = recorded_llm(monkeypatch, recorder, GEMINI)
    with pytest.raises(LLMError, match=r"failed \(400\). The provider said: API key not valid."):
        llm.structured("the document", DocumentAnalysis)
    assert len(recorder.requests) == 1


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
    assert (usage.calls, usage.input_tokens, usage.output_tokens) == (1, 25, 30)
    request = recorder.requests[0]
    assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer test-key"
    body = recorder.body()
    assert body["model"] == "google/gemini-2.5-flash-lite" and body["max_tokens"] == 16000
    assert body["messages"][0] == {"role": "system", "content": INDEX_SYSTEM}
    assert body["response_format"]["type"] == "json_schema"


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
