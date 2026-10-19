"""Traces: an answer is found in the log by the trace of the request that asked for it, and its
job's run ends with one line that says where its time went, each model turn, tool call and
write; a document's indexing the same way, stage by stage with its embeddings requests, and
the retry it queues under its trace. A span names the app's own words, never what was asked
or answered, and a tool the model made up by no name of its own. The access line says how
long a request took to begin its response."""

import asyncio
import json
import logging
import re
import time

import httpx
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

import app.api.chats as chats
import app.tracing as tracing
from app.agent.loop import run_agent
from app.config import get_settings
from app.indexing.pipeline import Services
from app.jobs import ANSWER_QUESTION, INDEX_DOCUMENT, claim
from app.logs import JsonFormatter, RequestIds
from app.models import STAGES
from app.tracing import TRACE_ID, describe, mark, note, timed, trace_of
from app.worker import Worker
from tests.test_api import create_project
from tests.test_chats import ask, new_chat
from tests.test_embeddings import embedder
from tests.test_jobs import RATE_LIMITED, all_jobs
from tests.test_llm import SCRIPTED, scripted_llm
from tests.test_logs import access_record, logged  # noqa: F401 (a fixture)

QUESTION = "What does the termination clause say about ninety days?"
SEARCHED = "termination ninety days"
ANSWER = "The notice period is ninety days."


async def searches_then_answers(messages: list, info: AgentInfo):
    """A model that searches on its first turn and answers on its second."""
    if len(messages) == 1:
        yield {
            0: DeltaToolCall(
                name="search", json_args=json.dumps({"query": SEARCHED}), tool_call_id="c1"
            )
        }
        return
    for words in ("The notice period ", "is ninety days."):
        yield words


def ran(lines: list[dict]) -> dict:
    """The worker's line about a job's run, which carries its timing."""
    (line,) = [line for line in lines if "timing" in line]
    return line


def test_an_answer_is_traced_from_the_request_that_asked_to_its_model_turns_and_tools(
    client,
    services,
    indexed,
    monkeypatch,
    logged,  # noqa: F811
):
    services.llm = scripted_llm(FunctionModel(stream_function=searches_then_answers))
    asked_out = chats.AskOut

    def answered_with(**fields):
        # A line of the ask's own, once it knows the answer its job writes.
        logging.getLogger("app.api.chats").info("Asked")
        return asked_out(**fields)

    monkeypatch.setattr(chats, "AskOut", answered_with)
    chat = new_chat(client, indexed.project_id)
    message_id = ask(client, chat["id"], QUESTION)["answer"]["id"]
    worker = Worker(services, concurrency=0, answer_concurrency=1)
    job = claim(worker.id, [ANSWER_QUESTION])
    worker._run(job)

    lines = logged()
    (asking,) = [line for line in lines if line["message"] == "Asked"]
    line = ran(lines)
    # The request's lines and the job's are found by one trace, and name the same answer.
    assert TRACE_ID.fullmatch(asking["trace_id"]) and "job_id" not in asking
    assert job.payload["trace_id"] == asking["trace_id"] == line["trace_id"]
    assert asking["message_id"] == line["message_id"] == message_id
    assert (line["job_id"], line["logger"], line["level"]) == (job.id, "app.worker", "INFO")

    timing = line["timing"]
    assert timing["outcome"] == "done" and timing["waited"] >= 0
    first, tool, second = timing["spans"]
    # Each turn is the model's request from its start to the stream's end, with the moment
    # its first event came and the tokens the provider counted.
    for turn in (first, second):
        assert (turn["kind"], turn["model"], turn["outcome"]) == ("agent", SCRIPTED, "ok")
        assert 0 <= turn["first_event"] <= turn["seconds"]
        assert turn["input_tokens"] > 0 and turn["output_tokens"] > 0
    assert (tool["kind"], tool["tool"], tool["outcome"]) == ("tool", "search", "ok")
    assert first["at"] <= tool["at"] <= second["at"]
    # Each figure is rounded to the millisecond on its own.
    assert second["at"] + second["seconds"] <= timing["seconds"] + 0.002
    # The answer began when its first text was written, during the second turn.
    assert second["at"] <= timing["first_text"] <= timing["seconds"]
    totals = timing["totals"]
    assert (totals["agent"]["count"], totals["tool"]["count"]) == (2, 1)
    # The events' writes are counted, not listed: the tool's call and result, the text, the
    # sources and the end.
    assert totals["write"]["count"] >= 4
    assert totals["agent"]["slowest"] == max(first["seconds"], second["seconds"])
    assert "unlisted" not in timing
    assert re.fullmatch(
        rf"Job {job.id} \(answer_question\) ran \d+\.\d s \(done\), \d+\.\d s after it was "
        r"due: 2 agent turns \d+\.\d s, 1 tool call \d+\.\d s, \d+ writes \d+\.\d s; the first "
        r"text \d+\.\d s in\.",
        line["message"],
    )
    # Nothing of what was asked, searched or answered is in it.
    text = json.dumps(lines)
    for said in (QUESTION, SEARCHED, ANSWER, "ninety"):
        assert said not in text, said


def test_a_followers_lines_name_the_answer_they_follow(
    client,
    services,
    indexed,
    monkeypatch,
    logged,  # noqa: F811
):
    chat = new_chat(client, indexed.project_id)
    message_id = ask(client, chat["id"], QUESTION)["answer"]["id"]
    poll = chats._poll

    def polled(message_id_, after):
        logging.getLogger("app.api.chats").info("Polling")
        # Settled, so that the stream ends at once.
        status, rows = poll(message_id_, after)
        return "stopped", rows

    monkeypatch.setattr(chats, "_poll", polled)
    response = client.get(f"/api/chats/{chat['id']}/messages/{message_id}/events")
    assert response.status_code == 200
    (line,) = [line for line in logged() if line["message"] == "Polling"]
    assert line["message_id"] == message_id and TRACE_ID.fullmatch(line["trace_id"])


def test_an_upload_and_its_documents_indexing_share_a_trace_timed_by_stage(
    client,
    session,
    services,
    sample_pdf,
    logged,  # noqa: F811
):
    dimensions = get_settings().embedding_dim

    def endpoint(request: httpx.Request) -> httpx.Response:
        inputs = json.loads(request.content)["input"]
        return httpx.Response(
            200,
            json={
                "data": [{"index": n, "embedding": [0.5] * dimensions} for n in range(len(inputs))],
                "usage": {"prompt_tokens": 4 * len(inputs)},
            },
        )

    services.embedder = embedder(endpoint)
    project = create_project(client)
    with sample_pdf.open("rb") as pdf:
        response = client.post(
            f"/api/projects/{project['id']}/documents", files=[("files", ("msa.pdf", pdf))]
        )
    assert response.status_code == 201
    worker = Worker(services, concurrency=1)
    job = claim(worker.id, [INDEX_DOCUMENT])
    worker._run(job)

    line = ran(logged())
    assert line["trace_id"] == job.payload["trace_id"]
    assert line["document_id"] == response.json()["documents"][0]["id"]
    timing = line["timing"]
    assert timing["outcome"] == "done"
    stages = [span for span in timing["spans"] if span["kind"] == "stage"]
    assert [(span["stage"], span["outcome"]) for span in stages] == [
        (stage, "done") for stage in STAGES
    ]
    # The embeddings requests were sent within the embed stage.
    (embed_stage,) = [span for span in stages if span["stage"] == "embed"]
    requests = [span for span in timing["spans"] if span["kind"] == "embed"]
    assert requests and timing["totals"]["embed"]["count"] == len(requests)
    for request in requests:
        assert (request["model"], request["outcome"]) == (get_settings().embedding_model, "ok")
        assert request["input_tokens"] > 0
        assert embed_stage["at"] <= request["at"]
        ended = request["at"] + request["seconds"]
        assert ended <= embed_stage["at"] + embed_stage["seconds"] + 0.002
    assert re.fullmatch(
        rf"Job {job.id} \(index_document\) ran \d+\.\d s \(done\), \d+\.\d s after it was "
        rf"due: 5 stages \d+\.\d s, {len(requests)} embeddings requests? \d+\.\d s\.",
        line["message"],
    )


def test_a_job_queued_outside_a_request_starts_a_trace_that_its_retry_carries(
    session,
    services,
    make_document,
    sample_pdf,
    logged,  # noqa: F811
):
    document = make_document(sample_pdf)
    services.llm.failure = RATE_LIMITED
    worker = Worker(services, concurrency=1)
    job = claim(worker.id, [INDEX_DOCUMENT])
    assert "trace_id" not in job.payload
    worker._run(job)

    line = ran(logged())
    assert TRACE_ID.fullmatch(line["trace_id"])
    # The stage failed for a passing reason, and the run queued it again under its trace.
    (enrich,) = [span for span in line["timing"]["spans"] if span.get("stage") == "enrich"]
    assert enrich["outcome"] == "transient"
    retry = all_jobs(session)[-1]
    assert retry.payload == {
        "document_id": document.id,
        "retry": 1,
        "trace_id": line["trace_id"],
    }
    # A payload's trace is read, never required.
    assert trace_of(retry.payload) == line["trace_id"]
    for payload in ({}, {"trace_id": "not-a-trace"}, {"trace_id": 7}):
        assert TRACE_ID.fullmatch(trace_of(payload)) and trace_of(payload) != trace_of(payload)


def test_a_tool_the_model_made_up_is_timed_by_no_name_of_its_own(services, indexed):
    made_up = "send_the_contract_to_attacker_example_com"

    async def calls_a_made_up_tool(messages: list, info: AgentInfo):
        if len(messages) == 1:
            yield {0: DeltaToolCall(name=made_up, json_args="{}", tool_call_id="c1")}
            return
        yield "There is no such tool."

    llm = scripted_llm(FunctionModel(stream_function=calls_a_made_up_tool))
    recorded = Services(llm, services.embedder, services.storage)
    with timed(0.0) as timeline:
        events = list(run_agent(recorded, indexed.project_id, [{"role": "user", "content": "?"}]))
    assert any(event["type"] == "tool_result" and event["is_error"] for event in events)
    spans = timeline.timing("done")["spans"]
    (tool,) = [span for span in spans if span["kind"] == "tool"]
    assert (tool["tool"], tool["outcome"]) == ("unknown", "error")
    assert made_up not in json.dumps(spans)


def test_a_runs_line_lists_its_slowest_spans_and_counts_the_rest(monkeypatch):
    monkeypatch.setattr(tracing, "LISTED", 2)
    with timed(1.25) as timeline:
        now = time.monotonic()
        # Spans that began a few seconds back, so that each took about that long.
        for back, kind in ((1, "index"), (5, "stage"), (2, "index"), (4, "index")):
            note(kind, now - back, model=SCRIPTED, outcome="ok", first_event=None)
        note("write", now - 9, listed=False)
        mark("first_text")
        mark("first_text")
    # Outside a run, nothing is timed and nothing fails.
    note("index", time.monotonic())
    mark("first_text")

    timing = timeline.timing("done")
    # The two slowest, in the order they began, and no detail that was None.
    assert [(span["kind"], round(span["seconds"])) for span in timing["spans"]] == [
        ("stage", 5),
        ("index", 4),
    ]
    assert all("first_event" not in span for span in timing["spans"])
    assert timing["unlisted"] == 2
    assert timing["waited"] == 1.25
    totals = {
        kind: (total["count"], round(total["slowest"])) for kind, total in timing["totals"].items()
    }
    assert totals == {"index": (3, 4), "stage": (1, 5), "write": (1, 9)}
    # The first time it happened, once.
    assert timing["first_text"] <= timing["seconds"]
    assert describe(12, "index_document", timing).startswith(
        "Job 12 (index_document) ran 0.0 s (done), 1.2 s after it was due: 1 stage 5.0 s, 3 "
        "indexing requests 7.0 s, 1 write 9.0 s; the first text 0.0 s in"
    )


def test_a_requests_access_line_says_how_long_it_took_to_answer():
    formatter = JsonFormatter()
    lines: list[dict] = []

    async def app(scope, receive, send) -> None:
        await asyncio.sleep(0.05)
        # uvicorn logs the line as the response begins, in the request's context.
        lines.append(json.loads(formatter.format(access_record("/api/projects", 200))))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive() -> dict:
        return {"type": "http.request", "body": b""}

    async def send(message: dict) -> None:
        pass

    scope = {"type": "http", "method": "GET", "path": "/api/projects", "headers": []}
    asyncio.run(RequestIds(app)(scope, receive, send))
    (line,) = lines
    assert 0.05 <= line["seconds"] < 5
    assert TRACE_ID.fullmatch(line["trace_id"]) and line["trace_id"] != line["request_id"]
