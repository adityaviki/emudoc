"""How the app's processes log, what each line names, and what the log never holds.

Whoever runs the deployment reads the log, and whatever collects it keeps it, so it must never
hold what a document says, a question, an answer or a key. The app's own lines name ids, counts
and its own sentences; what could still slip in comes from outside them, and is stopped here:

- An exception's traceback keeps its frames and the kinds of its exceptions, but not their
  messages, unless the app wrote them (`described` in app/errors.py). `WithheldMessages` puts
  that version in place of the usual one on every handler of the root logger, which uvicorn's
  lines reach too, before any formatter runs.
- The access log names each request by its path and the names of its query's parameters, not
  their values (`QueryValuesHidden`): a search carries its words there (`?q=`), the provider's
  sign-in its one-time code. The page links that carry a token keep it in the fragment, which
  never reaches a server (`_link` in app/api/auth.py).
- The line httpx writes for every request the app sends names its address the same way and
  without the user and password an address may carry (`AddressesBare`), since a setting such
  as EMBEDDING_BASE_URL can hold credentials or a key in its query.
- A provider's error is told by its status and codes (`describe_error` in app/llm.py, the
  embedder), the database is never asked to repeat a statement's values in an error
  (`hide_parameters` in app/db.py), and the parse child prints its traceback the same way.

The API and the worker write one JSON object a line in production (LOG_FORMAT, `JsonFormatter`),
for a collector to search, and each line carries what it was logged about: the request's id,
the account's user and organisation and the project once they are known, or the job's id, kind
and what it is for, and the trace either belongs to (`Context`, app/tracing.py). A request gets
its ids from `RequestIds`, a job from `logging_about` in the worker, and what either is about
is noted where it is found (`about`). uvicorn's own lines, its access log among them, go
through the same handler, without the probes that succeeded (`ProbesLeftOut`), which an
orchestrator sends every few seconds; an access line also says how long the request took
until its response began.

uvicorn sets up its loggers before it imports the app, so `configure_logging`, called when
app/main.py is imported, finds its handlers there; the worker, the maintenance commands and
alembic call it as they start.
"""

import json
import logging
import re
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.errors import described
from app.tracing import Timing, new_trace

LogFormat = Literal["text", "json"]
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
# The loggers that uvicorn gives handlers of their own, which `configure_logging` takes away,
# so that their lines reach the root's handler as the app's do.
UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")
# The loggers of the HTTP clients the app and its model libraries send requests with, which
# write one line per request with its whole address at INFO.
HTTP_CLIENT_LOGGERS = ("httpx", "httpx2")
# The probes (app/main.py), which an orchestrator or an uptime check asks every few seconds per
# replica: an answer that says all is well is a line nobody reads, so the access log keeps the
# failures alone.
PROBES = frozenset({"/api/livez", "/api/health"})
# The one handler every line of the process goes through, to standard error, which is where a
# container's log is read from.
HANDLER = logging.StreamHandler(sys.stderr)
# The header a request's id comes in from the proxy and goes back to the client in.
REQUEST_ID_HEADER = "X-Request-ID"
# What a request id from the proxy may look like: Caddy's {http.request.uuid}, nginx's
# $request_id, and other proxies' ids, but nothing that would read as more than an id.
_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}")
# The breached-password check asks Pwned Passwords' range API by the first five characters of
# the password's SHA-1, in the path (app/passwords.py). They are not the password, but with a
# copy of the database they would let a guesser skip most candidates at SHA-1's speed before
# trying the rest against the Argon2 hash, so no line holds them.
_HASH_RANGE = re.compile(r"(/range/)[0-9A-Fa-f]{5}$")


@dataclass(slots=True)
class Context:
    """What the lines a request or a job logs are about, which the JSON format writes on each.

    One is made for each request (`RequestIds`) and each job the worker runs (`logging_about`),
    and filled in as what it is about is found (`about`): the account once the session is read,
    the project once it is found in the account's organisation. `trace_id` is the trace a
    request starts and the jobs it queues carry (app/tracing.py). The context variable holds the
    one object, not a copy of its fields, so a dependency that runs in a thread of the pool
    fills it in for the lines logged after it, in another thread or on the event loop alike:
    each of them reaches the same object through its copy of the variable.
    """

    request_id: str | None = None
    trace_id: str | None = None
    job_id: int | None = None
    job_kind: str | None = None
    org_id: int | None = None
    user_id: int | None = None
    # The API key a request came with (app/apikeys.py), which acts as `user_id`.
    api_key_id: int | None = None
    # The operator viewing `user_id`'s account as support from their own session
    # (app/support.py).
    operator_id: int | None = None
    project_id: int | None = None
    document_id: int | None = None
    message_id: int | None = None


CONTEXT_FIELDS = tuple(field.name for field in fields(Context))
_current: ContextVar[Context | None] = ContextVar("log_context", default=None)
# When the current request began, as `time.time()`, for its access line's `seconds`.
_began: ContextVar[float | None] = ContextVar("request_began", default=None)


def about(**found: int | str | None) -> None:
    """Note what the current request or job is about, for every line it logs from now on.
    Outside both, as in the housekeeping, nothing is noted."""
    if (context := _current.get()) is not None:
        for name, value in found.items():
            setattr(context, name, value)


@contextmanager
def logging_about(**known: int | str | None) -> Iterator[None]:
    """Log the lines of the block as about `known`, in a context of their own: a job's."""
    token = _current.set(Context(**known))
    try:
        yield
    finally:
        _current.reset(token)


def current_context() -> dict[str, int | str]:
    """What the current request or job is known to be about, by field."""
    context = _current.get()
    if context is None:
        return {}
    return {name: value for name in CONTEXT_FIELDS if (value := getattr(context, name)) is not None}


class RequestIds:
    """Give every request an id and a trace, log what it does under them, and send the id back.

    The id is the proxy's when it sent a well-formed X-Request-ID (deploying.md, "Logs"), so
    that its access log and the app's lines name the same one, and a new one otherwise. The
    response carries it in X-Request-ID, for a member to quote and the operator to find the
    lines by. The trace is always new: the jobs the request queues carry it (app/tracing.py),
    and a client's word is not taken for which work its request belongs to.

    The context stays set for the rest of the request's task: uvicorn runs each request in a
    task with a context of its own, which goes with it, and writes the access line of a request
    that failed, and its traceback, once the app has given up on it.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = _request_id(scope)
        _current.set(Context(request_id=request_id, trace_id=new_trace()))
        _began.set(time.time())

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        await self.app(scope, receive, send_with_id)


def _request_id(scope: Scope) -> str:
    for name, value in scope["headers"]:
        if name == b"x-request-id":
            sent = value.decode("latin-1")
            if _REQUEST_ID.fullmatch(sent):
                return sent
            break
    return uuid.uuid4().hex


class WithheldMessages(logging.Filter):
    """Render a record's exception without the messages that may quote data, before any
    formatter renders it with them. A formatter keeps the first rendering in `exc_text`, so
    this one is what every handler prints."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.exc_info and record.exc_info[1] is not None:
            record.exc_text = described(record.exc_info[1])
        return True


class QueryValuesHidden(logging.Filter):
    """Keep the values of a request's query out of uvicorn's access log: "GET
    /api/projects/1/search?q=…&limit=… 200"."""

    def filter(self, record: logging.LogRecord) -> bool:
        # uvicorn logs the client, the method, the path with its query, the HTTP version and
        # the status, in that order.
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            record.args = (*args[:2], without_query_values(args[2]), *args[3:])
        return True


class ProbesLeftOut(logging.Filter):
    """Leave the probes that succeeded out of uvicorn's access log, and keep those that did not:
    a 503 from /api/health is a replica a load balancer stopped sending requests to."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not (isinstance(args, tuple) and len(args) >= 5 and isinstance(args[2], str)):
            return True
        path, status = args[2].partition("?")[0], args[4]
        return not (path in PROBES and isinstance(status, int) and status < 400)


class AddressesBare(logging.Filter):
    """Keep credentials and query values out of httpx's line for each request it sends:
    "HTTP Request: POST https://embeddings.example.com/v1/embeddings?key=… "HTTP/1.1 200 OK"".
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # httpx logs the method, the URL, the HTTP version, the status and its reason.
        args = record.args
        if isinstance(args, tuple) and len(args) >= 2:
            record.args = (args[0], bare_url(str(args[1])), *args[2:])
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object a line: the time in UTC, the level, the logger and the message, what the
    line is about (`Context`), and the traceback of an exception, without its messages.

    A line of uvicorn's access log also gives the request's parts as fields of their own, its
    query's values already hidden, and the seconds from the request's start to its response's
    (uvicorn logs the line as the response begins). A worker's line about a job's run gives
    its `timing` (app/tracing.py). Nothing else of a record is written, since a library's
    `extra` fields could hold anything: `timing` only when it is a `Timing`, which only the app
    makes.
    """

    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            **current_context(),
        }
        if record.name == "uvicorn.access":
            line.update(_request_line(record))
        if isinstance(timing := record.__dict__.get("timing"), Timing):
            line["timing"] = timing
        if record.exc_info and record.exc_info[1] is not None:
            # As WithheldMessages rendered it, or so, should this handler lack the filter.
            line["exception"] = record.exc_text or described(record.exc_info[1])
        if record.stack_info:
            line["stack"] = record.stack_info
        return json.dumps(line, ensure_ascii=False, default=str)


def _request_line(record: logging.LogRecord) -> dict[str, object]:
    """The parts of an access line: uvicorn logs the client, the method, the path with its
    query, the HTTP version and the status."""
    args = record.args
    if not (isinstance(args, tuple) and len(args) >= 5):
        return {}
    client, method, path, _, status = args[:5]
    line = {"method": method, "path": path, "status": status, "client": client}
    if (began := _began.get()) is not None:
        line["seconds"] = round(max(record.created - began, 0.0), 3)
    return line


def bare_url(url: str) -> str:
    """An address as the log and the app's sentences name it: no user or password, the names
    of its query's parameters without their values, and no password hash's range."""
    parts = urlsplit(url)
    parts = parts._replace(path=_HASH_RANGE.sub(r"\1…", parts.path))
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    try:
        port = parts.port
    except ValueError:
        port = None
    netloc = f"{host}:{port}" if port else host
    bare = urlunsplit((parts.scheme, netloc, parts.path, "", ""))
    return without_query_values(f"{bare}?{parts.query}") if parts.query else bare


def without_query_values(target: str) -> str:
    """The path with only the names of its query's parameters."""
    path, mark, query = target.partition("?")
    if not mark:
        return target
    names = [name for name, _ in parse_qsl(query, keep_blank_values=True)]
    return f"{path}?{'&'.join(f'{name}=…' for name in names)}" if names else path


def configure_logging(style: LogFormat = "text", format: str = FORMAT) -> None:
    """Log at INFO through `HANDLER` in `style`, `format` being the text's, and put the filters
    where every line meets them.

    uvicorn's loggers give up their handlers for it, so that uvicorn's lines, the access log's
    included, are written in the same style and name what they are about; a uvicorn
    --log-config is therefore of no use. Calling it again changes the style.
    """
    root = logging.getLogger()
    if HANDLER not in root.handlers:
        root.addHandler(HANDLER)
    root.setLevel(logging.INFO)
    HANDLER.setFormatter(JsonFormatter() if style == "json" else logging.Formatter(format))
    for name in UVICORN_LOGGERS:
        logger = logging.getLogger(name)
        if logger.handlers:
            # A logger uvicorn left without handlers, the access log after --no-access-log,
            # stays as quiet as it was.
            logger.handlers = []
            logger.propagate = True
    for handler in root.handlers:
        if not any(isinstance(found, WithheldMessages) for found in handler.filters):
            handler.addFilter(WithheldMessages())
    access = logging.getLogger("uvicorn.access")
    for kind in (QueryValuesHidden, ProbesLeftOut):
        if not any(isinstance(found, kind) for found in access.filters):
            access.addFilter(kind())
    for name in HTTP_CLIENT_LOGGERS:
        client = logging.getLogger(name)
        if not any(isinstance(found, AddressesBare) for found in client.filters):
            client.addFilter(AddressesBare())
