"""Malware scanning of uploads, and what becomes of a file that is flagged.

Every member of a project can open the files the others uploaded, so a file is scanned before
anything reads it: the `scan` stage is the first of the pipeline (app/indexing/pipeline.py),
ahead of the parse that hands the file to a parser, and the routes that serve a file
(app/api/documents.py) refuse one whose scan has not passed (`scan_passed`). The scanner is a
ClamAV daemon reached as SCANNER_URL names it (`ClamdScanner`): the file is streamed to it
over clamd's INSTREAM command, so the daemon needs no access to the storage and can run on
another machine. A scanner that does not answer is a passing problem: the stage fails
transient, the worker tries it again, and the upload waits in the queue meanwhile.

A file the scanner flags is quarantined: the document's status says so, its error names the
threat for the members, and the file stays where it is but is never served or parsed; an
editor deletes it, or indexes it again to have it scanned once more. The operator hears of
each quarantine once, by mail from the worker's alerter thread (`alert_quarantines`), like the
jobs the worker gave up on (app/deadletters.py).

Without SCANNER_URL nothing is scanned and the stage is skipped, which the document shows.
Production refuses that unless the setting is `off` on purpose (app/config.py).
"""

import logging
import socket
import struct
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import and_, select, text
from sqlalchemy.orm import Session

from app.config import Settings, parse_scanner_url
from app.db import SessionLocal
from app.errors import AppError
from app.mail import Mailer, Message
from app.models import Document, Organization, Project, UsageEvent, User
from app.usage import DOCUMENT, UPLOAD

log = logging.getLogger(__name__)

# How long one scan may take, in seconds, once the daemon answered the connection: the time
# to stream an upload of MAX_UPLOAD_MB and for the daemon to unpack and read it.
TIMEOUT = 300
CONNECT_TIMEOUT = 10
CHUNK = 64 * 1024
# What a document's stage says when there was no scanner to run.
NOT_SCANNED = "No malware scanner is configured (SCANNER_URL), so this file was not scanned."
# The same, as a deployment's members are told it (app/errors.py, `shown`).
UNSCANNED = "Uploads are not scanned for malware, so this file was not scanned."
# How many quarantines one digest lists in full; the mail is the alert, not the listing.
DIGEST_LIMIT = 50


class ScanError(AppError):
    """The scanner could not say whether the file is clean: it could not be reached (passing)
    or it refused the file (not). The message names the daemon and its settings, the
    operator's; the members are told that the file waits for the scanner."""

    members = "The malware scanner is unavailable just now, so this file cannot be opened yet."


class Scanner(ABC):
    """What the pipeline asks of a malware scanner; one implementation per daemon."""

    @abstractmethod
    def check(self) -> str:
        """Make sure the scanner answers, and say which it is (its version and signatures),
        or raise `ScanError` saying what to fix."""

    @abstractmethod
    def scan(self, path: Path) -> str | None:
        """The name of the threat found in the file, or None when it is clean. Raises
        `ScanError` when no verdict could be had."""


class ClamdScanner(Scanner):
    """ClamAV's daemon over its own protocol, by TCP address or Unix socket path.

    The commands are sent with the `z` prefix and NUL terminators, so that a reply ends at a
    NUL rather than at a newline a file name could contain. INSTREAM sends the file as
    length-prefixed chunks and a zero-length one to end it; the daemon answers with the
    verdict for "stream".
    """

    def __init__(self, address: tuple[str, int] | str):
        self.address = address

    @property
    def url(self) -> str:
        if isinstance(self.address, str):
            return f"clamd://{self.address}"
        host, port = self.address
        return f"clamd://{host}:{port}"

    def check(self) -> str:
        reply = self._ask(b"zPING\0")
        if reply != "PONG":
            raise ScanError(
                f"The malware scanner at {self.url} answered {reply!r} to PING, which is not "
                "ClamAV's daemon; check SCANNER_URL."
            )
        return self._ask(b"zVERSION\0")

    def scan(self, path: Path) -> str | None:
        with self._connect() as connection:
            try:
                connection.sendall(b"zINSTREAM\0")
                with path.open("rb") as file:
                    while chunk := file.read(CHUNK):
                        connection.sendall(struct.pack(">I", len(chunk)) + chunk)
                connection.sendall(b"\0\0\0\0")
                reply = _read_reply(connection)
            except OSError as error:
                raise self._unreachable(error) from error
        return self._verdict(reply)

    def _verdict(self, reply: str) -> str | None:
        if reply.endswith(" OK"):
            return None
        if reply.endswith(" FOUND"):
            # "stream: Win.Test.EICAR_HDB-1 FOUND"
            return reply.partition(": ")[2].removesuffix(" FOUND").strip() or "malware"
        if reply.endswith("ERROR"):
            # "INSTREAM size limit exceeded. ERROR": the daemon's own limits, not the file's
            # fault and not the moment's; the operator raises them.
            reason = reply.removesuffix("ERROR").strip().rstrip(".")
            raise ScanError(
                f"The malware scanner could not scan this file ({reason}). Ask the operator "
                "to check clamd's StreamMaxLength and MaxFileSize against the size an upload "
                "may have.",
                members=(
                    "The malware scanner could not scan this file, so it is neither indexed nor "
                    "opened. Try uploading a smaller copy."
                ),
            )
        raise ScanError(
            f"The malware scanner at {self.url} gave no verdict: it answered {reply!r}. Check "
            "that SCANNER_URL names ClamAV's daemon."
        )

    def _ask(self, command: bytes) -> str:
        with self._connect() as connection:
            try:
                connection.sendall(command)
                return _read_reply(connection)
            except OSError as error:
                raise self._unreachable(error) from error

    def _connect(self) -> socket.socket:
        try:
            if isinstance(self.address, str):
                connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                connection.settimeout(CONNECT_TIMEOUT)
                connection.connect(self.address)
            else:
                connection = socket.create_connection(self.address, timeout=CONNECT_TIMEOUT)
        except OSError as error:
            raise self._unreachable(error) from error
        connection.settimeout(TIMEOUT)
        return connection

    def _unreachable(self, error: OSError) -> ScanError:
        reason = error.strerror or type(error).__name__
        if isinstance(error, TimeoutError):
            reason = "it did not answer in time"
        return ScanError(
            f"The malware scanner at {self.url} could not be reached ({reason}).",
            transient=True,
        )


def _read_reply(connection: socket.socket) -> str:
    """The daemon's reply up to its NUL terminator, or up to the close of the connection."""
    received = bytearray()
    while True:
        data = connection.recv(4096)
        if not data:
            break
        received += data
        if b"\0" in received:
            break
    return received.split(b"\0", 1)[0].decode("utf-8", errors="replace").strip()


def scanner_from_settings(settings: Settings) -> Scanner | None:
    address = parse_scanner_url(settings.scanner_url)
    return None if address is None else ClamdScanner(address)


def check_scanner(settings: Settings, scanner: Scanner | None) -> None:
    """At start, say which scanner the uploads go through, or warn when none answers.

    A daemon that is down is a passing problem the stage retries, and one that is still
    loading its signatures answers in a minute, so the process starts and says so rather than
    stopping; a deployment that chose to run without one is reminded of it at every start.
    """
    if scanner is None:
        if settings.environment == "production":
            log.warning(
                "SCANNER_URL is off: uploads are not scanned for malware, and every member of a "
                "project can open what the others upload."
            )
        return
    try:
        log.info("Uploads are scanned by %s", scanner.check())
    except ScanError as error:
        if not error.transient:
            raise
        log.warning("%s Until it answers, uploads wait in the queue and are tried again.", error)


def quarantine_message(threat: str) -> str:
    """What the document says to the project's members once its file was flagged."""
    return (
        f"The malware scan flagged this file as {threat}. It is quarantined: not indexed, and "
        "nobody can open or download it. Delete it."
    )


def scan_passed(document: Document) -> bool:
    """Whether the file may be opened: its scan found nothing, or there was no scanner to run
    it and the stage was skipped. A scan that is pending, running or failed has not passed."""
    return document.stages.get("scan", {}).get("status") in ("done", "skipped")


# The quarantines not reported yet, stamped in the statement that selects them, so that two
# alerter threads cannot both take one (app/deadletters.py does the same for failed jobs).
_TAKE = text(
    "UPDATE documents SET quarantine_reported_at = now() "
    "WHERE status = 'quarantined' AND quarantine_reported_at IS NULL RETURNING id"
)


@dataclass(frozen=True)
class Quarantine:
    """A quarantined document, with the names of where it belongs and who uploaded it."""

    document: Document
    organization: str
    project: str
    uploader: str | None

    @property
    def about(self) -> str:
        """Where the file is, as "acme / Contracts / invoice.pdf"."""
        return f"{self.organization} / {self.project} / {self.document.filename}"


def take_unreported(session: Session) -> list[Quarantine]:
    """The quarantines not reported yet, marked reported as of now in the caller's
    transaction; rolling it back leaves them for the next round."""
    ids = session.scalars(_TAKE).all()
    if not ids:
        return []
    # The uploader is the member whose upload brought the bytes in (app/usage.py); a document
    # made another way, or whose uploader's account is gone, has none.
    rows = session.execute(
        select(Document, Organization.slug, Project.name, User.email)
        .join(Project, Project.id == Document.project_id)
        .join(Organization, Organization.id == Project.org_id)
        .outerjoin(
            UsageEvent,
            and_(
                UsageEvent.kind == UPLOAD,
                UsageEvent.unit == "bytes",
                UsageEvent.ref_type == DOCUMENT,
                UsageEvent.ref_id == Document.id,
            ),
        )
        .outerjoin(User, User.id == UsageEvent.user_id)
        .where(Document.id.in_(ids))
        .order_by(Document.id)
    )
    return [
        Quarantine(document, organization, project, uploader)
        for document, organization, project, uploader in rows
    ]


def describe(quarantine: Quarantine) -> str:
    """One quarantine as two lines: where the file is and who uploaded it; then the verdict."""
    who = f"  uploaded by {quarantine.uploader}" if quarantine.uploader else ""
    return f"{quarantine.about}{who}\n    {quarantine.document.error}"


def quarantines_email(quarantines: list[Quarantine]) -> tuple[str, str]:
    """The digest: the subject says how many, the body says which, up to DIGEST_LIMIT."""
    count = f"{len(quarantines)} upload{'' if len(quarantines) == 1 else 's'}"
    listed = "\n".join(describe(quarantine) for quarantine in quarantines[:DIGEST_LIMIT])
    more = len(quarantines) - DIGEST_LIMIT
    body = (
        f"The malware scan flagged {count}. Each is quarantined: not indexed, and served to "
        "nobody; the document shows the verdict to the project's members, who can delete it "
        'or have it scanned again with "Index again".\n\n'
        f"{listed}\n" + (f"\nand {more} more.\n" if more > 0 else "")
    )
    return f"Emudoc quarantined {count}", body


def alert_quarantines(mailer: Mailer, to: str) -> int:
    """Mail the quarantines not yet reported to `to`, as one message, and mark them reported.

    The marking is committed only once the message was handed over, so that a relay that is
    down leaves them for the next round. Returns how many were reported; nothing is taken
    without an address.
    """
    if not to:
        return 0
    with SessionLocal() as session:
        quarantines = take_unreported(session)
        if not quarantines:
            return 0
        subject, body = quarantines_email(quarantines)
        mailer.send(Message(to, subject, body))
        session.commit()
    return len(quarantines)
