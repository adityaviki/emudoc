"""Malware scanning: the clamd client against a daemon that speaks its protocol, the scan
stage ahead of everything that opens a file, the quarantine the members see and the routes
refuse, and the digest the operator gets."""

import socket
import socketserver
import struct
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import text

import app.worker as worker_module
from app.api.documents import QUARANTINED_FILE, UNSCANNED_FILE
from app.config import Settings, get_settings, parse_scanner_url
from app.indexing.pipeline import process_document
from app.jobs import claim
from app.models import Block, Document, Fact, Passage, Section
from app.scanning import (
    NOT_SCANNED,
    UNSCANNED,
    ClamdScanner,
    ScanError,
    alert_quarantines,
    check_scanner,
    quarantine_message,
    scanner_from_settings,
)
from app.worker import MAX_RETRIES, Worker
from tests.conftest import EICAR, EICAR_NAME, SCANNER_DOWN, FakeMailer
from tests.test_api import create_project, listed, upload
from tests.test_deadletters import OPERATOR
from tests.test_jobs import INDEX, run_index, wait_until
from tests.test_pipeline import rows, stage_statuses

VERSION = b"ClamAV 1.4.1/27450/Tue Oct 14 08:00:00 2026"


class _ClamdHandler(socketserver.StreamRequestHandler):
    """One connection to the fake daemon: a command with the `z` prefix and NUL terminator,
    then for INSTREAM the length-prefixed chunks up to the empty one, and the reply."""

    def handle(self) -> None:
        server = self.server
        command = bytearray()
        while (byte := self.rfile.read(1)) and byte != b"\0":
            command += byte
        if command == b"zPING":
            self.wfile.write(server.pong)
        elif command == b"zVERSION":
            self.wfile.write(VERSION + b"\0")
        elif command == b"zINSTREAM":
            data = bytearray()
            while (length := struct.unpack(">I", self.rfile.read(4))[0]) != 0:
                data += self.rfile.read(length)
            server.received.append(bytes(data))
            if server.reply is not None:
                self.wfile.write(server.reply)
            elif EICAR in data:
                self.wfile.write(f"stream: {EICAR_NAME} FOUND\0".encode())
            else:
                self.wfile.write(b"stream: OK\0")
        else:
            self.wfile.write(b"UNKNOWN COMMAND\0")


class _FakeClamdMixin:
    """The knobs of the fake daemon: what it answers to PING, a canned reply to every scan,
    and what it was sent."""

    def __init__(self, address):
        super().__init__(address, _ClamdHandler)
        self.pong = b"PONG\0"
        self.reply: bytes | None = None
        self.received: list[bytes] = []


class FakeClamd(_FakeClamdMixin, socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True


class FakeClamdSocket(_FakeClamdMixin, socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    pass


@contextmanager
def serving(server: socketserver.BaseServer) -> Iterator[socketserver.BaseServer]:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def clamd() -> Iterator[FakeClamd]:
    """A fake daemon on a loopback port of its own."""
    with serving(FakeClamd(("127.0.0.1", 0))) as server:
        yield server


def flagged_pdf(directory: Path, sample_pdf: Path, name: str = "bad.pdf") -> Path:
    """The sample contract with the EICAR string after its trailer: a PDF by its bytes, and
    flagged by every scanner."""
    path = directory / name
    path.write_bytes(sample_pdf.read_bytes() + b"\n" + EICAR + b"\n" + name.encode())
    return path


def test_the_url_names_a_daemon_a_socket_or_nothing():
    assert parse_scanner_url("") is None and parse_scanner_url(" off ") is None
    assert parse_scanner_url("OFF") is None
    assert parse_scanner_url("clamd://clamav") == ("clamav", 3310)
    assert parse_scanner_url("clamd://10.0.0.5:3311") == ("10.0.0.5", 3311)
    assert parse_scanner_url("clamd:///run/clamav/clamd.ctl") == "/run/clamav/clamd.ctl"
    for bad in (
        "clamav:3310",
        "http://clamav:3310",
        "clamd://",
        "clamd://clamav/ctl",
        "clamd://clamav:port",
        "clamd://clamav?x=1",
        "none",
    ):
        with pytest.raises(ValueError, match="SCANNER_URL names the ClamAV daemon"):
            parse_scanner_url(bad)

    # The settings refuse the same, and the scanner is built from them.
    with pytest.raises(ValidationError, match="SCANNER_URL names the ClamAV daemon"):
        Settings(_env_file=None, scanner_url="clamav:3310")
    assert scanner_from_settings(Settings(_env_file=None, scanner_url="")) is None
    assert scanner_from_settings(Settings(_env_file=None, scanner_url="off")) is None
    scanner = scanner_from_settings(Settings(_env_file=None, scanner_url="clamd://clamav"))
    assert isinstance(scanner, ClamdScanner) and scanner.url == "clamd://clamav:3310"
    assert ClamdScanner("/run/clamav/clamd.ctl").url == "clamd:///run/clamav/clamd.ctl"


def test_clamd_is_asked_over_tcp_and_over_a_socket(clamd, tmp_path):
    scanner = ClamdScanner(clamd.server_address)
    assert scanner.check() == VERSION.decode()

    # A clean file is streamed whole, in more than one chunk; a flagged one is named.
    clean = tmp_path / "clean.txt"
    clean.write_bytes(b"Dear supplier, the delivery was late.\n" * 5000)
    bad = tmp_path / "bad.txt"
    bad.write_bytes(b"x" * 70_000 + EICAR + b"y" * 100)
    assert scanner.scan(clean) is None
    assert scanner.scan(bad) == EICAR_NAME
    assert clamd.received == [clean.read_bytes(), bad.read_bytes()]

    path = tmp_path / "clamd.ctl"
    with serving(FakeClamdSocket(str(path))) as over_socket:
        scanner = ClamdScanner(str(path))
        assert scanner.check() == VERSION.decode()
        assert scanner.scan(bad) == EICAR_NAME
        assert over_socket.received == [bad.read_bytes()]


def test_a_daemon_that_is_down_is_a_passing_failure(tmp_path):
    # A port nobody listens on, and a socket that is not there.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    file = tmp_path / "x.txt"
    file.write_text("x")
    down = ClamdScanner(("127.0.0.1", port))
    with pytest.raises(ScanError, match="could not be reached") as error:
        down.check()
    assert error.value.transient and f"clamd://127.0.0.1:{port}" in str(error.value)
    with pytest.raises(ScanError, match="Connection refused") as error:
        down.scan(file)
    assert error.value.transient

    missing = ClamdScanner(str(tmp_path / "none.ctl"))
    with pytest.raises(ScanError, match="could not be reached") as error:
        missing.scan(file)
    assert error.value.transient and "clamd:///" in str(error.value)


def test_a_daemon_that_refuses_the_file_or_talks_nonsense_is_not_retried(clamd, tmp_path):
    scanner = ClamdScanner(clamd.server_address)
    file = tmp_path / "big.pdf"
    file.write_bytes(b"%PDF-1.4\n")

    # clamd's own size limit: the operator raises it, no retry would help.
    clamd.reply = b"INSTREAM size limit exceeded. ERROR\0"
    with pytest.raises(ScanError, match="StreamMaxLength") as error:
        scanner.scan(file)
    assert not error.value.transient
    assert str(error.value).startswith(
        "The malware scanner could not scan this file (INSTREAM size limit exceeded)."
    )
    # The members cannot raise it, and are not told of clamd's settings.
    assert error.value.members == (
        "The malware scanner could not scan this file, so it is neither indexed nor opened. Try "
        "uploading a smaller copy."
    )

    clamd.reply = b"stream: something odd\0"
    with pytest.raises(ScanError, match="gave no verdict") as error:
        scanner.scan(file)
    assert not error.value.transient and "'stream: something odd'" in str(error.value)

    # Something else listens at the address.
    clamd.pong = b"HELLO\0"
    with pytest.raises(ScanError, match="not ClamAV's daemon") as error:
        scanner.check()
    assert not error.value.transient


def test_the_start_says_which_scanner_runs_or_warns(clamd, caplog, monkeypatch):
    settings = get_settings()
    with caplog.at_level("INFO", logger="app.scanning"):
        check_scanner(settings, ClamdScanner(clamd.server_address))
        assert f"Uploads are scanned by {VERSION.decode()}" in caplog.text

        # A daemon that is down warns and lets the process start: the stage retries.
        caplog.clear()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        check_scanner(settings, ClamdScanner(("127.0.0.1", port)))
        assert "could not be reached" in caplog.text and "wait in the queue" in caplog.text

        # Something that is not clamd stops it.
        clamd.pong = b"HELLO\0"
        with pytest.raises(ScanError, match="not ClamAV's daemon"):
            check_scanner(settings, ClamdScanner(clamd.server_address))

        # No scanner, unset or off, is development's default and a team's choice, said at
        # every start of a deployment, which it does not stop.
        caplog.clear()
        check_scanner(settings, None)
        assert caplog.text == ""
        monkeypatch.setattr(settings, "environment", "production")
        check_scanner(settings, None)
        assert "SCANNER_URL names no scanner: uploads are not scanned" in caplog.text
        assert "Name the ClamAV daemon, as clamd://clamav:3310, to scan them." in caplog.text


def test_a_flagged_upload_is_quarantined_and_nothing_of_it_is_served(
    client, session, services, sample_pdf, tmp_path
):
    project = create_project(client)
    bad = flagged_pdf(tmp_path, sample_pdf)
    clean, flagged = upload(client, project["id"], sample_pdf, bad)["documents"]

    # Nothing is served before its scan has passed, the clean file included.
    for document in (clean, flagged):
        response = client.get(f"/api/documents/{document['id']}/file")
        assert response.status_code == 409 and response.json()["detail"] == UNSCANNED_FILE
    for document in (clean, flagged):
        process_document(document["id"], services)
    assert client.get(f"/api/documents/{clean['id']}/file").status_code == 200

    shown = client.get(f"/api/documents/{flagged['id']}").json()
    assert shown["status"] == "quarantined"
    assert shown["error"] == quarantine_message(EICAR_NAME)
    assert shown["error"] == (
        f"The malware scan flagged this file as {EICAR_NAME}. It is quarantined: not indexed, "
        "and nobody can open or download it. Delete it."
    )
    assert {name: stage["status"] for name, stage in shown["stages"].items()} == {
        "scan": "failed",
        "parse": "pending",
        "structure": "pending",
        "enrich": "pending",
        "embed": "pending",
    }
    assert shown["stages"]["scan"]["detail"] == shown["error"]
    assert shown["page_count"] is None and shown["usage"].keys() == {"upload"}

    # The file is refused for good by every route that would open it, and nothing of it is
    # in the index.
    for route in ("file", "thumbnail.png"):
        response = client.get(f"/api/documents/{flagged['id']}/{route}")
        assert response.status_code == 403 and response.json()["detail"] == QUARANTINED_FILE
    assert client.get(f"/api/documents/{flagged['id']}/pages/1.png").status_code == 404
    assert client.get(f"/api/documents/{flagged['id']}/content").json()["blocks"] == []
    assert client.get(f"/api/documents/{flagged['id']}/outline").json() == []
    statuses = {d["filename"]: d["status"] for d in listed(client, project["id"])}
    assert statuses == {"msa.pdf": "ready", "bad.pdf": "quarantined"}

    # Running the project's documents again leaves it alone, and the job that scanned it is
    # done, not a dead letter: the verdict is the outcome, not a failure of the worker.
    every = {"from_stage": "enrich"}
    assert client.post(f"/api/projects/{project['id']}/reprocess", params=every).json() == {
        "queued": 1
    }
    assert session.scalars(text("SELECT status FROM jobs WHERE status = 'failed'")).all() == []

    # An editor deletes it, and the file goes with it once it is deleted from the trash.
    key = session.get(Document, flagged["id"]).storage_key
    assert services.storage.exists(key)
    assert client.delete(f"/api/documents/{flagged['id']}").status_code == 204
    assert client.delete(f"/api/trash/documents/{flagged['id']}").status_code == 204
    assert not services.storage.exists(key)


def test_index_again_scans_a_quarantined_document_once_more(
    client, session, services, sample_pdf, tmp_path
):
    project = create_project(client)
    (flagged,) = upload(client, project["id"], flagged_pdf(tmp_path, sample_pdf))["documents"]
    process_document(flagged["id"], services)
    document = session.get(Document, flagged["id"])
    assert document.status == "quarantined"

    # Scanned again and flagged again: quarantined still, and nothing else ran.
    assert client.post(f"/api/documents/{flagged['id']}/reprocess").status_code == 202
    session.refresh(document)
    assert (document.status, document.error) == ("queued", None)
    process_document(flagged["id"], services)
    session.refresh(document)
    assert document.status == "quarantined" and len(services.scanner.scanned) == 2
    assert services.llm.calls == []

    # With signatures that no longer flag it, the same request indexes it like any upload.
    services.scanner.flags_eicar = False
    assert client.post(f"/api/documents/{flagged['id']}/reprocess").status_code == 202
    process_document(flagged["id"], services)
    session.refresh(document)
    assert document.status == "ready" and document.error is None
    assert set(stage_statuses(document).values()) == {"done"}
    assert client.get(f"/api/documents/{flagged['id']}/file").status_code == 200


def test_a_document_flagged_by_a_later_scan_loses_its_index(client, session, services, indexed):
    # Indexed while clean, then flagged by newer signatures when scanned again: what was
    # indexed of it goes, so that neither search nor the agent reads it. A passed scan is not
    # run again by "Index again" (from parse); a run from the scan stage asks for it.
    assert client.get(f"/api/projects/{indexed.project_id}/search", params={"q": "notice"}).json()[
        "hits"
    ]
    services.scanner.flagging = True
    assert client.post(f"/api/documents/{indexed.id}/reprocess").status_code == 202
    process_document(indexed.id, services)
    session.refresh(indexed)
    assert indexed.status == "ready" and len(services.scanner.scanned) == 1

    assert client.post(f"/api/documents/{indexed.id}/reprocess?from_stage=scan").status_code == 202
    process_document(indexed.id, services)
    session.refresh(indexed)
    assert indexed.status == "quarantined"
    for model in (Block, Section, Passage, Fact):
        assert rows(session, model, indexed) == []
    hits = client.get(f"/api/projects/{indexed.project_id}/search", params={"q": "notice"}).json()
    assert hits["hits"] == []
    assert client.get(f"/api/projects/{indexed.project_id}/facts").json()["facts"] == []


def test_without_a_scanner_the_stage_is_skipped_and_the_file_is_served(
    client, session, services, sample_pdf
):
    services.scanner = None
    project = create_project(client)
    (document,) = upload(client, project["id"], sample_pdf)["documents"]
    process_document(document["id"], services)
    shown = client.get(f"/api/documents/{document['id']}").json()
    assert shown["status"] == "ready"
    assert shown["stages"]["scan"] == {
        "status": "skipped",
        "detail": NOT_SCANNED,
        "retry_at": None,
        "deferred": False,
    }
    assert NOT_SCANNED == (
        "No malware scanner is configured (SCANNER_URL), so this file was not scanned."
    )
    assert UNSCANNED == "Uploads are not scanned for malware, so this file was not scanned."
    assert client.get(f"/api/documents/{document['id']}/file").status_code == 200


def test_a_scanner_that_is_down_holds_the_upload_until_it_answers(
    client, session, services, sample_pdf
):
    project = create_project(client)
    (uploaded,) = upload(client, project["id"], sample_pdf)["documents"]
    services.scanner.outage = SCANNER_DOWN
    run_index(claim("w:1", INDEX), services)

    # The scan is required, so the document waits as queued for the retry, saying why, and
    # the file is not served meanwhile.
    document = session.get(Document, uploaded["id"])
    session.refresh(document)
    assert (document.status, document.error) == ("queued", None)
    assert stage_statuses(document) == {
        "scan": "failed",
        "parse": "pending",
        "structure": "pending",
        "enrich": "pending",
        "embed": "pending",
    }
    assert document.stages["scan"]["detail"] == (
        f"{SCANNER_DOWN} Retrying in 1 minute (1 of {MAX_RETRIES})."
    )
    assert client.get(f"/api/documents/{uploaded['id']}/file").status_code == 409

    # The daemon is back: the retry scans and indexes.
    services.scanner.outage = None
    session.execute(text("UPDATE jobs SET run_at = now() WHERE status = 'queued'"))
    session.commit()
    run_index(claim("w:1", INDEX), services)
    session.refresh(document)
    assert document.status == "ready" and stage_statuses(document)["scan"] == "done"
    assert client.get(f"/api/documents/{uploaded['id']}/file").status_code == 200


def test_a_deployments_members_read_that_the_scanner_is_down_without_its_address(
    client, session, services, sample_pdf, monkeypatch, caplog
):
    monkeypatch.setattr(get_settings(), "environment", "production")
    project = create_project(client)
    (uploaded,) = upload(client, project["id"], sample_pdf)["documents"]
    services.scanner.outage = SCANNER_DOWN
    with caplog.at_level("INFO", logger="app.worker"):
        run_index(claim("w:1", INDEX), services)

    document = session.get(Document, uploaded["id"])
    session.refresh(document)
    assert document.stages["scan"]["detail"] == (
        f"{ScanError.members} Retrying in 1 minute (1 of {MAX_RETRIES})."
    )
    # The operator reads the daemon's address in the log, with the retry.
    assert str(SCANNER_DOWN) in caplog.text

    # The last retry fails too: the document says so in the members' words, and the job that
    # gave up keeps the operator's for the dead letters (tests/test_deadletters.py).
    session.execute(
        text("UPDATE jobs SET run_at = now(), payload = payload || :retry WHERE status = 'queued'"),
        {"retry": f'{{"retry": {MAX_RETRIES}}}'},
    )
    session.commit()
    worker = Worker(services, concurrency=1)
    worker._run(claim(worker.id, INDEX))
    session.refresh(document)
    assert document.status == "failed"
    assert document.error == (
        f'{ScanError.members} Tried {MAX_RETRIES + 1} times over 15 minutes; use "Index '
        'again" once the service is back.'
    )
    shown = client.get(f"/api/documents/{uploaded['id']}").text
    assert "clamd://" not in shown and "SCANNER_URL" not in shown
    last = session.execute(text("SELECT last_error FROM jobs ORDER BY id DESC LIMIT 1")).scalar()
    assert last == f"Stage scan failed {MAX_RETRIES + 1} times over 15 minutes: {SCANNER_DOWN}"


def test_quarantines_are_mailed_to_the_operator_once(
    client, session, services, account, sample_pdf, tmp_path
):
    project = create_project(client)
    one, two = upload(
        client,
        project["id"],
        flagged_pdf(tmp_path, sample_pdf, "one.pdf"),
        flagged_pdf(tmp_path, sample_pdf, "two.pdf"),
    )["documents"]
    for document in (one, two):
        process_document(document["id"], services)
    mailer = FakeMailer()

    # Without an address nothing is sent and nothing is marked.
    assert alert_quarantines(mailer, "") == 0 and mailer.sent == []

    assert alert_quarantines(mailer, OPERATOR) == 2
    ((to, subject, body),) = mailer.sent
    assert to == OPERATOR and subject == "Emudoc quarantined 2 uploads"
    assert body.startswith("The malware scan flagged 2 uploads. Each is quarantined")
    slug = account.organization.slug
    assert (
        f"{slug} / Contracts / one.pdf  uploaded by {account.user.email}\n"
        f"    {quarantine_message(EICAR_NAME)}\n"
        f"{slug} / Contracts / two.pdf  uploaded by {account.user.email}\n"
    ) in body
    assert alert_quarantines(mailer, OPERATOR) == 0 and len(mailer.sent) == 1

    # Cleared by a scan with newer signatures and flagged again by a later one, a document
    # is reported again; one that stays quarantined through a rescan is not.
    client.post(f"/api/documents/{two['id']}/reprocess")
    process_document(two["id"], services)
    services.scanner.flags_eicar = False
    client.post(f"/api/documents/{one['id']}/reprocess")
    process_document(one["id"], services)
    session.expire_all()
    assert session.get(Document, one["id"]).status == "ready"
    services.scanner.flags_eicar = True
    client.post(f"/api/documents/{one['id']}/reprocess?from_stage=scan")
    process_document(one["id"], services)
    assert alert_quarantines(mailer, OPERATOR) == 1
    assert mailer.sent[-1][1] == "Emudoc quarantined 1 upload" and "one.pdf" in mailer.sent[-1][2]


def test_the_worker_reports_quarantines_from_its_alerter_thread(
    client, session, services, sample_pdf, tmp_path, monkeypatch
):
    project = create_project(client)
    (flagged,) = upload(client, project["id"], flagged_pdf(tmp_path, sample_pdf))["documents"]
    process_document(flagged["id"], services)
    monkeypatch.setattr(get_settings(), "alert_email", OPERATOR)
    monkeypatch.setattr(worker_module, "ALERT_INTERVAL", 0.05)
    mailer = FakeMailer()

    worker = Worker(services, concurrency=0, mailer=mailer)
    worker.start()
    try:
        wait_until(lambda: bool(mailer.sent))
    finally:
        worker.stop()
    assert mailer.sent[0][1] == "Emudoc quarantined 1 upload"
