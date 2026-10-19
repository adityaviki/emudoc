"""The child process that draws a PDF's pages as PNG images, for the renderer's pool
(app/rendering.py).

pdfium is not thread-safe, not even with a different document in each thread, so a process
draws one page at a time; the pages are drawn side by side by running several of these. Each
runs on files that anyone with an account can upload, so it keeps within limits the parent
could not set for a thread: its address space is capped from the start (`--memory-mb`), each
page may use the CPU seconds its request names and no more (the parent's clock is the one that
normally stops a slow page, this is for a child whose parent is gone), and no page is drawn
larger than its request's `edge` in pixels, whatever size the PDF gives it: a page as large as
the format allows, 200 inches a side, would otherwise ask for gigabytes at the scale the
document pane draws at.

It is this module run as a command, `python -m app.parsing.render`, with the backend directory
as its working directory, so that it imports `app.parsing` and nothing else of the app, as the
parse child does (app/parsing/limits.py). Unlike that child it serves page after page: the
parent writes one request a line on stdin, as JSON (`path`, `page`, `scale`, `edge`,
`seconds`), and reads a reply on stdout, a line of JSON and then the bytes it announces:
`{"png": n}` and the image, `{"error": sentence}` for a page that cannot be drawn, or
`{"crashed": name, "frames": …}` for an error nobody expected, after which the parent replaces
this process. It ends when stdin ends, which it does when the parent closes it or is gone, and
with OUT_OF_MEMORY when a page needs more than the cap. The file's path comes in a request, never
on the command line, which every user of the machine can read.
"""

import argparse
import io
import json
import math
import os
import resource
import sys
import warnings
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image  # noqa: F401  (loaded before the cap applies, as the parse child does)

from app.errors import described
from app.parsing.limits import CPU_GRACE, OUT_OF_MEMORY, _cap, _name, _out_of_memory

# What a page that pdfium cannot open or draw is told by: a damaged file, mostly, since one
# that is locked or has no pages is refused at the upload's parse and has no pages to ask for.
UNDRAWABLE = (
    "This page could not be drawn: the PDF is damaged in a way the viewer cannot read past. Its "
    "text is still searchable; to see the page, open the original file."
)


class NoSuchPage(Exception):
    """The request names a page the PDF does not have."""


def draw(path: Path, page_number: int, scale: float | None, edge: int) -> bytes:
    """Page `page_number` (1-based) as a PNG, at `scale` pixels per point, smaller where that
    would make its longer side more than `edge` pixels; without `scale`, exactly that long."""
    pdf = pdfium.PdfDocument(str(path))
    try:
        if not 1 <= page_number <= len(pdf):
            raise NoSuchPage(f"This PDF has no page {page_number}.")
        page = pdf[page_number - 1]
        try:
            fit = edge / max(*page.get_size(), 1.0)
            bitmap = page.render(scale=min(scale, fit) if scale else fit)
            try:
                buffer = io.BytesIO()
                # Not `optimize`, which takes three to four times as long for a file a few
                # percent smaller.
                bitmap.to_pil().save(buffer, format="PNG")
            finally:
                bitmap.close()
        finally:
            page.close()
    finally:
        pdf.close()
    return buffer.getvalue()


def main(argv: list[str] | None = None) -> int:
    """Draw the pages the parent asks for, until it closes stdin; see the module docstring."""
    parser = argparse.ArgumentParser(
        prog="python -m app.parsing.render",
        description="Draw PDF pages as PNG images, one request a line on stdin, within a cap on "
        "memory; the renderer (app/rendering.py) runs a few of these as children, so that a "
        "page it cannot draw takes down only this process.",
    )
    parser.add_argument("--memory-mb", type=int, default=0, help="address space; 0 is unlimited")
    args = parser.parse_args(argv)
    # Nobody reads this process's stderr (the parent passes it nothing to write to), so a
    # library's warnings would go nowhere anyway; a reply carries what the parent should log.
    warnings.simplefilter("ignore")
    if args.memory_mb:
        _cap(resource.RLIMIT_AS, args.memory_mb << 20)
    out = sys.stdout.buffer
    for line in sys.stdin.buffer:
        request = json.loads(line)
        _allow_cpu(request["seconds"])
        body = b""
        try:
            body = draw(Path(request["path"]), request["page"], request["scale"], request["edge"])
            reply: dict = {"png": len(body)}
        except MemoryError:
            # Making a reply would take memory this process no longer gets.
            os._exit(OUT_OF_MEMORY)
        except pdfium.PdfiumError:
            reply = {"error": UNDRAWABLE}
        except NoSuchPage as error:
            reply = {"error": str(error)}
        except Exception as error:
            if _out_of_memory(error):
                os._exit(OUT_OF_MEMORY)
            # The frames and kinds without the messages, which may quote the file.
            reply = {"crashed": _name(error), "frames": described(error)}
        try:
            out.write(json.dumps(reply).encode() + b"\n" + body)
            out.flush()
        except MemoryError:
            os._exit(OUT_OF_MEMORY)
        except BrokenPipeError:
            # The parent is gone, or stopped waiting and will replace this process.
            return 0
    return 0


def _allow_cpu(seconds: float) -> None:
    """Let the next page use `seconds` of CPU time, and the grace, on top of what this process
    has used so far; past it the kernel stops the process (SIGXCPU). 0 is no limit."""
    _, hard = resource.getrlimit(resource.RLIMIT_CPU)
    if not seconds:
        resource.setrlimit(resource.RLIMIT_CPU, (hard, hard))
        return
    usage = resource.getrusage(resource.RUSAGE_SELF)
    soft = math.ceil(usage.ru_utime + usage.ru_stime + seconds) + CPU_GRACE
    if hard != resource.RLIM_INFINITY:
        soft = min(soft, hard)
    resource.setrlimit(resource.RLIMIT_CPU, (soft, hard))


if __name__ == "__main__":
    sys.exit(main())
