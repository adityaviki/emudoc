"""The agent loop: the model calls the tools until it can answer, streamed as events."""

import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelResponseStreamEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from sqlalchemy.exc import TimeoutError as PoolTimeout

from app.agent.tools import TOOLS, ToolContext
from app.db import SessionLocal
from app.indexing.pipeline import Services
from app.llm import LLMError
from app.models import Project
from app.usage import ModelUsage

log = logging.getLogger(__name__)

MAX_TURNS = 12
MAX_RETRIES = 2

SYSTEM_PROMPT = """\
You answer questions about the documents in one project of a document workspace. You learn \
what the documents say only through your tools, so every statement about them must rest on \
tool results from this conversation.

The documents are indexed three ways, and each tool reads one index:
- list_documents and get_outline read the map: a catalogue of the documents, and each \
document's outline with a one-sentence summary per section.
- search reads the passage index, combining keyword and semantic matching.
- query_facts reads the fact table: typed fields extracted from each document, such as \
dates, parties, amounts and identifiers.
- read returns exact source text.

Documents are filed in folders, shown as paths such as Invoices/2024. list_documents lists the \
folders; list_documents, search and query_facts take a `folder` to work within one folder and \
its subfolders. Use it when the user names a folder or asks about part of the project.

Choosing a route:
- For a specific detail, search, then read around the best hit if the passage alone does \
not settle the question.
- For a broad question or a summary, start from list_documents or get_outline and read the \
sections that matter.
- When the answer must cover every matching document (totals, "which documents", "do all \
of them"), start from query_facts or list_documents. Search returns only the top matches, \
so it cannot show that something is absent or count how often it occurs.

Summaries in the catalogue and the outlines were written by an indexing model as signposts. \
Use them to decide where to look. Base the answer on passage text and facts, and read the \
source whenever precision matters: amounts, dates, obligations, exact wording.

Citing: passages carry references such as [P123]. Put the reference straight after each \
statement you take from a passage, like this: The notice period is 90 days [P812]. Cite \
only references that appeared in tool results in this conversation. Rows from query_facts \
name the passage each fact came from; cite that one.

If the documents do not contain the answer, say so and say what you checked. If a question \
could refer to several documents, say which ones you looked at. Lead with the answer, then \
give the supporting detail. Quote the exact words when the user asks what a document says."""


def run_agent(
    services: Services,
    project_id: int,
    messages: list[dict[str, str]],
    usage: ModelUsage | None = None,
) -> Iterator[dict[str, Any]]:
    """Run one user turn to completion, yielding events for the client as it goes.

    The loop keeps no database session: it opens one to read the project for the prompt and
    one per tool call, closed with the call. So an answer holds a connection only while a tool
    runs, never while the model thinks, which is most of its time; every call reads a snapshot
    of its own; and whatever a call leaves open, or a failure inside it, reaches neither the
    next call nor the caller's session.

    `usage` is filled with each model call as it is answered, so a caller that leaves the
    loop before its `done` event still knows what the calls so far cost (app/usage.py).
    """
    if usage is None:
        usage = ModelUsage()
    with SessionLocal() as session:
        project = session.get_one(Project, project_id)
        system = (
            f"{SYSTEM_PROMPT}\n\nProject: {project.name}"
            + (f" ({project.description})" if project.description else "")
            # Read in UTC, like every timestamp the app stores, so the worker host's zone
            # cannot move it; the label tells the model which zone the date is in.
            + f"\nToday's date (UTC): {datetime.now(UTC).date().isoformat()}"
        )
    tools = [tool.definition() for tool in TOOLS.values()]
    conversation: list[ModelMessage] = [_chat_message(message) for message in messages]
    # Passages shown to the model so far, keyed by id: the only references an answer may cite.
    sources: dict[int, dict[str, Any]] = {}
    retries = 0
    turns = 0

    while turns < MAX_TURNS:
        try:
            with services.llm.stream_agent(system, conversation, tools) as stream:
                for event in stream:
                    if isinstance(event, PartStartEvent) and isinstance(event.part, ThinkingPart):
                        yield {"type": "thinking"}
                    elif text := _new_text(event):
                        yield {"type": "text", "text": text}
                response = stream.response()
        except LLMError as error:
            yield {"type": "error", "message": str(error)}
            return

        usage.add(response.usage)

        if response.finish_reason == "content_filter":
            yield {
                "type": "error",
                "message": "The model declined to answer this request.",
            }
            return

        calls = response.tool_calls
        if response.finish_reason == "length":
            if not calls:
                yield {
                    "type": "notice",
                    "message": "The answer was cut off at the length limit.",
                }
                break
            # An input cut off by the limit can still parse as a partial object; never run it.
            yield {
                "type": "error",
                "message": "A tool call was cut off. Please ask again.",
            }
            return

        inputs = _tool_inputs(calls)
        answered = bool(calls or (response.text or "").strip())
        if response.finish_reason == "error" or inputs is None or not answered:
            # Nothing to act on: a reply the provider itself reports as broken (Gemini does
            # when it fumbles a function call), tool arguments that are not JSON, or neither
            # a tool call nor text. It is left out of the conversation and the turn reissued.
            retries += 1
            if retries > MAX_RETRIES:
                yield {
                    "type": "error",
                    "message": "The model kept sending replies that could not be used.",
                }
                return
            yield {"type": "retry"}
            continue

        retries = 0
        turns += 1
        if not calls:
            break

        results = []
        for call, arguments in zip(calls, inputs, strict=True):
            yield {
                "type": "tool_call",
                "id": call.tool_call_id,
                "name": call.tool_name,
                "input": arguments,
            }
            known = set(sources)
            with SessionLocal() as session:
                context = ToolContext(session, services.embedder, project_id, sources=sources)
                output, is_error = _run_tool(context, call.tool_name, arguments)
            new_sources = [source for key, source in sources.items() if key not in known]
            if new_sources:
                yield {"type": "sources", "sources": new_sources}
            yield {
                "type": "tool_result",
                "id": call.tool_call_id,
                "summary": output.split("\n", 1)[0][:200],
                "is_error": is_error,
            }
            results.append(
                ToolReturnPart(
                    call.tool_name,
                    output,
                    call.tool_call_id,
                    outcome="failed" if is_error else "success",
                )
            )

        conversation.append(response)
        # All results go back in one message so parallel tool calls stay parallel.
        conversation.append(ModelRequest(parts=results))
    else:
        yield {
            "type": "notice",
            "message": f"Stopped after {MAX_TURNS} rounds of tool calls.",
        }

    yield {
        "type": "done",
        "usage": {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens},
    }


def _chat_message(message: dict[str, str]) -> ModelMessage:
    """One turn of the chat so far, as the model layer takes it."""
    if message["role"] == "user":
        return ModelRequest(parts=[UserPromptPart(message["content"])])
    return ModelResponse(parts=[TextPart(message["content"])])


def _new_text(event: ModelResponseStreamEvent) -> str | None:
    """The text a stream event adds to the answer, if it adds any."""
    if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
        return event.part.content
    if isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
        return event.delta.content_delta
    return None


def _tool_inputs(calls: list[ToolCallPart]) -> list[dict[str, Any]] | None:
    """Each call's arguments as an object, or None if any of them is not valid JSON."""
    try:
        return [call.args_as_dict(raise_if_invalid=True) for call in calls]
    except (ValueError, AssertionError):
        return None


def _run_tool(context: ToolContext, name: str, raw_input: Any) -> tuple[str, bool]:
    """Validate and run one tool call. Returns the result text and whether it is an error."""
    tool = TOOLS.get(name)
    if tool is None:
        return f"Unknown tool {name!r}.", True
    try:
        arguments = tool.input_model.model_validate(raw_input)
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in item['loc']) or 'input'}: {item['msg']}"
            for item in error.errors()
        )
        return json.dumps({"INVALID_JSON": json.dumps(raw_input), "problems": problems}), True
    try:
        return tool.run(context, arguments), False
    except PoolTimeout:
        # Not the tool's doing, and nothing the model can work around: the answer ends with the
        # sentence that says to try again (`answer` in app/answers.py).
        raise
    except Exception:
        log.exception("Tool %s failed", name)
        return "The tool failed unexpectedly. Try a different approach.", True
