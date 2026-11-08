"""The converter: a small HTTP server that turns a Word, PowerPoint, Excel, RTF or HTML file into a
PDF with LibreOffice, so that the document pane can show it as it looks, page by page
(app/conversion.py is the app's side, which sends it each file and keeps the PDF).

LibreOffice reads formats as old and as large as its own through import filters written in C++,
and a document can name other files and addresses for it to fetch: a linked image, a section
linked to another file, a spreadsheet's external data or WEBSERVICE formula. Run in the worker,
such a link would reach the database and the worker's own environment, its keys in it. So it
never runs where the app's secrets are: this module is the image run with another command, as a
container of its own (deploy/compose.yml, `converter`) that is given no settings, no volume and
no network but one it shares with the worker alone, which sends it the file over HTTP and keeps
the PDF it answers.

Within it, each conversion runs LibreOffice in a profile of its own, copied from one made at the
start whose settings (`PROFILE_SETTINGS`) run no macro, update no link, load nothing a document
refers to outside itself and recalculate no spreadsheet, so that what was saved is what is drawn;
as a process group of its own, killed past `--timeout`; and `--concurrency` at once, a request
past them waiting its turn as long as one conversion may take.

It imports nothing of the app, which would ask for the settings the container is not given: the
standard library alone.

    POST /convert?type=docx   the file as the body. 200 and the PDF; 400 for a type it does not
                              convert; 411 without a length; 413 past --max-mb; 422 for a file
                              LibreOffice could not convert; 503 while every slot stayed taken;
                              504 for a conversion past --timeout. An error's body is a sentence.
    GET /health               200 and LibreOffice's version as JSON, once the first conversion
                              at the start has made the profile every other copies.
"""

import argparse
import json
import logging
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

log = logging.getLogger("app.converter")

# The types it converts, by the extension the app names them by (CONVERTIBLE in
# app/conversion.py), each with the one import filter LibreOffice reads it with. LibreOffice
# would otherwise tell a format by the file's content, so that an OpenDocument file or an old
# binary one sent as a .docx would be read by a filter of its own, which the upload's checks
# of a .docx never had in mind; held to the type's filter, it is refused.
TYPES = {
    "docx": "MS Word 2007 XML",
    "pptx": "Impress MS PowerPoint 2007 XML",
    "xlsx": "Calc MS Excel 2007 XML",
    "rtf": "Rich Text Format",
    "html": "HTML (StarWriter)",
}
# LibreOffice's command, which the tests replace with a stand-in.
LIBREOFFICE = ("soffice",)
CHUNK = 1024 * 1024
PDF_HEADER = b"%PDF-"
# What the profile made at the start converts, to have LibreOffice write the rest of its profile
# once rather than for every file, and to know at the start that it runs.
FIRST_FILE = "<!doctype html><title>Emudoc</title><p>The converter's first page.</p>"

# The settings every conversion's profile starts from, in LibreOffice's own format
# (registrymodifications.xcu), each a defence against a document that names something outside
# itself: no macro runs, whoever signed it (MacroSecurityLevel 3, "very high", with the
# execution off); a link from the document to a file or an address is not followed
# (BlockUntrustedRefererLinks, since no location is trusted: with LibreOffice's defaults a .docx
# whose picture is linked to a path drew that file into its PDF, tried on 2026-11-08); a linked
# section, field or object is never updated from its source (Link: Writer's 2 and Calc's 1 are
# "never", each component numbering always, never and on request in an order of its own); and
# a spreadsheet's formulas keep the values it was saved with (RecalcMode 1, "never"), so that
# WEBSERVICE and external references fetch nothing and the page shows what its author last saw.
# A formula saved without a value is still worked out.
PROFILE_SETTINGS = """\
<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry" \
xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<item oor:path="/org.openoffice.Office.Common/Security/Scripting">\
<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Security/Scripting">\
<prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Security/Scripting">\
<prop oor:name="BlockUntrustedRefererLinks" oor:op="fuse"><value>true</value></prop></item>
<item oor:path="/org.openoffice.Office.Writer/Content/Update">\
<prop oor:name="Link" oor:op="fuse"><value>2</value></prop></item>
<item oor:path="/org.openoffice.Office.Calc/Content/Update">\
<prop oor:name="Link" oor:op="fuse"><value>1</value></prop></item>
<item oor:path="/org.openoffice.Office.Calc/Formula/Load">\
<prop oor:name="OOXMLRecalcMode" oor:op="fuse"><value>1</value></prop></item>
<item oor:path="/org.openoffice.Office.Calc/Formula/Load">\
<prop oor:name="ODFRecalcMode" oor:op="fuse"><value>1</value></prop></item>
</oor:items>
"""

UNKNOWN_TYPE = f"The converter takes {', '.join(TYPES)} files; name the type as ?type=…."
NO_LENGTH = "The file's length is needed, as Content-Length."
TOO_LARGE = "The file is larger than the converter takes ({limit} MB)."
UNCONVERTED = "LibreOffice could not read the file, so it was not converted."
BUSY = "Every conversion slot stayed taken; try again in a moment."
TIMED_OUT = "Converting the file took longer than {limit} seconds, so it was stopped."
NOT_FOUND = "The converter answers POST /convert and GET /health."


class Failed(Exception):
    """A conversion that did not give a PDF: the status to answer with and the sentence why."""

    def __init__(self, status: HTTPStatus, sentence: str):
        super().__init__(sentence)
        self.status = status


@dataclass(frozen=True)
class Limits:
    concurrency: int
    timeout: float
    max_bytes: int


class LibreOffice:
    """LibreOffice run once a file, in a copy of the profile `start` made."""

    def __init__(self, work: Path, limits: Limits, command: Sequence[str] = LIBREOFFICE):
        self.work = work
        self.limits = limits
        self.command = tuple(command)
        self.profile = work / "profile"
        self.version = ""
        self._slots = threading.BoundedSemaphore(limits.concurrency)

    def start(self) -> None:
        """Make the profile every conversion copies, by converting a first file in it, and
        learn LibreOffice's version; raises `Failed` when LibreOffice does not run."""
        user = self.profile / "user"
        user.mkdir(parents=True, exist_ok=True)
        (user / "registrymodifications.xcu").write_text(PROFILE_SETTINGS)
        with tempfile.TemporaryDirectory(dir=self.work) as job:
            source = Path(job) / "document.html"
            source.write_text(FIRST_FILE)
            self._run(source, self.profile)
        version = subprocess.run(
            [*self.command, "--version"],
            capture_output=True,
            env=_environment(self.work),
            timeout=self.limits.timeout,
            check=False,
        )
        self.version = version.stdout.decode(errors="replace").strip()

    def convert(self, source: Path) -> Path:
        """The PDF made of `source`, beside it; raises `Failed`."""
        if not self._slots.acquire(timeout=self.limits.timeout):
            raise Failed(HTTPStatus.SERVICE_UNAVAILABLE, BUSY)
        try:
            profile = source.parent / "profile"
            shutil.copytree(self.profile, profile, symlinks=True)
            return self._run(source, profile)
        finally:
            self._slots.release()

    def _run(self, source: Path, profile: Path) -> Path:
        out = source.parent / "out"
        out.mkdir()
        command = [
            *self.command,
            f"-env:UserInstallation={profile.as_uri()}",
            f"--infilter={TYPES[source.suffix[1:]]}",
            "--headless",
            "--invisible",
            "--nocrashreport",
            "--nodefault",
            "--nofirststartwizard",
            "--nolockcheck",
            "--nologo",
            "--norestore",
            "--convert-to",
            "pdf",
            "--outdir",
            str(out),
            str(source),
        ]
        # A group of its own, so that the time limit kills soffice.bin with the script that
        # started it, and nothing of a conversion outlives it.
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_environment(source.parent),
            start_new_session=True,
        )
        try:
            process.wait(self.limits.timeout)
        except subprocess.TimeoutExpired:
            raise Failed(
                HTTPStatus.GATEWAY_TIMEOUT, TIMED_OUT.format(limit=f"{self.limits.timeout:g}")
            ) from None
        finally:
            with suppress(ProcessLookupError, PermissionError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        pdf = out / f"{source.stem}.pdf"
        try:
            with pdf.open("rb") as file:
                if file.read(len(PDF_HEADER)) == PDF_HEADER:
                    return pdf
        except FileNotFoundError:
            pass
        raise Failed(HTTPStatus.UNPROCESSABLE_ENTITY, UNCONVERTED)


def _environment(home: Path) -> dict[str, str]:
    """What LibreOffice runs with: a home and a temporary directory inside the conversion's own
    directory, which goes with it, and nothing else of this process's environment."""
    kept = {name: os.environ[name] for name in ("PATH",) if name in os.environ}
    return {**kept, "HOME": str(home), "TMPDIR": str(home), "LANG": "C.UTF-8"}


class Handler(BaseHTTPRequestHandler):
    server: "ConverterServer"
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        if urlsplit(self.path).path != "/health":
            self._sentence(HTTPStatus.NOT_FOUND, NOT_FOUND)
            return
        body = json.dumps({"libreoffice": self.server.libreoffice.version}).encode()
        self._answer(HTTPStatus.OK, "application/json", body)

    def do_POST(self) -> None:
        url = urlsplit(self.path)
        if url.path != "/convert":
            self._sentence(HTTPStatus.NOT_FOUND, NOT_FOUND, close=True)
            return
        kind = parse_qs(url.query).get("type", [""])[0]
        if kind not in TYPES:
            self._sentence(HTTPStatus.BAD_REQUEST, UNKNOWN_TYPE, close=True)
            return
        length = self.headers.get("Content-Length", "")
        if not length.isdigit():
            self._sentence(HTTPStatus.LENGTH_REQUIRED, NO_LENGTH, close=True)
            return
        libreoffice = self.server.libreoffice
        if int(length) > libreoffice.limits.max_bytes:
            limit = libreoffice.limits.max_bytes // (1024 * 1024)
            self._sentence(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, TOO_LARGE.format(limit=limit), True)
            return
        started = time.monotonic()
        with tempfile.TemporaryDirectory(dir=libreoffice.work) as job:
            source = Path(job) / f"document.{kind}"
            self._receive(source, int(length))
            try:
                pdf = libreoffice.convert(source)
            except Failed as failure:
                log.warning(
                    "A %s file of %s bytes was not converted after %.1f s: %s",
                    kind,
                    length,
                    time.monotonic() - started,
                    failure,
                )
                self._sentence(failure.status, str(failure))
                return
            size = pdf.stat().st_size
            log.info(
                "A %s file of %s bytes became a PDF of %s bytes in %.1f s",
                kind,
                length,
                size,
                time.monotonic() - started,
            )
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with pdf.open("rb") as file:
                shutil.copyfileobj(file, self.wfile, CHUNK)

    def _receive(self, path: Path, length: int) -> None:
        with path.open("wb") as file:
            while length:
                chunk = self.rfile.read(min(CHUNK, length))
                if not chunk:
                    raise ConnectionError("The request ended before its body did.")
                file.write(chunk)
                length -= len(chunk)

    def _sentence(self, status: HTTPStatus, sentence: str, close: bool = False) -> None:
        # A refusal before the body was read leaves it unread, so the connection cannot carry
        # another request.
        self.close_connection = close
        self._answer(status, "text/plain; charset=utf-8", sentence.encode(), close)

    def _answer(self, status: HTTPStatus, kind: str, body: bytes, close: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        if close:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        # Each conversion is logged as it ends, above; the request lines would say no more.
        pass


class ConverterServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], libreoffice: LibreOffice):
        self.libreoffice = libreoffice
        super().__init__(address, Handler)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.converter", description=__doc__.split("\n\n")[0]
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--concurrency", type=int, default=2, help="conversions at once")
    parser.add_argument("--timeout", type=float, default=120, help="seconds a conversion may take")
    parser.add_argument("--max-mb", type=int, default=100, help="the largest file it takes")
    parser.add_argument("--work", type=Path, default=Path(tempfile.gettempdir()) / "converter")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args.work.mkdir(parents=True, exist_ok=True)
    limits = Limits(max(args.concurrency, 1), args.timeout, args.max_mb * 1024 * 1024)
    libreoffice = LibreOffice(args.work, limits)
    try:
        libreoffice.start()
    except Failed as failure:
        log.error("LibreOffice did not convert the first file, so the converter stops: %s", failure)
        return 1
    server = ConverterServer((args.host, args.port), libreoffice)
    log.info(
        "Converting with %s on %s:%s, %s at once",
        libreoffice.version,
        args.host,
        args.port,
        limits.concurrency,
    )
    # Docker's stop asks with SIGTERM: the conversions in progress end with the process, and the
    # worker asks again for what it was not answered.
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown).start())
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
