"""Error tracking: the errors nobody planned for, reported to Sentry or to a server that takes its
protocol (GlitchTip, a Sentry of the operator's own), each under the ids of the request or job
it happened in.

The log already has every one of them (app/logs.py); a tracker groups them, counts them and
tells someone, which a log does not. It is another company that receives data, though, unless
it runs on the deployment's own network, so it is told what the log would show and nothing
more (`reported`): an exception's kind and the frames it was raised through, its message only
when the app wrote it (`shown_messages` in app/errors.py), the log line it was logged with, the
route's pattern and the request's path without its query's values, and the ids of
`current_context`, the user by id alone. Not the frames' local variables (a stage's hold the
document's text), the request's body, cookies or headers, the log lines before it, a record's
extra fields, nor what the SDK's integrations would add of their own: its default ones are off,
the AI integrations above all, which record prompts and replies, and `reported` rebuilds every
event from what it may keep, so that a field a newer SDK adds stays out until it is added here.
Only events leave: no performance traces, sessions, logs or client reports.

The page reports an error it rendered into to the app, not to the tracker (`POST
/api/page-errors`, from the error boundary in frontend/src/components/ErrorBoundary.tsx): so
the browser runs no SDK, which would send the page's address with the library's search words in
it, the requests before the error and the console, the page's policy needs no other origin, and
the report reaches the log as well as the tracker, under the id of the request that brought
it, which the page shows as the reference to quote (`report_page_error`).

SENTRY_DSN turns it on (app/config.py). The API (app/main.py) and the worker (app/worker.py)
call `start_tracking` as they start, after their logging; the maintenance commands, which a
person runs and reads, and the parse child, which imports nothing of the app, do not.
"""

import logging
from collections.abc import Mapping
from typing import Any

import sentry_sdk
from sentry_sdk.integrations.atexit import AtexitIntegration
from sentry_sdk.integrations.dedupe import DedupeIntegration
from sentry_sdk.integrations.excepthook import ExcepthookIntegration
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration
from sentry_sdk.integrations.threading import ThreadingIntegration
from sentry_sdk.transport import Transport
from sentry_sdk.utils import BadDsn, Dsn

from app.config import Settings
from app.errors import WITHHELD, shown_messages
from app.logs import bare_url, current_context, without_query_values
from app.network import host_of
from app.schemas import PageErrorIn, PageFrame
from app.version import VERSION

log = logging.getLogger(__name__)

BAD_DSN = (
    "SENTRY_DSN is not a DSN: it reads https://<key>@<host>/<project>, as the tracker's project "
    "settings show it. Correct it, or leave it empty to have the errors in the log alone."
)
# What of an event the tracker is sent as the SDK made it: when and where it happened, how bad
# it was, the release and environment, and the route it happened on, by its pattern.
KEPT = frozenset(
    {
        "event_id",
        "timestamp",
        "level",
        "logger",
        "platform",
        "release",
        "environment",
        "server_name",
        "sdk",
        "transaction",
        "transaction_info",
        "fingerprint",
    }
)
# The contexts the SDK fills in that say nothing of the request: the interpreter and the system.
CONTEXTS = frozenset({"runtime", "os"})
# What a frame keeps: where it is in the code, and the code's own lines around it.
FRAME = frozenset(
    {
        "filename",
        "abs_path",
        "module",
        "function",
        "lineno",
        "colno",
        "in_app",
        "context_line",
        "pre_context",
        "post_context",
    }
)
# What the hint of an event `report_page_error` made carries, so that `reported` keeps the
# exception it built from the page's report as it is.
_PAGE = "emudoc_page_error"


def start_tracking(settings: Settings, transport: Transport | None = None) -> bool:
    """Report this process's errors to SENTRY_DSN, if set, and say whether they are; tests pass
    a `transport` that keeps what would be sent. A DSN that is not one stops the process with
    what to do, as a setting that does not fit does (`load_settings` in app/config.py), without
    repeating it: it holds the project's key."""
    if not settings.sentry_dsn:
        return False
    try:
        Dsn(settings.sentry_dsn)
    except BadDsn:
        raise SystemExit(BAD_DSN) from None
    environment = settings.sentry_environment or settings.environment
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        release=VERSION,
        environment=environment,
        transport=transport,
        # Every integration by name: those the SDK turns on by itself include the model
        # libraries', which record prompts and replies, and http.client's and httpx's, which
        # add a trace header to every request the app sends a provider.
        default_integrations=False,
        auto_enabling_integrations=False,
        integrations=[
            AtexitIntegration(callback=_unannounced),
            DedupeIntegration(),
            ExcepthookIntegration(),
            # A line at ERROR is an event; no line is a breadcrumb, and none goes to the
            # tracker's own log store.
            LoggingIntegration(level=None, event_level=logging.ERROR, sentry_logs_level=None),
            ThreadingIntegration(),
            # A refusal the app answers on purpose (a 503 when the storage is down, a 404) is
            # no fault; only an exception nobody handled is.
            StarletteIntegration(failed_request_status_codes=set()),
            FastApiIntegration(failed_request_status_codes=set()),
        ],
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
        max_breadcrumbs=0,
        auto_session_tracking=False,
        send_client_reports=False,
        before_send=reported,
    )
    log.info("Errors are reported to %s as %s", host_of(settings.sentry_dsn), environment)
    return True


def reported(event: dict[str, Any], hint: Mapping[str, Any]) -> dict[str, Any]:
    """What the tracker is sent of `event`: what it may keep as it is (KEPT, CONTEXTS), the
    exceptions with what the log would show of each message and their frames without their
    variables, the log line without its arguments, the request's method and address without
    its query's values, and as tags the ids of what the request or job was about."""
    kept = {key: event[key] for key in KEPT if key in event}
    if exception := event.get("exception"):
        kept["exception"] = {"values": _exceptions(exception.get("values") or [], hint)}
    if entry := event.get("logentry"):
        kept["logentry"] = {key: entry[key] for key in ("message", "formatted") if key in entry}
    if request := event.get("request"):
        kept["request"] = _request(request)
    if contexts := event.get("contexts"):
        kept["contexts"] = {key: value for key, value in contexts.items() if key in CONTEXTS}
    about = current_context()
    tags = {name: str(value) for name, value in about.items()}
    if hint.get(_PAGE):
        tags.update(event.get("tags") or {})
    if tags:
        kept["tags"] = tags
    if "user_id" in about:
        kept["user"] = {"id": str(about["user_id"])}
    return kept


def report_page_error(report: PageErrorIn, origin: str) -> None:
    """Log an error the page at `origin` rendered into, and send it to the tracker when there is
    one, as an event of the page's own platform: its frames point into the built scripts, which
    the commit's build maps back to the source (deploying.md, "Error tracking")."""
    frames = report.frames
    where = f" in {frames[0].function or 'a function'} at {_position(frames[0])}" if frames else ""
    refused = f" (the API answered {report.status})" if report.status is not None else ""
    log.warning("The page %s failed to render: %s%s%s", report.path, report.name, refused, where)
    if not sentry_sdk.get_client().is_active():
        return
    sentry_sdk.capture_event(
        {
            "level": "error",
            "platform": "javascript",
            "logger": "page",
            "transaction": report.path,
            "transaction_info": {"source": "custom"},
            "exception": {
                "values": [
                    {
                        "type": report.name,
                        "value": f"The API answered {report.status}."
                        if report.status is not None
                        else WITHHELD,
                        "mechanism": {"type": "error_boundary", "handled": True},
                        # The outermost frame first, as the tracker reads them.
                        "stacktrace": {
                            "frames": [
                                {
                                    "function": frame.function or "?",
                                    "filename": frame.file,
                                    "abs_path": f"{origin}{frame.file}",
                                    "lineno": frame.line,
                                    "colno": frame.column,
                                    "in_app": True,
                                }
                                for frame in reversed(frames)
                            ]
                        },
                    }
                ]
            },
            "tags": {"page": report.path},
        },
        hint={_PAGE: True},
    )


def _exceptions(values: list[dict[str, Any]], hint: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The exceptions of an event, the first cause first as the SDK lists them, each message
    replaced by what the log would show of it. When they are not the chain of the hint's
    exception as the log tells it, as for an exception group, every message is withheld."""
    if hint.get(_PAGE):
        return values
    exc_info = hint.get("exc_info")
    told = shown_messages(exc_info[1]) if exc_info and exc_info[1] is not None else []
    if [kind for kind, _ in told] != [value.get("type") for value in values]:
        told = [("", WITHHELD)] * len(values)
    return [_exception(value, message) for value, (_, message) in zip(values, told, strict=True)]


def _exception(value: dict[str, Any], message: str) -> dict[str, Any]:
    kept = {key: value[key] for key in ("type", "module", "mechanism") if key in value}
    kept["value"] = message
    if stacktrace := value.get("stacktrace"):
        kept["stacktrace"] = {
            "frames": [
                {key: frame[key] for key in FRAME if key in frame}
                for frame in stacktrace.get("frames") or []
            ]
        }
    return kept


def _request(request: dict[str, Any]) -> dict[str, Any]:
    kept = {}
    if method := request.get("method"):
        kept["method"] = method
    if url := request.get("url"):
        kept["url"] = bare_url(url)
    if query := request.get("query_string"):
        kept["query_string"] = without_query_values(f"?{query}").removeprefix("?")
    return kept


def _position(frame: PageFrame) -> str:
    return f"{frame.file}:{frame.line}:{frame.column}"


def _unannounced(pending: int, timeout: float) -> None:
    """At exit the SDK sends what is pending without printing to standard error, where every
    line of the process is the log's."""
