"""Drawing a PDF's pages as images, in child processes each process keeps for it: the pages of
the document pane and the library's thumbnails (app/api/documents.py), the scans the parse stage
reads (app/indexing/pipeline.py), and a PDF's first pages, drawn ahead once it is parsed so that
opening it shows them at once (`draw_ahead`, the worker's `render_pages` job).

pdfium is not thread-safe, not even with a different document in each thread, so a process used
to draw under one lock: the pages members scrolled to, the thumbnails and a scan's OCR waited for
each other, one page at a time, and a page that took long held them all up. And it drew inside
the API and the worker themselves, on files anyone with an account can upload, so a page that
asked for gigabytes took the process with it.

So every page is drawn by a child (app/parsing/render.py), one of a pool of RENDER_PROCESSES per
process, started when first needed and kept for the next pages, so that a page costs its drawing
and not an interpreter's start. A child draws one page at a time, so the pool draws as many side
by side, and each within limits: RENDER_MEMORY_MB of address space, RENDER_TIMEOUT seconds by
this process's clock, and a longer side of at most MAX_EDGE pixels. A child that ran out of time
or memory, crashed or answered what it should not is killed, and a fresh one draws the next page;
one that drew RENDERS_PER_CHILD pages is retired, so that what a long run leaves behind in one
does not grow.

A page is drawn from a local file (`Storage.localize`, or `Storage.kept` for the pages and
thumbnails the API draws, whose copy of a bucket's original serves the next pages too); the
child is sent its path, never the document's name. The metrics and the run's spans name what a
page was drawn for and how it ended, never the document.
"""

import atexit
import json
import logging
import os
import select
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from queue import LifoQueue

from app.config import get_settings
from app.db import SessionLocal
from app.errors import AppError
from app.metrics import RENDER_SECONDS, RENDERS
from app.models import Document
from app.parsing.limits import BACKEND_DIR, KEPT_VARIABLES, OUT_OF_MEMORY
from app.scanning import scan_passed
from app.storage import Storage, StorageError
from app.tracing import RENDER, note
from app.usage import DOCUMENT, record
from app.usage import RENDER as RENDERED

log = logging.getLogger(__name__)

# Pixels per point of a page drawn for the document pane and for the model to read: 144 to the
# inch, sharp on a high-density screen and for OCR.
SCALE = 2.0
# The longest side of a page drawn, in pixels, whatever its size in the PDF: the most a model
# reads of an image, the bound MAX_IMAGE_EDGE (app/indexing/pipeline.py) puts on an uploaded
# one, and a bitmap of about 25 MB at most. A page as large as the format allows, 200 inches a
# side, would take over 3 GB at SCALE.
MAX_EDGE = 2576
# How many pages a child draws before it is replaced by a fresh one.
RENDERS_PER_CHILD = 200
# What a page is drawn for, as the metrics count it: the document pane, a thumbnail, a scan for
# the model to read, a first page drawn ahead.
FOR_PANE = "pane"
FOR_THUMBNAIL = "thumbnail"
FOR_OCR = "ocr"
AHEAD = "ahead"
# How a page's drawing ended: drawn; refused, a page the child could not draw (damaged, or not
# there); timeout; out_of_memory; crashed, the child ended or failed otherwise; unstarted, no
# child could be started, or the process is stopping.
OUTCOMES = ("drawn", "refused", "timeout", "out_of_memory", "crashed", "unstarted")
for _purpose in (FOR_PANE, FOR_THUMBNAIL, FOR_OCR, AHEAD):
    RENDER_SECONDS.labels(_purpose)
    for _outcome in OUTCOMES:
        RENDERS.labels(_purpose, _outcome)

TIMED_OUT = (
    "Drawing the page took longer than {limit}, so it was stopped. Save the file again from "
    "the program that made it, or split it, and upload that."
)
OUT_OF_ROOM = (
    "Drawing the page needs more than {limit}, so it was stopped. Save the file again from "
    "the program that made it, with smaller images, and upload that."
)
CRASHED = (
    "Drawing the page stopped unexpectedly. Check that the file opens in another program; if it "
    "does, ask support about it."
)
UNSTARTED = "No process could be started to draw the page. Try again in a moment."
STOPPING = "The app is stopping, so the page was not drawn. Try again in a moment."


class RenderError(AppError):
    """A page was not drawn. The message says why, as a sentence a member can act on, and
    `outcome` names it for the metrics (OUTCOMES)."""

    def __init__(self, message: str, outcome: str, *, transient: bool = False):
        super().__init__(message, transient=transient)
        self.outcome = outcome


class _Lost(Exception):
    """The child gave no reply to read: it ended, or wrote what is not one."""


class _Child:
    """One render process, and how many pages it drew."""

    def __init__(self, process: subprocess.Popen):
        self.process = process
        self.drawn = 0
        self._readable = select.poll()
        self._readable.register(process.stdout.fileno(), select.POLLIN)

    def ask(self, request: dict, timeout: float) -> tuple[dict, bytes]:
        """Send one request and read its reply within `timeout` seconds (0 waits as long as it
        takes): `TimeoutError` past it, `_Lost` when the child ended or wrote nonsense."""
        deadline = time.monotonic() + timeout if timeout else None
        data = json.dumps(request).encode() + b"\n"
        try:
            while data:
                data = data[os.write(self.process.stdin.fileno(), data) :]
        except BrokenPipeError:
            raise _Lost from None
        out = self.process.stdout.fileno()
        buffer = bytearray()
        header: dict | None = None
        size = 0
        while True:
            if header is None and (end := buffer.find(b"\n")) >= 0:
                header, size = _header(bytes(buffer[:end]))
                del buffer[: end + 1]
            if header is not None and len(buffer) >= size:
                return header, bytes(buffer[:size])
            wait = None if deadline is None else deadline - time.monotonic()
            if wait is not None and wait <= 0:
                raise TimeoutError
            if not self._readable.poll(None if wait is None else wait * 1000):
                raise TimeoutError
            chunk = os.read(out, max(1 << 16, size - len(buffer)))
            if not chunk:
                raise _Lost
            buffer += chunk

    def end(self) -> int:
        """Stop the process, if it still runs, and return how it ended. Only the thread that
        holds the child closes its pipes: another one could still be reading from them."""
        if self.process.poll() is None:
            self.process.kill()
        code = self.process.wait()
        self.process.stdin.close()
        self.process.stdout.close()
        return code


def _header(line: bytes) -> tuple[dict, int]:
    """A reply's first line, and the bytes of the image that follow it."""
    try:
        header = json.loads(line)
    except ValueError:
        raise _Lost from None
    if not isinstance(header, dict):
        raise _Lost
    size = header.get("png", 0)
    if not isinstance(size, int) or size < 0:
        raise _Lost
    return header, size


class Renderer:
    """A pool of `processes` render children, each capped at `memory_mb` of address space (0 is
    no cap). Threads share it: a page waits for a child that is free."""

    def __init__(self, processes: int, memory_mb: int = 0):
        self._memory_mb = memory_mb
        # A slot is a child that is free, or None for one not started yet; the last one given
        # back is taken first, so that a quiet process keeps one child running, not all.
        self._free: LifoQueue[_Child | None] = LifoQueue()
        for _ in range(processes):
            self._free.put(None)
        self._lock = threading.Lock()
        self._children: set[_Child] = set()
        self._closed = False

    def render(
        self,
        path: Path,
        page: int,
        *,
        purpose: str,
        scale: float | None = SCALE,
        edge: int = MAX_EDGE,
        timeout: float | None = None,
    ) -> bytes:
        """Page `page` (1-based) of the PDF at `path` as a PNG, at `scale` pixels per point and
        no longer than `edge` on its longer side; without `scale`, exactly that long, as for a
        thumbnail. `timeout` is RENDER_TIMEOUT unless given. Raises `RenderError`."""
        if timeout is None:
            timeout = get_settings().render_timeout
        began = time.monotonic()
        outcome = "drawn"
        try:
            return self._render(
                {
                    "path": str(path.absolute()),
                    "page": page,
                    "scale": scale,
                    "edge": edge,
                    "seconds": timeout,
                },
                timeout,
            )
        except RenderError as error:
            outcome = error.outcome
            raise
        finally:
            RENDERS.labels(purpose, outcome).inc()
            RENDER_SECONDS.labels(purpose).observe(time.monotonic() - began)
            note(RENDER, began, purpose=purpose, outcome=outcome)

    def _render(self, request: dict, timeout: float) -> bytes:
        child = self._free.get()
        kept: _Child | None = None
        try:
            if child is None:
                child = self._start()
            header, image = self._ask(child, request, timeout)
            child.drawn += 1
            if child.drawn < RENDERS_PER_CHILD:
                kept = child
            if "png" in header:
                return image
            # A page the child could not draw, which leaves the child as it was.
            raise RenderError(str(header["error"]), "refused")
        finally:
            if child is not None and kept is None:
                self._end(child)
            self._free.put(kept)

    def _ask(self, child: _Child, request: dict, timeout: float) -> tuple[dict, bytes]:
        """The child's reply: an image, or a page it could not draw. Whatever else it does
        raises `RenderError`, and the caller replaces the child."""
        try:
            header, image = child.ask(request, timeout)
        except TimeoutError:
            log.warning(
                "A render process was stopped: a page took longer than RENDER_TIMEOUT (%s)",
                _seconds(timeout),
            )
            raise RenderError(TIMED_OUT.format(limit=_seconds(timeout)), "timeout") from None
        except _Lost:
            code = self._end(child)
            if self._closed:
                raise RenderError(STOPPING, "unstarted", transient=True) from None
            if code == OUT_OF_MEMORY:
                log.warning(
                    "A render process was stopped: a page needed more than RENDER_MEMORY_MB "
                    "(%s MB)",
                    f"{self._memory_mb:,}",
                )
                raise RenderError(
                    OUT_OF_ROOM.format(limit=f"the {self._memory_mb:,} MB of memory allowed"),
                    "out_of_memory",
                ) from None
            if code == -signal.SIGXCPU:
                # The child's own CPU limit, which only comes after this process's clock would
                # have stopped it.
                log.warning("A render process was stopped by its own limit on CPU time")
                raise RenderError(TIMED_OUT.format(limit=_seconds(timeout)), "timeout") from None
            log.warning("A render process ended with code %s while it drew a page", code)
            raise RenderError(CRASHED, "crashed") from None
        if "png" in header or isinstance(header.get("error"), str):
            return header, image
        log.warning(
            "A render process failed to draw a page (%s): %s",
            header.get("crashed", "no reason given"),
            str(header.get("frames", ""))[-2000:].strip() or "(no frames)",
        )
        raise RenderError(CRASHED, "crashed")

    def _start(self) -> _Child:
        command = [sys.executable, "-m", "app.parsing.render", f"--memory-mb={self._memory_mb}"]
        try:
            process = subprocess.Popen(
                command,
                cwd=BACKEND_DIR,
                env={name: os.environ[name] for name in KEPT_VARIABLES if name in os.environ},
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )
        except OSError as error:
            # The machine is out of processes or memory for now.
            raise RenderError(UNSTARTED, "unstarted", transient=True) from error
        child = _Child(process)
        with self._lock:
            self._children.add(child)
            closed = self._closed
        if closed:
            self._end(child)
            raise RenderError(STOPPING, "unstarted", transient=True)
        return child

    def _end(self, child: _Child) -> int:
        with self._lock:
            self._children.discard(child)
        return child.end()

    def close(self) -> None:
        """Stop every child; a page being drawn ends with `RenderError`, and so does any page
        asked for after."""
        with self._lock:
            self._closed = True
            children = list(self._children)
        for child in children:
            if child.process.poll() is None:
                child.process.kill()
        for child in children:
            child.process.wait()


def _seconds(seconds: float) -> str:
    return "1 second" if seconds == 1 else f"{seconds:g} seconds"


_renderer: Renderer | None = None
_making = threading.Lock()


def renderer() -> Renderer:
    """The process's renderer, made from the settings when first asked for and closed when the
    process exits; a child whose process was killed ends by itself, when its stdin does."""
    global _renderer
    with _making:
        if _renderer is None:
            settings = get_settings()
            _renderer = Renderer(settings.render_processes, settings.render_memory_mb)
            atexit.register(_renderer.close)
        return _renderer


def draw_ahead(document_id: int, pages: list[int], storage: Storage) -> int:
    """Draw the pages of a PDF that its parse queued ahead (a `render_pages` job, queued by
    `_queue_drawing_ahead` in app/indexing/pipeline.py) and the storage does not keep yet, keep
    them where the document pane asks for them, and charge them to the organisation, with no
    member, as a stage's work is; returns how many were drawn.

    The pages are a cache filled in advance, so nothing here fails the job: a page that cannot
    be drawn is left for the pane, which tries it again when asked and says why it failed, and a
    storage or a renderer out of reach ends the job with what was drawn by then. Run again after
    a lost lease, it finds the pages kept and draws only the rest.
    """
    with SessionLocal() as session:
        document = session.get(Document, document_id)
        if document is None or document.status == "quarantined" or not scan_passed(document):
            return 0
        original = document.storage_key
        wanted = {
            page: document.page_image_key(page)
            for page in pages
            if 1 <= page <= (document.page_count or 0)
        }
    drawn = 0
    try:
        wanted = {page: key for page, key in wanted.items() if not storage.exists(key)}
        if not wanted:
            return 0
        with storage.localize(original) as path:
            for page, key in wanted.items():
                try:
                    image = renderer().render(path, page, purpose=AHEAD)
                except RenderError as error:
                    log.info(
                        "Page %s of document %s was not drawn ahead: %s", page, document_id, error
                    )
                    if error.transient:
                        break
                    continue
                storage.put(key, image, "image/png")
                drawn += 1
    except (StorageError, FileNotFoundError) as error:
        log.warning("The first pages of document %s were not drawn ahead: %s", document_id, error)
    finally:
        if drawn:
            _charge(document_id, drawn)
    return drawn


def _charge(document_id: int, pages: int) -> None:
    with SessionLocal() as session:
        # Under the row's lock, so that a document deleted meanwhile is not charged for: its
        # project and organisation may have gone with it.
        document = session.get(Document, document_id, with_for_update=True)
        if document is None:
            return
        record(
            session,
            org_id=document.project.org_id,
            project_id=document.project_id,
            user_id=None,
            kind=RENDERED,
            measures={"pages": pages},
            ref=(DOCUMENT, document_id),
        )
        session.commit()
