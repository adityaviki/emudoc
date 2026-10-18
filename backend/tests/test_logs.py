"""What the log never holds: an exception is told by its frames and kinds, its message only when
the app wrote it or it is about a connection; a database error by its SQLSTATE and names, never
the row it refused; a crashing stage, the parse child and a failed job the same way; the access
log names a query's parameters without their values; and every handler there is gets the
filter, uvicorn's included.

And what each line names: in JSON, one object a line, the request's id and the account and
project it acts on, or the job's id and what it is for; uvicorn's lines in the same format, the
probes that succeeded left out."""

import io
import json
import logging
import re
import sys

import pytest
from sqlalchemy.exc import IntegrityError

import app.answers as answers
import app.indexing.pipeline as pipeline
import app.parsing.limits as limits
from app.errors import WITHHELD, AppError, described, provider_codes, summary
from app.indexing.pipeline import process_document
from app.jobs import ANSWER_QUESTION, INDEX_DOCUMENT, claim
from app.logs import (
    HANDLER,
    HTTP_CLIENT_LOGGERS,
    UVICORN_LOGGERS,
    AddressesBare,
    JsonFormatter,
    ProbesLeftOut,
    QueryValuesHidden,
    WithheldMessages,
    bare_url,
    configure_logging,
    current_context,
    logging_about,
    without_query_values,
)
from app.models import Folder, Project
from app.worker import HANDLERS, Handler, Worker
from tests.conftest import FakeEmbedder
from tests.test_api import create_project
from tests.test_auth import signup
from tests.test_chats import ask, new_chat

SECRET = "the termination clause says ninety days"
# Where uvicorn's own handlers write, which nothing should once the app set up its logging.
UVICORNS = io.StringIO()


def raised(error: BaseException) -> BaseException:
    """The exception with a traceback, as the log receives one."""
    try:
        raise error
    except BaseException as caught:
        return caught


@pytest.fixture
def written():
    """What the app's loggers write through a handler with the app's filter, as text."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    handler.addFilter(WithheldMessages())
    logger = logging.getLogger("app")
    logger.addHandler(handler)
    yield stream
    logger.removeHandler(handler)


@pytest.fixture
def logged():
    """What the app's loggers write in JSON, from INFO up, through a handler with the app's
    filter: a function that returns the lines so far as objects."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(WithheldMessages())
    logger = logging.getLogger("app")
    level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    yield lambda: [json.loads(line) for line in stream.getvalue().splitlines()]
    logger.removeHandler(handler)
    logger.setLevel(level)


@pytest.fixture
def as_uvicorn_starts(monkeypatch) -> io.StringIO:
    """Logging as uvicorn leaves it before it imports the app: handlers of its own on "uvicorn"
    and "uvicorn.access", which do not propagate, and no filters. What the app's handler writes
    goes to the stream returned; all of it is put back afterwards."""
    root = logging.getLogger()
    level = root.level
    monkeypatch.setattr(HANDLER, "formatter", HANDLER.formatter)
    stream = io.StringIO()
    monkeypatch.setattr(HANDLER, "stream", stream)
    uvicorns = logging.StreamHandler(UVICORNS)
    for name in UVICORN_LOGGERS:
        logger = logging.getLogger(name)
        own = name != "uvicorn.error"
        monkeypatch.setattr(logger, "handlers", [uvicorns] if own else [])
        monkeypatch.setattr(logger, "propagate", not own)
        monkeypatch.setattr(logger, "filters", [])
    for name in HTTP_CLIENT_LOGGERS:
        monkeypatch.setattr(logging.getLogger(name), "filters", [])
    yield stream
    assert UVICORNS.getvalue() == ""
    root.setLevel(level)


def access_record(path: str, status: int) -> logging.LogRecord:
    """A line of uvicorn's access log as uvicorn makes one: client, method, path with query,
    HTTP version, status."""
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("10.73.0.1:41000", "GET", path, "1.1", status),
        None,
    )


def test_an_exception_keeps_its_frames_and_kinds_but_not_a_message_that_may_quote_data():
    try:
        try:
            raise KeyError(SECRET)
        except KeyError as error:
            raise ValueError(f"could not read {SECRET}") from error
    except ValueError as error:
        text = described(error)
    assert SECRET not in text
    assert f"KeyError: {WITHHELD}" in text and f"ValueError: {WITHHELD}" in text
    assert "direct cause" in text and "test_logs.py" in text and "raise KeyError" in text

    # The app's own sentences and the operating system's are shown.
    assert "app.errors.AppError: Set INDEX_MODEL first." in described(
        raised(AppError("Set INDEX_MODEL first."))
    )
    assert "ConnectionRefusedError: [Errno 111] Connection refused" in described(
        raised(ConnectionRefusedError(111, "Connection refused"))
    )

    # In one line, for a job's last error: the kind and where, or the kind and the sentence.
    line = summary(raised(ValueError(SECRET)))
    assert line.startswith("ValueError at tests/test_logs.py:") and SECRET not in line
    assert summary(raised(AppError("The file is empty."))) == (
        "app.errors.AppError: The file is empty."
    )


def test_a_database_error_is_told_by_its_code_and_names_not_the_row_it_refused(session):
    session.add(Folder(project_id=None, name=SECRET))
    with pytest.raises(IntegrityError) as refused:
        session.flush()
    session.rollback()
    # Postgres repeats the whole row in its DETAIL; the statement's values are hidden already.
    assert SECRET in str(refused.value) and "parameters hidden" in str(refused.value)
    text = described(refused.value)
    assert SECRET not in text
    assert "SQLSTATE 23502, table folders, column project_id" in text


def test_a_crashing_stage_logs_where_it_broke_without_what_it_held(
    session, services, make_document, sample_pdf, monkeypatch, written
):
    def crash(session_, document, services_, meter):
        raise ValueError(f"cannot split {SECRET}")

    monkeypatch.setitem(pipeline._STAGES, "structure", crash)
    document = make_document(sample_pdf)
    process_document(document.id, services)

    session.refresh(document)
    assert document.stages["structure"]["detail"] == "Unexpected error: ValueError"
    log = written.getvalue()
    assert f"Stage structure failed for document {document.id}" in log
    assert f"ValueError: {WITHHELD}" in log and "in crash" in log
    assert SECRET not in log


def test_the_parse_child_prints_its_traceback_without_the_messages(tmp_path, monkeypatch, capsys):
    def crash(path, filename, **limits_):
        raise ValueError(f"bad cell {SECRET}")

    monkeypatch.setattr(limits, "parse_file", crash)
    path = tmp_path / "prices.csv"
    path.write_text("item,price\n")

    assert limits.main([str(path), "prices.csv"]) == limits.CRASHED
    out, err = capsys.readouterr()
    assert json.loads(out) == {"error": "ValueError"}
    assert f"ValueError: {WITHHELD}" in err and "in crash" in err
    assert SECRET not in err


def test_a_failed_parse_names_the_document_by_its_id_not_its_name(tmp_path, monkeypatch, written):
    # A file's name can say what it holds ("Dismissal of J. Smith.pdf"), and the child's
    # command line is readable by every user of the machine.
    started = []
    popen = limits.subprocess.Popen

    def recording(command, **options):
        started.append(command)
        return popen(command, **options)

    monkeypatch.setattr(limits.subprocess, "Popen", recording)
    path = tmp_path / "original.pdf"
    path.write_bytes(b"%PDF-1.4 but nothing of a PDF follows")
    with pytest.raises(limits.ParseError):
        limits.parse_within_limits(path, f"{SECRET}.pdf", limits.ParseLimits(), document_id=7)
    (command,) = started
    assert "upload.pdf" in command and not any(SECRET in part for part in command)
    log = written.getvalue()
    assert "Parsing document 7 (.pdf) in a child process ended with code" in log
    assert SECRET not in log


def test_the_access_log_names_a_querys_parameters_without_their_values():
    assert without_query_values("/api/projects/1/search?q=termination+clause&limit=20") == (
        "/api/projects/1/search?q=…&limit=…"
    )
    assert without_query_values("/api/auth/oauth/google/callback?code=4/0Ab&state=xyz") == (
        "/api/auth/oauth/google/callback?code=…&state=…"
    )
    assert without_query_values("/api/projects") == "/api/projects"
    assert without_query_values("/verify?") == "/verify"

    # A record as uvicorn makes one: client, method, path with query, HTTP version, status.
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:50000", "GET", "/api/projects/1/search?q=ninety+days", "1.1", 200),
        None,
    )
    assert QueryValuesHidden().filter(record) is True
    assert record.getMessage() == '127.0.0.1:50000 - "GET /api/projects/1/search?q=… HTTP/1.1" 200'


def test_an_outgoing_requests_line_names_its_address_without_credentials_or_values():
    # httpx writes one line per request the app sends; an EMBEDDING_BASE_URL may hold a user
    # and password, or a key in its query.
    record = logging.LogRecord(
        "httpx",
        logging.INFO,
        __file__,
        1,
        'HTTP Request: %s %s "%s %d %s"',
        (
            "POST",
            "https://bob:s3cret@embed.example.com:8443/v1/embeddings?key=k3y",
            "HTTP/1.1",
            200,
            "OK",
        ),
        None,
    )
    assert AddressesBare().filter(record) is True
    assert record.getMessage() == (
        'HTTP Request: POST https://embed.example.com:8443/v1/embeddings?key=… "HTTP/1.1 200 OK"'
    )
    assert bare_url("http://[::1]:11434/v1") == "http://[::1]:11434/v1"


def test_every_handler_there_is_gets_the_filters(as_uvicorn_starts):
    configure_logging()
    configure_logging()

    for name in HTTP_CLIENT_LOGGERS:
        assert [type(found) for found in logging.getLogger(name).filters] == [AddressesBare]
    assert [type(found) for found in logging.getLogger("uvicorn.access").filters] == [
        QueryValuesHidden,
        ProbesLeftOut,
    ]
    root = logging.getLogger()
    assert HANDLER in root.handlers
    assert all(
        [type(found) for found in handler.filters].count(WithheldMessages) == 1
        for handler in root.handlers
    )
    # uvicorn's lines go to the app's handler, and its own handlers write nothing.
    for name in UVICORN_LOGGERS:
        logger = logging.getLogger(name)
        assert logger.handlers == [] and logger.propagate
    # uvicorn logs an exception in a request this way, and the handler prints no message.
    try:
        raise RuntimeError(SECRET)
    except RuntimeError:
        logging.getLogger("uvicorn.error").error("Exception in ASGI application", exc_info=True)
    written = as_uvicorn_starts.getvalue()
    assert f"RuntimeError: {WITHHELD}" in written and SECRET not in written


def test_a_providers_error_body_is_told_by_its_identifiers():
    body = {
        "error": {
            "code": 400,
            "message": f"Invalid input: {SECRET}",
            "status": "INVALID_ARGUMENT",
            "type": "not an identifier, a sentence",
            "details": [{"reason": "API_KEY_INVALID"}, {"reason": "API_KEY_INVALID"}, "x"],
        }
    }
    assert provider_codes(body) == ["INVALID_ARGUMENT", "API_KEY_INVALID"]
    assert provider_codes("Bad gateway") == [] and provider_codes({"error": "x"}) == []


def test_a_logged_database_error_holds_nothing_of_its_row(session, written):
    # What a member typed reaches the database, and the database's refusal repeats it.
    session.add(Project(org_id=None, name=SECRET))
    with pytest.raises(IntegrityError) as refused:
        session.flush()
    session.rollback()
    logging.getLogger("app.test").error("Saving failed", exc_info=refused.value)
    assert "SQLSTATE 23502" in written.getvalue() and SECRET not in written.getvalue()


def test_a_json_line_is_one_object_with_what_it_was_logged_about():
    try:
        raise ValueError(f"cannot split {SECRET}")
    except ValueError:
        record = logging.LogRecord(
            "app.worker",
            logging.ERROR,
            __file__,
            1,
            "Job %s (%s) failed",
            (4, "index_document"),
            sys.exc_info(),
        )
    # A library's `extra` fields are no part of the line: they could hold anything.
    record.prompt = SECRET
    formatter = JsonFormatter()
    with logging_about(job_id=4, job_kind="index_document", org_id=2, project_id=7):
        text = formatter.format(record)
    assert "\n" not in text and SECRET not in text
    line = json.loads(text)
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", line.pop("time"))
    exception = line.pop("exception")
    assert f"ValueError: {WITHHELD}" in exception and "Traceback" in exception
    assert line == {
        "level": "ERROR",
        "logger": "app.worker",
        "message": "Job 4 (index_document) failed",
        "job_id": 4,
        "job_kind": "index_document",
        "org_id": 2,
        "project_id": 7,
    }
    # Outside a request or a job, the line is about nothing in particular.
    assert current_context() == {}
    assert "job_id" not in json.loads(formatter.format(record))


def test_a_requests_lines_name_its_id_the_member_and_the_project(
    client, services, signed_in_only, logged
):
    member = signup(client)
    project = create_project(client)
    services.embedder.fail = True
    # The proxy's id for the request, so that its access log and the app's lines agree.
    request_id = "0f8fad5b-d9cb-469f-a165-70867728950e"
    response = client.get(
        f"/api/projects/{project['id']}/search",
        params={"q": SECRET},
        headers={"X-Request-ID": request_id},
    )
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == request_id
    (line,) = [line for line in logged() if line["logger"] == "app.search"]
    line.pop("time")
    assert line == {
        "level": "WARNING",
        "logger": "app.search",
        "message": f"A search in project {project['id']} matched keywords only: "
        f"{FakeEmbedder.UNREACHABLE}",
        "request_id": request_id,
        "org_id": member["organization"]["id"],
        "user_id": member["id"],
        "project_id": project["id"],
    }
    assert all(SECRET not in json.dumps(line) for line in logged())


def test_every_request_gets_an_id_of_its_own_unless_the_proxy_sent_one(client):
    first, second = (client.get("/api/livez").headers["X-Request-ID"] for _ in range(2))
    assert re.fullmatch("[0-9a-f]{32}", first) and first != second
    # Caddy's {http.request.uuid} and nginx's $request_id are taken as they come.
    for sent in ("0f8fad5b-d9cb-469f-a165-70867728950e", "8b4f2a6c0d1e3f5a7b9c8d0e1f2a3b4c"):
        assert (
            client.get("/api/livez", headers={"X-Request-ID": sent}).headers["X-Request-ID"] == sent
        )
    # Anything that would read as more than an id gets one of the app's instead.
    for sent in ("short", "a" * 129, "two words", "-leading-dash", '"quoted-value"'):
        taken = client.get("/api/livez", headers={"X-Request-ID": sent}).headers["X-Request-ID"]
        assert re.fullmatch("[0-9a-f]{32}", taken)


def test_a_jobs_lines_name_the_job_and_what_it_is_for(
    session, services, account, make_document, sample_pdf, monkeypatch, logged
):
    document = make_document(sample_pdf)

    def run(job, services_):
        logging.getLogger("app.indexing.pipeline").info("Indexing")
        raise ValueError(SECRET)

    monkeypatch.setitem(HANDLERS, INDEX_DOCUMENT, Handler(run, HANDLERS[INDEX_DOCUMENT].abandon))
    worker = Worker(services, concurrency=1)
    job = claim(worker.id, [INDEX_DOCUMENT])
    worker._run(job)

    about = {
        "job_id": job.id,
        "job_kind": INDEX_DOCUMENT,
        "org_id": account.organization.id,
        "project_id": document.project_id,
        "document_id": document.id,
    }
    started, failed = logged()
    assert started["message"] == "Indexing" and failed["logger"] == "app.worker"
    assert {name: started.get(name) for name in about} == about
    # The worker's own line about the job, its traceback without the message, too.
    assert {name: failed.get(name) for name in about} == about
    assert f"ValueError: {WITHHELD}" in failed["exception"]
    # The thread runs its next job, or none, about nothing of this one.
    assert current_context() == {}


def test_an_answers_lines_name_the_member_who_asked(client, services, account, monkeypatch, logged):
    project = create_project(client)
    chat = new_chat(client, project["id"])
    asked = ask(client, chat["id"], SECRET)

    def thinking(services_, project_id, messages, usage):
        logging.getLogger("app.agent.loop").info("Thinking")
        return iter([])

    monkeypatch.setattr(answers, "run_agent", thinking)
    worker = Worker(services, concurrency=0, answer_concurrency=1)
    job = claim(worker.id, [ANSWER_QUESTION])
    worker._run(job)

    (line,) = [line for line in logged() if line["message"] == "Thinking"]
    assert line["job_id"] == job.id and line["job_kind"] == ANSWER_QUESTION
    assert line["message_id"] == asked["answer"]["id"]
    assert line["project_id"] == project["id"]
    assert (line["org_id"], line["user_id"]) == (account.organization.id, account.user.id)


def test_the_probes_that_succeeded_are_left_out_of_the_access_log():
    keep = ProbesLeftOut()
    assert not keep.filter(access_record("/api/health", 200))
    assert not keep.filter(access_record("/api/livez", 200))
    assert not keep.filter(access_record("/api/livez?from=uptime", 200))
    # A replica that is not ready, and every other request, stay in.
    assert keep.filter(access_record("/api/health", 503))
    assert keep.filter(access_record("/api/projects", 200))
    assert keep.filter(access_record("/api/healthy", 200))


def test_uvicorns_lines_go_through_the_apps_handler_in_its_format(as_uvicorn_starts):
    configure_logging("json")

    access = logging.getLogger("uvicorn.access")
    with logging_about(request_id="0f8fad5b-d9cb-469f-a165-70867728950e", user_id=3):
        for record in (
            access_record("/api/projects/3/search?q=ninety+days", 200),
            access_record("/api/health", 200),
            access_record("/api/health", 503),
        ):
            access.handle(record)
    logging.getLogger("uvicorn.error").info("Started server process [%d]", 7)

    lines = [json.loads(line) for line in as_uvicorn_starts.getvalue().splitlines()]
    for line in lines:
        line.pop("time")
    assert lines == [
        {
            "level": "INFO",
            "logger": "uvicorn.access",
            "message": '10.73.0.1:41000 - "GET /api/projects/3/search?q=… HTTP/1.1" 200',
            "request_id": "0f8fad5b-d9cb-469f-a165-70867728950e",
            "user_id": 3,
            "method": "GET",
            "path": "/api/projects/3/search?q=…",
            "status": 200,
            "client": "10.73.0.1:41000",
        },
        {
            "level": "INFO",
            "logger": "uvicorn.access",
            "message": '10.73.0.1:41000 - "GET /api/health HTTP/1.1" 503',
            "request_id": "0f8fad5b-d9cb-469f-a165-70867728950e",
            "user_id": 3,
            "method": "GET",
            "path": "/api/health",
            "status": 503,
            "client": "10.73.0.1:41000",
        },
        {"level": "INFO", "logger": "uvicorn.error", "message": "Started server process [7]"},
    ]

    # Asked again, in the other style, the one handler changes.
    configure_logging("text")
    logging.getLogger("uvicorn.error").info("Shutting down")
    assert as_uvicorn_starts.getvalue().endswith(" INFO uvicorn.error: Shutting down\n")


def test_an_access_log_turned_off_stays_off(as_uvicorn_starts, monkeypatch):
    # uvicorn --no-access-log leaves its access logger without handlers, not propagating.
    access = logging.getLogger("uvicorn.access")
    monkeypatch.setattr(access, "handlers", [])
    monkeypatch.setattr(access, "propagate", False)
    configure_logging("json")
    assert not access.hasHandlers()
