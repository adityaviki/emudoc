"""Trying the models: each model the settings name is asked what the app asks of it, so the
operator sees every one answer before it is needed (`python -m app.maintenance models`).

A start builds the models and asks none (`check_model` in app/startup.py): an answer at every
start of every replica costs tokens, and a primary that is down would hold a release its
fallback could serve. So a model is tried only here, and a fallback, which serves only when its
primary cannot, is tried nowhere else before its day. Run it after changing a model, before a
launch, and at intervals, since a provider retires a model on its own schedule.

A model for indexing is asked one structured request, two facts of a short delivery note, and
one scanned page, an image the app draws with a number on it: a model that cannot read images
would fail every scan. A model for answering is asked one streamed turn with the agent's tools
and told to call one: a model that answers without calling a tool would answer questions
without reading the documents. Each model is asked alone, its fallback not standing in, and
nothing is recorded as an organisation's usage: the tokens are the operator's, and the output
says what they cost by MODEL_PRICES.
"""

import io
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel
from pydantic_ai.messages import ModelRequest, UserPromptPart

from app.agent.tools import TOOLS
from app.config import AGENT, INDEX, Settings, listing
from app.llm import LLM, LLMError
from app.usage import ModelUsage, money

# The delivery note the indexing request reads, and what it says.
NOTE = (
    "Pull the delivery note's number and the number of pallets out of this text.\n\n"
    "Delivery note 12\nDelivered on 3 March 2026: 40 pallets of copy paper, signed by J. Ortiz."
)
NOTE_SAYS = (12, 40)
# What the scanned page shows.
PAGE_TEXT = "Invoice 4711"
PAGE_NUMBER = "4711"
# The smallest cost `money` shows, in millionths.
CENT = 10_000
TOOL_TURN = (
    "This is a check that you can call tools. Call list_documents once, with no folder, and "
    "nothing else."
)


class DeliveryNote(BaseModel):
    """What the indexing request asks for."""

    note_number: int
    pallets: int


class Unfit(Exception):
    """A model answered, but not as the app needs it to."""


@dataclass
class Step:
    """One request of a trial: what was asked, what came of it, and how long it took."""

    asked: str
    outcome: str
    seconds: float
    failed: bool


@dataclass
class Trial:
    """What one model did, asked as the settings that name it use it."""

    name: str
    variables: list[str]
    steps: list[Step] = field(default_factory=list)
    usage: ModelUsage = field(default_factory=ModelUsage)

    @property
    def failed(self) -> bool:
        return any(step.failed for step in self.steps)


def try_models(settings: Settings, build: Callable[[Settings], LLM] = LLM) -> list[Trial]:
    """Ask each model the settings name, once for every kind of work it does, in the order
    the settings list them; a model named twice is tried once. `build` makes the model layer
    from settings that name one model alone (tests pass one over scripted models)."""
    named: dict[str, list[str]] = {}
    roles: dict[str, set[str]] = {}
    for model in settings.models:
        named.setdefault(model.name, []).append(model.variable)
        roles.setdefault(model.name, set()).add(model.role)
    return [
        _try(settings, build, name, variables, roles[name]) for name, variables in named.items()
    ]


def _try(
    settings: Settings,
    build: Callable[[Settings], LLM],
    name: str,
    variables: list[str],
    roles: set[str],
) -> Trial:
    trial = Trial(name, variables)
    # The model alone: as the first model of every kind of work, with no fallback to stand in.
    alone = settings.model_copy(
        update={
            "index_model": name,
            "agent_model": name,
            "index_fallback_model": "",
            "agent_fallback_model": "",
        }
    )
    llm = build(alone)
    if problem := llm.problem():
        trial.steps.append(Step("setting it up", problem, 0.0, failed=True))
        return trial
    if INDEX in roles:
        trial.steps.append(_step("an indexing request", lambda: _note(llm, trial.usage)))
        trial.steps.append(_step("a scanned page", lambda: _page(llm, trial.usage)))
    if AGENT in roles:
        trial.steps.append(_step("a turn with the agent's tools", lambda: _turn(llm, trial.usage)))
    return trial


def _step(asked: str, ask: Callable[[], str]) -> Step:
    started = time.monotonic()
    try:
        outcome, failed = ask(), False
    except (LLMError, Unfit) as error:
        outcome, failed = str(error), True
    return Step(asked, outcome, time.monotonic() - started, failed)


def _note(llm: LLM, usage: ModelUsage) -> str:
    note = llm.structured(NOTE, DeliveryNote, usage)
    read = (note.note_number, note.pallets)
    if read != NOTE_SAYS:
        raise Unfit(
            f"read note {read[0]} and {read[1]} pallets where the note says {NOTE_SAYS[0]} and "
            f"{NOTE_SAYS[1]}, so its facts and summaries could not be trusted."
        )
    return f"read note {note.note_number} and {note.pallets} pallets"


def _page(llm: LLM, usage: ModelUsage) -> str:
    text = " ".join(block.text for block in llm.ocr_page(scanned_page(), "image/png", usage))
    if PAGE_NUMBER not in text:
        raise Unfit(
            f"read {text.strip()!r} from a page that says {PAGE_TEXT!r}, so it would fail "
            "every scan and image; give INDEX_MODEL one that reads images."
        )
    return f"read {text.strip()!r}"


def _turn(llm: LLM, usage: ModelUsage) -> str:
    question = [ModelRequest(parts=[UserPromptPart("Which documents are in the project?")])]
    tools = [tool.definition() for tool in TOOLS.values()]
    with llm.stream_agent(TOOL_TURN, question, tools) as stream:
        response = stream.response()
    usage.add(stream.model, response.usage)
    if not response.tool_calls:
        raise Unfit(
            "answered without calling a tool, so it would answer questions without reading "
            "the documents; give AGENT_MODEL one that calls tools."
        )
    return f"called {', '.join(call.tool_name for call in response.tool_calls)}"


def scanned_page() -> bytes:
    """A page with PAGE_TEXT on it, as a scan without a text layer shows it to the model."""
    image = Image.new("RGB", (640, 160), "white")
    font = ImageFont.load_default(size=56)
    ImageDraw.Draw(image).text((32, 40), PAGE_TEXT, fill="black", font=font)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def describe_trials(trials: list[Trial], settings: Settings) -> list[str]:
    """The lines the `models` command prints: each model, each request and its outcome, what
    the requests used, and a last line that says whether every model answered."""
    lines: list[str] = []
    for trial in trials:
        lines.append(f"{listing(trial.variables)}: {trial.name}")
        for step in trial.steps:
            verdict = "FAILED" if step.failed else "ok"
            lines.append(f"  {verdict:6}  {step.asked}: {step.outcome} ({step.seconds:.1f} s)")
        if trial.usage.calls:
            lines.append(f"          {_spent(trial, settings)}")
    failed = [trial.name for trial in trials if trial.failed]
    if failed:
        lines.append(
            f"{listing(failed)} did not answer as the app asks; until "
            f"{'it does' if len(failed) == 1 else 'they do'}, the work above fails or falls "
            "back. Fix the setting or the key, or name another model."
        )
    else:
        lines.append(f"Every model answered as the app asks it ({len(trials)} tried).")
    return lines


def _spent(trial: Trial, settings: Settings) -> str:
    spent = trial.usage.spent()
    used = (
        f"{spent['calls']} call{'s' if spent['calls'] != 1 else ''}, "
        f"{spent['input_tokens']:,} tokens in, {spent['output_tokens']:,} out"
    )
    if (cost := spent["cost_micros"]) is not None:
        # A trial costs a fraction of a cent, which two decimals would show as nothing.
        return f"{used}, {money(cost) if not 0 < cost < CENT else 'under ' + money(CENT)}"
    if trial.name not in settings.prices:
        # A model without a price answers outside every spending limit (app/spending.py).
        return f"{used}; MODEL_PRICES has no price for it, so the spending limits count none"
    return used
