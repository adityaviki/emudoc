"""Trying the models (app/trial.py, `python -m app.maintenance models`): each model the
settings name is asked what the app asks of it, alone, and the operator reads what came of it."""

import json

import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import BinaryContent, ModelResponse, TextPart
from pydantic_ai.models import Model
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

import app.maintenance as maintenance
from app.config import Settings, get_settings
from app.llm import LLM
from app.trial import PAGE_TEXT, describe_trials, scanned_page, try_models

PRIMARY = "scripted:model"
BACKUP = "backup:model"
NOTE = {"note_number": 12, "pallets": 40}
PAGE = {"blocks": [{"kind": "heading", "level": 1, "text": PAGE_TEXT}]}


def indexer(note: dict = NOTE, page: dict = PAGE) -> FunctionModel:
    """A model for indexing that reads the note and the page as `note` and `page` say."""

    def reply(messages: list, info: AgentInfo) -> ModelResponse:
        content = messages[0].parts[0].content
        scanned = any(isinstance(part, BinaryContent) for part in content)
        return ModelResponse(parts=[TextPart(json.dumps(page if scanned else note))])

    return FunctionModel(reply)


def answerer(calls_a_tool: bool = True) -> FunctionModel:
    async def turn(messages: list, info: AgentInfo):
        if calls_a_tool:
            yield {0: DeltaToolCall(name="list_documents", json_args="{}", tool_call_id="c1")}
        else:
            yield "The project holds a delivery note."

    return FunctionModel(stream_function=turn)


def both(**kwargs) -> FunctionModel:
    """A model that does both kinds of work."""
    index, answer = indexer(), answerer(**kwargs)
    return FunctionModel(index.function, stream_function=answer.stream_function)


def scripted(models: dict[str, Model]):
    def build(settings: Settings) -> LLM:
        llm = LLM(settings)
        llm._models.update(models)
        return llm

    return build


def configured(**changes: object) -> Settings:
    return Settings(
        _env_file=None,
        **{"index_model": PRIMARY, "agent_model": PRIMARY, **changes},
    )


def test_every_model_is_asked_what_the_app_asks_of_it(monkeypatch):
    # Costs are the process's prices, which the command's settings are.
    prices = f"{PRIMARY}=1/1,{BACKUP}=2/2"
    monkeypatch.setattr(get_settings(), "model_prices", prices)
    settings = configured(agent_fallback_model=BACKUP, model_prices=prices)
    trials = try_models(settings, scripted({PRIMARY: both(), BACKUP: answerer()}))

    assert [(trial.name, trial.variables) for trial in trials] == [
        (PRIMARY, ["INDEX_MODEL", "AGENT_MODEL"]),
        (BACKUP, ["AGENT_FALLBACK_MODEL"]),
    ]
    primary, backup = trials
    assert [step.asked for step in primary.steps] == [
        "an indexing request",
        "a scanned page",
        "a turn with the agent's tools",
    ]
    # A fallback is asked only what its kind of work asks, and is charged for itself.
    assert [step.asked for step in backup.steps] == ["a turn with the agent's tools"]
    assert list(backup.usage.models) == [BACKUP] and not any(t.failed for t in trials)

    lines = describe_trials(trials, settings)
    assert lines[0] == f"INDEX_MODEL and AGENT_MODEL: {PRIMARY}"
    assert lines[1].startswith("  ok      an indexing request: read note 12 and 40 pallets (")
    assert lines[2].startswith(f"  ok      a scanned page: read '{PAGE_TEXT}' (")
    assert lines[3].startswith("  ok      a turn with the agent's tools: called list_documents (")
    assert lines[4].startswith("          3 calls, ") and lines[4].endswith(", under USD 0.01")
    assert lines[-1] == "Every model answered as the app asks it (2 tried)."


def test_a_model_that_answers_in_a_way_the_app_cannot_use_fails_its_trial():
    wrong = FunctionModel(
        indexer(note={"note_number": 12, "pallets": 4}, page={"blocks": []}).function,
        stream_function=answerer(calls_a_tool=False).stream_function,
    )
    (trial,) = try_models(configured(), scripted({PRIMARY: wrong}))
    note, page, turn = trial.steps
    assert note.failed and note.outcome.startswith("read note 12 and 4 pallets where the note")
    assert page.failed and "so it would fail every scan and image" in page.outcome
    assert turn.failed and turn.outcome.startswith("answered without calling a tool")
    assert describe_trials([trial], configured())[-1].startswith(
        f"{PRIMARY} did not answer as the app asks; until it does, the work above fails or "
        "falls back."
    )


def test_a_model_that_fails_is_tried_alone_without_its_fallback():
    async def retired(messages: list, info: AgentInfo):
        raise ModelHTTPError(404, "model")
        yield ""

    settings = configured(agent_fallback_model=BACKUP)
    asked = []

    async def backup_turn(messages: list, info: AgentInfo):
        asked.append(messages)
        yield {0: DeltaToolCall(name="search", json_args="{}", tool_call_id="c1")}

    models = {
        PRIMARY: FunctionModel(indexer().function, stream_function=retired),
        BACKUP: FunctionModel(stream_function=backup_turn),
    }
    primary, backup = try_models(settings, scripted(models))
    turn = primary.steps[-1]
    assert turn.failed and turn.outcome.startswith("The model model was not found")
    # The backup was asked its own trial once, not the primary's in its place.
    assert len(asked) == 1 and not backup.failed


def test_a_model_that_cannot_be_set_up_says_what_to_set(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = configured(agent_fallback_model="anthropic:claude-haiku-5-5")
    _, claude = try_models(settings, scripted({PRIMARY: both()}))
    (step,) = claude.steps
    assert step.asked == "setting it up" and step.failed
    assert step.outcome == "Set ANTHROPIC_API_KEY in .env to use anthropic:claude-haiku-5-5."


def test_a_model_without_a_price_is_said_to_escape_the_spending_limits():
    settings = configured(model_prices=f"{BACKUP}=1/1")
    (trial,) = try_models(settings, scripted({PRIMARY: both()}))
    assert describe_trials([trial], settings)[4].endswith(
        "; MODEL_PRICES has no price for it, so the spending limits count none"
    )


def test_the_command_exits_with_1_when_a_model_fails(monkeypatch, capsys):
    monkeypatch.setattr(get_settings(), "agent_fallback_model", BACKUP)
    models = {
        get_settings().index_model: both(),
        BACKUP: FunctionModel(stream_function=answerer(calls_a_tool=False).stream_function),
    }
    monkeypatch.setattr(
        maintenance, "try_models", lambda settings: try_models(settings, scripted(models))
    )
    with pytest.raises(SystemExit) as stop:
        maintenance.main(["models"])
    assert stop.value.code == 1
    out = capsys.readouterr().out.splitlines()
    assert out[-1].startswith(f"{BACKUP} did not answer as the app asks")

    models[BACKUP] = answerer()
    maintenance.main(["models"])
    assert capsys.readouterr().out.splitlines()[-1] == (
        "Every model answered as the app asks it (2 tried)."
    )


def test_the_scanned_page_is_an_image():
    assert scanned_page().startswith(b"\x89PNG\r\n\x1a\n")
