"""What the log never holds: an exception is told by its frames and kinds, its message only when
the app wrote it or it is about a connection; a database error by its SQLSTATE and names, never
the row it refused; a crashing stage, the parse child and a failed job the same way; the access
log names a query's parameters without their values; and every handler there is gets the
filter, uvicorn's included."""

import io
import json
import logging

import pytest
from sqlalchemy.exc import IntegrityError

import app.indexing.pipeline as pipeline
import app.parsing.limits as limits
from app.errors import WITHHELD, AppError, described, provider_codes, summary
from app.indexing.pipeline import process_document
from app.logs import (
    HTTP_CLIENT_LOGGERS,
    AddressesBare,
    QueryValuesHidden,
    WithheldMessages,
    bare_url,
    configure_logging,
    without_query_values,
)
from app.models import Folder, Project

SECRET = "the termination clause says ninety days"


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


def test_every_handler_there_is_gets_the_filters(monkeypatch):
    uvicorn_error = logging.getLogger("uvicorn.error")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    monkeypatch.setattr(uvicorn_error, "handlers", [handler])
    monkeypatch.setattr(logging.getLogger("uvicorn.access"), "filters", [])
    for name in HTTP_CLIENT_LOGGERS:
        monkeypatch.setattr(logging.getLogger(name), "filters", [])

    configure_logging()
    configure_logging()

    for name in HTTP_CLIENT_LOGGERS:
        assert [type(found) for found in logging.getLogger(name).filters] == [AddressesBare]

    assert [type(found) for found in handler.filters] == [WithheldMessages]
    assert [type(found) for found in logging.getLogger("uvicorn.access").filters] == [
        QueryValuesHidden
    ]
    assert all(
        any(isinstance(found, WithheldMessages) for found in root.filters)
        for root in logging.getLogger().handlers
    )
    # uvicorn logs an exception in a request this way, and its handler prints no message.
    try:
        raise RuntimeError(SECRET)
    except RuntimeError:
        uvicorn_error.error("Exception in ASGI application", exc_info=True)
    assert f"RuntimeError: {WITHHELD}" in stream.getvalue() and SECRET not in stream.getvalue()


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
