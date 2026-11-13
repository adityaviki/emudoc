"""Traces: where the time of one answer, or of one document's indexing, went, from the request
that asked for it to each model request and tool call, in the log the deployment keeps already.

A trace is an id that every line of one piece of work names (`trace_id`, `Context` in
app/logs.py). A request starts one (`RequestIds`), a job queued while it runs carries it in its
payload (`enqueue` and `queue_index` in app/jobs.py), and the worker logs that job's lines under
it: the lines of an upload's request and of each of its documents' indexing are found by one
id, the retries included, which a run queues under its own trace. A job queued outside a
request, by a maintenance command or the housekeeping, starts a trace when it runs (`trace_of`).

The worker times each run of a job (`timed`, in `Worker._run`): how long the job waited from the
moment it was due, then the spans the run went through, each with the moment it began and how
long it took. The stages (app/indexing/pipeline.py), the requests to the models and the
embeddings endpoint (`ModelRequest` in app/metrics.py, which measures them for the metrics
already, on whichever thread sends them), the agent's tool calls (app/agent/loop.py), the
writes of an answer's events (app/answers.py), and the moment its first text was written. At
the end of the run it logs one line, "Job 812 (answer_question) ran 48.2 s (done) …", whose
`timing` field holds them all (deploying.md, "Traces"): a slow answer a member reports, or the
slowest of the hour, is read from one line, where the metrics only say that some were slow.

The log rather than OpenTelemetry's SDK, on purpose. The log is kept as long as the deployment
keeps its server logs, while spans sent to a tracing service would be one more store of
members' ids to hold to that period, or one more company to send them to. And a global tracer
provider turns on spans the app did not write: FastAPI's, with each request's path and query,
and Anthropic's SDK's (app/metrics.py's docstring). The ids have the shape of a W3C trace's, so
a collector that turns log lines into spans can keep them.

A span holds the app's own words: its kind, a model's configured name, a stage, a tool among
the agent's own (a name the model made up is "unknown"), how it ended, tokens and seconds;
never what a tool was asked, a passage or a reply. Outside a job's run, in a request say,
nothing is timed: the request's time is its access line's (`seconds`, app/logs.py).

This module imports nothing of the app, so that app/logs.py and app/metrics.py can import it.
"""

import re
import secrets
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

# What a trace's id looks like: 32 hex digits, as W3C trace context writes one.
TRACE_ID = re.compile(r"[0-9a-f]{32}")
# The kinds of span beside the models' work (INDEX and AGENT in app/config.py, EMBED in
# app/metrics.py), which `ModelRequest` names its spans by.
STAGE = "stage"
TOOL = "tool"
WRITE = "write"
# A PDF page drawn as an image (app/rendering.py): for OCR, or drawn ahead.
RENDER = "render"
# A file parsed in a child process, the wait for one of the worker's parses included
# (app/indexing/pipeline.py).
PARSE = "parse"
# A tool name the model sent that is not one of the agent's tools: the name is the model's
# words, which a document can steer, so the span names it by this instead.
UNKNOWN_TOOL = "unknown"
# The moment an answer's first text was written, which a member sees it begin by.
FIRST_TEXT = "first_text"
# How many spans a run's line lists one by one: the slowest, in the order they began. The rest
# are counted in its totals alone, since a scan of three hundred pages makes as many requests,
# and journald splits a line past 48 KiB (its LineMax), which then no longer reads as JSON.
LISTED = 50
# How a run's sentence counts each kind of span, in the order it names them: the outer ones
# before what they wait on (a search waits on an embeddings request).
NOUNS = {
    STAGE: ("stage", "stages"),
    PARSE: ("parse", "parses"),
    "agent": ("agent turn", "agent turns"),
    RENDER: ("page drawn", "pages drawn"),
    "index": ("indexing request", "indexing requests"),
    TOOL: ("tool call", "tool calls"),
    "embed": ("embeddings request", "embeddings requests"),
    WRITE: ("write", "writes"),
}

type Detail = str | int | float | bool


def new_trace() -> str:
    """A trace's id, for a request or a job that starts one."""
    return secrets.token_hex(16)


def trace_of(payload: dict) -> str:
    """The trace a job was queued under, or a new one for a job queued outside a request. The
    payload is read, never required: a job queued before traces were carried has none."""
    trace = payload.get("trace_id")
    return trace if isinstance(trace, str) and TRACE_ID.fullmatch(trace) else new_trace()


class Timing(dict):
    """What a run's line writes as its `timing` field. The JSON format writes a record's
    `timing` only when it is one of these, which only this module makes (app/logs.py)."""


@dataclass
class _Total:
    count: int = 0
    seconds: float = 0.0
    slowest: float = 0.0


class Timeline:
    """One run of a job as the worker times it: when it began, how long the job waited for a
    worker, and its spans and marks, which any thread may add while the run lasts, since a
    model's request is sent from the model loop's thread (app/llm.py)."""

    def __init__(self, waited: float):
        self.began = time.monotonic()
        self.waited = waited
        self._spans: list[dict[str, Detail]] = []
        self._totals: dict[str, _Total] = {}
        self._marks: dict[str, float] = {}
        self._lock = threading.Lock()

    def add(self, kind: str, began: float, *, listed: bool = True, **details: Detail) -> None:
        """A span of `kind` that began at `began`, a `time.monotonic()`, and ends now. One
        that is not `listed` is counted in the totals alone: the writes of an answer's events
        are many and short, and would crowd out the spans worth reading."""
        seconds = time.monotonic() - began
        with self._lock:
            total = self._totals.setdefault(kind, _Total())
            total.count += 1
            total.seconds += seconds
            total.slowest = max(total.slowest, seconds)
            if listed:
                self._spans.append(
                    {"kind": kind, "at": began - self.began, "seconds": seconds, **details}
                )

    def mark(self, name: str) -> float | None:
        """Note the moment `name` first happened in the run. That first time, returns how long
        the job had been due by then: its wait for a worker and the run so far."""
        with self._lock:
            if name in self._marks:
                return None
            at = self._marks[name] = time.monotonic() - self.began
        return self.waited + at

    def timing(self, outcome: str) -> Timing:
        """The run so far, ended as `outcome` (as `JOB_RUNS` calls it, app/metrics.py)."""
        seconds = time.monotonic() - self.began
        with self._lock:
            slowest = sorted(self._spans, key=lambda span: span["seconds"], reverse=True)
            listed = sorted(slowest[:LISTED], key=lambda span: span["at"])
            timing = Timing(
                outcome=outcome,
                seconds=_rounded(seconds),
                waited=_rounded(self.waited),
                **{name: _rounded(at) for name, at in self._marks.items()},
                totals={
                    kind: {
                        "count": total.count,
                        "seconds": _rounded(total.seconds),
                        "slowest": _rounded(total.slowest),
                    }
                    for kind, total in sorted(self._totals.items(), key=_in_order)
                },
                spans=[
                    {
                        name: _rounded(value) if name in ("at", "seconds") else value
                        for name, value in span.items()
                    }
                    for span in listed
                ],
            )
            if unlisted := len(slowest) - len(listed):
                timing["unlisted"] = unlisted
        return timing


def _rounded(seconds: float) -> float:
    return round(seconds, 3)


def _in_order(item: tuple[str, _Total]) -> int:
    """Where a kind's total goes: in the order the sentence names them (`NOUNS`)."""
    return list(NOUNS).index(item[0]) if item[0] in NOUNS else len(NOUNS)


_timeline: ContextVar[Timeline | None] = ContextVar("timeline", default=None)


@contextmanager
def timed(waited: float) -> Iterator[Timeline]:
    """Time the block as one run of a job, which waited `waited` seconds for a worker. The spans
    noted in it are its, on every thread its context reaches: the model loop runs each request
    in a copy of the context of the thread that sent it."""
    timeline = Timeline(waited)
    token = _timeline.set(timeline)
    try:
        yield timeline
    finally:
        _timeline.reset(token)


def note(kind: str, began: float, *, listed: bool = True, **details: Detail | None) -> None:
    """Add a span of `kind` that began at `began`, a `time.monotonic()`, and ends now to the run
    being timed, if any; a detail that is None is left out."""
    if (timeline := _timeline.get()) is not None:
        found = {name: value for name, value in details.items() if value is not None}
        timeline.add(kind, began, listed=listed, **found)


@contextmanager
def span(kind: str, *, listed: bool = True, **details: Detail) -> Iterator[dict[str, Detail]]:
    """Time the block as a span of `kind` of the run being timed. The dict it yields takes what
    the block learns, such as how it ended; one that raises ended "failed" unless it said."""
    began = time.monotonic()
    found = dict(details)
    try:
        yield found
    except BaseException:
        found.setdefault("outcome", "failed")
        raise
    finally:
        note(kind, began, listed=listed, **found)


def mark(name: str) -> float | None:
    """Note the moment `name` first happened in the run being timed, if any; returns, that
    first time, how long the job had been due by then (`Timeline.mark`)."""
    if (timeline := _timeline.get()) is not None:
        return timeline.mark(name)
    return None


def describe(job_id: int, kind: str, timing: Timing) -> str:
    """A run's line in words: "Job 812 (answer_question) ran 48.2 s (done), 0.3 s after it was
    due: 3 agent turns 40.1 s, 5 tool calls 6.2 s, 37 writes 0.4 s; the first text 12.4 s in."
    """
    sentence = (
        f"Job {job_id} ({kind}) ran {timing['seconds']:.1f} s ({timing['outcome']}), "
        f"{timing['waited']:.1f} s after it was due"
    )
    totals = timing["totals"]
    counted = [
        f"{totals[name]['count']} {nouns[totals[name]['count'] != 1]} "
        f"{totals[name]['seconds']:.1f} s"
        for name, nouns in NOUNS.items()
        if name in totals
    ]
    if counted:
        sentence += ": " + ", ".join(counted)
    if FIRST_TEXT in timing:
        sentence += f"; the first text {timing[FIRST_TEXT]:.1f} s in"
    return sentence + "."
