"""What the error tracker is told: an unplanned error with what the log would show of it and the
ids of the request or job it happened in, never a message the app did not write, a frame's
variables, a query's values, a body or a cookie; the page's errors through the app; nothing
for a refusal the app answers on purpose. And what the member sees of an unplanned error: a
500 with the reference to quote."""

import json
import logging

import pytest
import sentry_sdk
from fastapi.testclient import TestClient
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport

import app.indexing.pipeline as pipeline
import app.main as main
from app.config import get_settings
from app.errors import WITHHELD, AppError
from app.indexing.pipeline import process_document
from app.jobs import INDEX_DOCUMENT, claim
from app.storage import StorageError
from app.tracing import TRACE_ID
from app.tracking import BAD_DSN, reported, start_tracking
from app.version import VERSION
from app.worker import HANDLERS, Handler, Worker
from tests.conftest import APP_HEADERS
from tests.test_api import create_project
from tests.test_auth import signup
from tests.test_headers import carries_the_headers

SECRET = "the termination clause says ninety days"
DSN = "https://0123456789abcdef@o1.ingest.de.sentry.io/2"
# What the page's error boundary sends for a TypeError in the chat pane (frontend/src/errors.ts).
REPORT = {
    "name": "TypeError",
    "status": None,
    "frames": [
        {"function": "Xe", "file": "/assets/index-B2x9kQ.js", "line": 40, "column": 1234},
        {"function": "new Ab", "file": "/assets/index-B2x9kQ.js", "line": 1, "column": 99},
        {"function": None, "file": "/assets/pdf-Cq1.js", "line": 3, "column": 7},
    ],
    "path": "/projects/12/documents/3",
}


class Kept(Transport):
    """A transport that keeps what the SDK would send, in the thread that captured it."""

    def __init__(self) -> None:
        super().__init__()
        self.envelopes: list[Envelope] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        self.envelopes.append(envelope)

    @property
    def events(self) -> list[dict]:
        return [item.payload.json for envelope in self.envelopes for item in envelope.items]

    def sent(self) -> str:
        """Everything that would have left, as text, for what must not be in it."""
        return "".join(
            item.get_bytes().decode() for envelope in self.envelopes for item in envelope.items
        )


@pytest.fixture
def tracker(monkeypatch):
    kept = Kept()
    monkeypatch.setattr(get_settings(), "sentry_dsn", DSN)
    monkeypatch.setattr(get_settings(), "sentry_environment", "staging")
    assert start_tracking(get_settings(), transport=kept)
    yield kept
    sentry_sdk.get_client().close()
    sentry_sdk.get_global_scope().set_client(None)


@pytest.fixture
def failing(client):
    """The test client's app with a client that gets the 500 of an exception nobody handled,
    as a browser would, rather than the exception itself."""
    return TestClient(client.app, headers=APP_HEADERS, raise_server_exceptions=False)


def boom(*args, **kwargs):
    held = SECRET  # noqa: F841 (a local variable the tracker must not be sent)
    raise RuntimeError(f"cannot read {SECRET}")


def test_an_unplanned_error_is_answered_with_the_reference_to_quote(failing, monkeypatch, caplog):
    monkeypatch.setattr(main, "_contract", boom)
    request_id = "0f8fad5b-d9cb-469f-a165-70867728950e"
    response = failing.get("/api/v1/openapi.json", headers={"X-Request-ID": request_id})

    assert response.status_code == 500
    assert response.json() == {
        "detail": "Emudoc ran into an error of its own, which it recorded. Try again, and if it "
        f"fails again, give the reference {request_id} when you report it.",
        "request_id": request_id,
    }
    assert SECRET not in response.text
    # Sent from outside the app's middleware, it carries what they would have put on it.
    assert response.headers["X-Request-ID"] == request_id
    carries_the_headers(response)
    assert response.headers["cache-control"] == "no-store"
    # A request of the app's own gets an id of its own all the same.
    own = failing.get("/api/v1/openapi.json")
    assert own.json()["request_id"] == own.headers["X-Request-ID"]
    assert own.headers["X-Request-ID"] in own.json()["detail"]


def test_the_tracker_is_told_what_the_log_would_show(
    failing, services, signed_in_only, monkeypatch, tracker
):
    member = signup(failing)
    monkeypatch.setattr(services.embedder, "check", boom)
    failing.cookies.set("emudoc_theme", "dark")
    response = failing.get("/api/status", params={"q": SECRET})
    assert response.status_code == 500

    # uvicorn would log the exception too, the same one: it is reported once.
    (event,) = tracker.events
    assert {item.type for envelope in tracker.envelopes for item in envelope.items} == {"event"}
    (error,) = event["exception"]["values"]
    assert error["type"] == "RuntimeError" and error["value"] == WITHHELD
    frames = error["stacktrace"]["frames"]
    assert frames[-1]["function"] == "boom" and frames[-1]["filename"] == "tests/test_tracking.py"
    assert all("vars" not in frame for frame in frames)
    assert event["request"] == {
        "method": "GET",
        "url": "http://testserver/api/status",
        "query_string": "q=…",
    }
    assert event["transaction"] == "/api/status"
    # The request's trace, by which its lines and its jobs' are found in the log.
    assert TRACE_ID.fullmatch(event["tags"].pop("trace_id"))
    assert event["tags"] == {
        "request_id": response.headers["X-Request-ID"],
        "user_id": str(member["id"]),
        "org_id": str(member["organization"]["id"]),
    }
    assert event["user"] == {"id": str(member["id"])}
    assert event["release"] == VERSION and event["environment"] == "staging"
    assert not {"extra", "breadcrumbs", "modules"} & event.keys()
    sent = tracker.sent()
    for withheld in (SECRET, "ninety", "emudoc_theme", "Cookie", member["email"]):
        assert withheld not in sent, withheld


def test_a_message_the_app_wrote_is_shown_and_its_cause_is_not(failing, monkeypatch, tracker):
    def refuse(*args, **kwargs):
        try:
            raise ValueError(SECRET)
        except ValueError as error:
            raise AppError("The model's reply could not be read; ask again.") from error

    monkeypatch.setattr(main, "_contract", refuse)
    assert failing.get("/api/v1/openapi.json").status_code == 500

    (event,) = tracker.events
    cause, error = event["exception"]["values"]
    assert (cause["type"], cause["value"]) == ("ValueError", WITHHELD)
    assert (error["type"], error["value"]) == (
        "AppError",
        "The model's reply could not be read; ask again.",
    )
    assert SECRET not in tracker.sent()


def test_a_refusal_the_app_answers_on_purpose_is_no_error(client, monkeypatch, tracker):
    def unreachable(*args, **kwargs):
        raise StorageError("The bucket could not be reached.", transient=True)

    monkeypatch.setattr(main, "_contract", unreachable)
    assert client.get("/api/v1/openapi.json").status_code == 503
    assert client.get("/api/no-such-route").status_code == 404
    assert client.post("/api/projects", json={}).status_code == 422
    assert tracker.events == []


def test_a_failed_job_is_reported_with_what_it_was_for(
    services, account, make_document, sample_pdf, monkeypatch, tracker
):
    document = make_document(sample_pdf)

    def run(job, services_):
        raise ValueError(SECRET)

    monkeypatch.setitem(HANDLERS, INDEX_DOCUMENT, Handler(run, HANDLERS[INDEX_DOCUMENT].abandon))
    worker = Worker(services, concurrency=1)
    job = claim(worker.id, [INDEX_DOCUMENT])
    worker._run(job)

    (event,) = tracker.events
    assert event["logger"] == "app.worker"
    assert event["logentry"] == {
        "message": "Job %s (%s) failed",
        "formatted": f"Job {job.id} ({INDEX_DOCUMENT}) failed",
    }
    assert event["exception"]["values"][0]["value"] == WITHHELD
    assert TRACE_ID.fullmatch(event["tags"].pop("trace_id"))
    assert event["tags"] == {
        "job_id": str(job.id),
        "job_kind": INDEX_DOCUMENT,
        "org_id": str(account.organization.id),
        "project_id": str(document.project_id),
        "document_id": str(document.id),
    }
    assert SECRET not in tracker.sent()


def test_a_crashing_stage_is_reported_without_the_text_it_held(
    services, make_document, sample_pdf, monkeypatch, tracker
):
    def crash(session_, document, services_, meter):
        text = f"{SECRET} {document.filename}"
        raise ValueError(text)

    monkeypatch.setitem(pipeline._STAGES, "structure", crash)
    document = make_document(sample_pdf)
    process_document(document.id, services)

    (event,) = tracker.events
    failed = f"Stage structure failed for document {document.id}"
    assert event["logentry"]["formatted"].startswith(failed)
    assert event["exception"]["values"][-1]["stacktrace"]["frames"][-1]["function"] == "crash"
    sent = tracker.sent()
    assert SECRET not in sent and document.filename not in sent


def test_an_event_whose_exceptions_are_not_the_logged_chain_tells_no_message():
    # As for an exception group, whose members the SDK lists after it: nothing to match them by.
    event = {
        "exception": {
            "values": [
                {"type": "ValueError", "value": SECRET},
                {"type": "ExceptionGroup", "value": f"two failed: {SECRET}"},
            ]
        },
        "extra": {"document": SECRET},
        "breadcrumbs": {"values": [{"message": SECRET}]},
        "request": {"data": SECRET, "cookies": {"emudoc_session": "x"}, "headers": {"A": "b"}},
    }
    error = ExceptionGroup("two failed", [ValueError(SECRET)])
    kept = reported(event, {"exc_info": (type(error), error, None)})
    assert [value["value"] for value in kept["exception"]["values"]] == [WITHHELD, WITHHELD]
    assert SECRET not in json.dumps(kept)
    assert kept["request"] == {}


def test_the_page_reports_an_error_it_rendered_into(client, caplog, tracker):
    with caplog.at_level(logging.WARNING, "app.tracking"):
        response = client.post("/api/page-errors", json=REPORT)

    assert response.status_code == 202
    reference = response.json()["reference"]
    assert reference == response.headers["X-Request-ID"]
    (line,) = caplog.messages
    assert line == (
        "The page /projects/12/documents/3 failed to render: TypeError in Xe at "
        "/assets/index-B2x9kQ.js:40:1234"
    )
    (event,) = tracker.events
    assert event["platform"] == "javascript" and event["transaction"] == REPORT["path"]
    (error,) = event["exception"]["values"]
    assert (error["type"], error["value"]) == ("TypeError", WITHHELD)
    # The outermost frame first, at the address the browser loaded the script from.
    assert [frame["function"] for frame in error["stacktrace"]["frames"]] == ["?", "new Ab", "Xe"]
    assert error["stacktrace"]["frames"][-1]["abs_path"] == (
        "http://testserver/assets/index-B2x9kQ.js"
    )
    assert TRACE_ID.fullmatch(event["tags"].pop("trace_id"))
    assert event["tags"] == {"request_id": reference, "page": REPORT["path"]}


def test_a_refusal_of_the_api_is_reported_by_its_status(client, tracker):
    response = client.post("/api/page-errors", json=REPORT | {"name": "ApiError", "status": 500})
    assert response.status_code == 202
    (event,) = tracker.events
    assert event["exception"]["values"][0]["value"] == "The API answered 500."


def test_a_report_takes_nothing_but_kinds_frames_of_the_pages_scripts_and_a_bare_path(client):
    # Without a tracker, the report is the log's alone.
    assert client.post("/api/page-errors", json=REPORT).json()["reference"]
    frame = REPORT["frames"][0]
    for unfit in (
        {"name": "TypeError: x is undefined"},
        {"path": "/projects/12?q=termination"},
        {"path": "/projects/twelve%20words"},
        {"frames": [frame | {"file": "https://other.example/assets/x.js"}]},
        {"frames": [frame | {"function": "the termination clause"}]},
        {"frames": [frame] * 51},
    ):
        assert client.post("/api/page-errors", json=REPORT | unfit).status_code == 422, unfit
    # A message the page might send anyway is not read.
    assert client.post("/api/page-errors", json=REPORT | {"message": SECRET}).status_code == 202


@pytest.mark.usefixtures("one_minute")
def test_a_page_that_keeps_failing_is_held_to_a_rate(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_page_errors", 2)
    statuses = [client.post("/api/page-errors", json=REPORT).status_code for _ in range(3)]
    assert statuses == [202, 202, 429]


def test_tracking_starts_only_with_a_dsn_and_refuses_one_that_is_not(monkeypatch):
    monkeypatch.setattr(get_settings(), "sentry_dsn", "")
    assert not start_tracking(get_settings())
    assert not sentry_sdk.get_client().is_active()
    # The message does not repeat the setting, which holds the project's key.
    monkeypatch.setattr(get_settings(), "sentry_dsn", "o1.ingest.de.sentry.io/2")
    with pytest.raises(SystemExit) as stopped:
        start_tracking(get_settings())
    assert stopped.value.code == BAD_DSN
    assert not sentry_sdk.get_client().is_active()


def test_project_routes_get_the_tracker_their_ids(failing, monkeypatch, tracker):
    project = create_project(failing)
    monkeypatch.setattr("app.api.query.hybrid_search", boom)
    response = failing.get(f"/api/projects/{project['id']}/search", params={"q": SECRET})
    assert response.status_code == 500
    (event,) = tracker.events
    assert event["tags"]["project_id"] == str(project["id"])
    assert event["transaction"] == "/api/projects/{project_id}/search"
    assert event["request"]["query_string"].startswith("q=…")
