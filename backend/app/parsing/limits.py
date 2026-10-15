"""The limits on parsing an upload, kept by running the parser in a child process.

The parsers (pdfplumber, python-docx, openpyxl) run on files that anyone with an account can
upload, and a crafted or merely enormous file can keep one busy for hours or drive it out of
memory. A worker thread cannot be stopped, so `parse_within_limits` runs the parser in a process
of its own and stops that: the parent kills it when it runs past the time limit, the child caps
its own address space and CPU time with `resource` before it parses (the CPU limit stands even
when the worker that waits for it is gone), and the parsers refuse a PDF with too many pages or
a sheet with too many rows before they take it on. The child gets a scrubbed environment, so a
parser that is made to read it finds no key in it. It is a bound on time and memory, not a
security sandbox: the child runs as the same user with the same files and network.

The child is this module run as a command, `python -m app.parsing.limits`, started with the
backend directory as its working directory so that it imports `app.parsing` and nothing else of
the app: not the settings, not the database. Its reply is JSON on stdout (never a pickle, which a
subverted child could use against the parent) and its exit code says how it ended; the parent
turns each ending into a `ParseError` whose message names the limit and what to do. OCR stays in
the parent: the child reports the pages without a text layer and the parent sends them to the
model, which is not picklable and is faked in tests.
"""

import argparse
import importlib
import json
import logging
import os
import resource
import signal
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from app.errors import AppError, described
from app.parsing import (
    PARSER_MODULES,
    FileTooLarge,
    ParsedBlock,
    ParsedDocument,
    UnreadableFile,
    UnsupportedFile,
    parse_file,
)

log = logging.getLogger(__name__)

# Where `python -m app.parsing.limits` finds the `app` package: this file is backend/app/parsing/.
BACKEND_DIR = Path(__file__).resolve().parents[2]
# The variables the child keeps. The model keys and the database URL are in the parent's
# environment too (copied from .env in development), and no parser needs them.
KEPT_VARIABLES = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR")
# How the child ended, beyond 0 for a parsed file and a signal for a killed one.
REFUSED = 2  # the file is unsupported, over a cap or unreadable; stdout says why
OUT_OF_MEMORY = 3
CRASHED = 4  # the parser raised; stdout names the exception, stderr its traceback sans messages
# The grace the CPU limit leaves over the time limit, so the parent's clock is the one that
# normally stops a child and the CPU limit is only for a child whose parent is gone.
CPU_GRACE = 5


class ParseError(AppError):
    """Parsing stopped or was refused; the message says which limit and what to do."""


@dataclass(frozen=True)
class ParseLimits:
    """What a parse may use. Zero removes a limit."""

    timeout: float = 300.0  # seconds, by the parent's clock
    memory_mb: int = 2048  # the child's address space
    max_pages: int = 500  # of a PDF
    max_rows: int = 100_000  # of one sheet


def parse_within_limits(path: Path, filename: str, limits: ParseLimits) -> ParsedDocument:
    """Parse the file in a child process that stays within `limits`.

    Raises `UnsupportedFile` for an extension nothing parses and `ParseError` when the child was
    stopped, refused the file or failed; the parse itself is `parse_file`.
    """
    suffix = Path(filename).suffix.lower()
    if suffix not in PARSER_MODULES:
        # An image needs no parser (the parent sends it to OCR), and anything else is refused;
        # neither is worth a process.
        return parse_file(path, filename)
    command = [
        sys.executable,
        "-m",
        "app.parsing.limits",
        str(path.absolute()),
        filename,
        f"--memory-mb={limits.memory_mb}",
        f"--cpu-seconds={int(limits.timeout) + CPU_GRACE if limits.timeout else 0}",
        f"--max-pages={limits.max_pages}",
        f"--max-rows={limits.max_rows}",
    ]
    try:
        child = subprocess.Popen(
            command,
            cwd=BACKEND_DIR,
            env={name: os.environ[name] for name in KEPT_VARIABLES if name in os.environ},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as error:
        # No process could be started: the machine is out of processes or memory for now.
        raise ParseError(
            "The worker could not start a process to parse the file.", transient=True
        ) from error
    try:
        try:
            out, err = child.communicate(timeout=limits.timeout or None)
        except subprocess.TimeoutExpired:
            child.kill()
            out, err = child.communicate()
            _log_failure(filename, child.returncode, err)
            raise ParseError(
                f"Parsing did not finish within {_duration(limits.timeout)} and was stopped. "
                "Split the file into smaller parts and upload those."
            ) from None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
    return _outcome(filename, child.returncode, out, err, limits)


def _outcome(
    filename: str, code: int, out: bytes, err: bytes, limits: ParseLimits
) -> ParsedDocument:
    if code == 0:
        try:
            return _decode(json.loads(out))
        except (ValueError, KeyError, TypeError) as error:
            _log_failure(filename, code, err)
            raise ParseError(
                "The parser's reply could not be read. Ask support about this file."
            ) from error
    if code == REFUSED:
        raise ParseError(_reported(out, "the file was refused"))
    _log_failure(filename, code, err)
    if code == CRASHED:
        # A damaged file, mostly; a C library that hits the memory cap can end here too, with
        # its own error rather than a MemoryError, which the log shows.
        raise ParseError(
            f"The file could not be parsed ({_reported(out, 'an error')}). Check that it opens "
            "in another program, then upload it again."
        )
    if code == OUT_OF_MEMORY:
        raise ParseError(
            f"Parsing needs more than {_memory(limits)} and was stopped. Split the file into "
            "smaller parts and upload those."
        )
    if code == -signal.SIGXCPU:
        # The child's own CPU limit stopped it, which only happens after the parent's timeout
        # would have, so the parent was late or gone.
        raise ParseError(
            f"Parsing did not finish within {_duration(limits.timeout)} and was stopped. "
            "Split the file into smaller parts and upload those."
        )
    if code < 0:
        raise ParseError(
            f"Parsing stopped unexpectedly ({signal.Signals(-code).name}), which usually means "
            f"the file needs more than {_memory(limits)}. Try a smaller or simpler file, or ask "
            "support about this one."
        )
    raise ParseError(
        f"Parsing stopped unexpectedly (exit code {code}). If the file opens in another "
        "program, ask support about it."
    )


def _reported(out: bytes, fallback: str) -> str:
    """What the child put in its reply's `error`, or `fallback` when the reply is not one."""
    try:
        return str(json.loads(out)["error"])
    except (ValueError, KeyError, TypeError):
        return fallback


def _decode(data: dict) -> ParsedDocument:
    return ParsedDocument(
        blocks=[
            ParsedBlock(
                block["kind"],
                block["text"],
                level=block["level"],
                page=block["page"],
                bbox=tuple(block["bbox"]) if block["bbox"] else None,
            )
            for block in data["blocks"]
        ],
        page_count=data["page_count"],
        page_sizes=[(width, height) for width, height in data["page_sizes"]],
        title=data["title"],
        ocr_pages=list(data["ocr_pages"]),
    )


def _log_failure(filename: str, code: int | None, err: bytes) -> None:
    log.warning(
        "Parsing %s in a child process ended with code %s; its stderr: %s",
        filename,
        code,
        err.decode(errors="replace")[-2000:].strip() or "(empty)",
    )


def _duration(seconds: float) -> str:
    if seconds >= 60 and seconds % 60 == 0:
        minutes = int(seconds // 60)
        return "1 minute" if minutes == 1 else f"{minutes} minutes"
    return f"{seconds:g} seconds"


def _memory(limits: ParseLimits) -> str:
    if limits.memory_mb:
        return f"the {limits.memory_mb:,} MB of memory allowed"
    return "the memory available"


# The child.


def main(argv: list[str] | None = None) -> int:
    """Parse one file within the given limits and reply on stdout; see the module docstring."""
    parser = argparse.ArgumentParser(
        prog="python -m app.parsing.limits",
        description="Parse a file in this process, within limits on memory and CPU time; the "
        "parse stage runs this as a child so that a file it cannot handle takes down only this "
        "process. It prints the parsed document as JSON.",
    )
    parser.add_argument("path", type=Path)
    parser.add_argument("filename", help="the uploaded name, whose extension picks the parser")
    parser.add_argument("--memory-mb", type=int, default=0, help="address space; 0 is unlimited")
    parser.add_argument("--cpu-seconds", type=int, default=0, help="0 is unlimited")
    parser.add_argument("--max-pages", type=int, default=0, help="of a PDF; 0 is any number")
    parser.add_argument("--max-rows", type=int, default=0, help="of a sheet; 0 is any number")
    args = parser.parse_args(argv)

    # The parser's libraries are loaded before the limit applies, so the limit is what parsing
    # the file may use, and hitting it is a MemoryError in the parse rather than a failed import.
    if module := PARSER_MODULES.get(Path(args.filename).suffix.lower()):
        importlib.import_module(module)
    if args.memory_mb:
        _cap(resource.RLIMIT_AS, args.memory_mb << 20)
    if args.cpu_seconds:
        _cap(resource.RLIMIT_CPU, args.cpu_seconds)

    try:
        parsed = parse_file(
            args.path, args.filename, max_pages=args.max_pages, max_rows=args.max_rows
        )
        payload, code = asdict(parsed), 0
    except MemoryError:
        # Making a reply would take memory this process no longer gets.
        os._exit(OUT_OF_MEMORY)
    except (UnsupportedFile, FileTooLarge, UnreadableFile) as error:
        payload, code = {"error": str(error)}, REFUSED
    except Exception as error:
        # The frames and kinds without the messages, which may quote the file (a cell, a
        # stream of a PDF): the parent logs what this prints (app/errors.py).
        print(described(error), file=sys.stderr)
        payload, code = {"error": _name(error)}, CRASHED
    try:
        sys.stdout.buffer.write(json.dumps(payload).encode())
        sys.stdout.buffer.flush()
    except MemoryError:
        os._exit(OUT_OF_MEMORY)
    return code


def _name(error: Exception) -> str:
    """The exception's name with its library's, as "pdfplumber.PdfminerException": the library
    tells support where to look, and some names say nothing alone ("zlib.error")."""
    kind = type(error)
    library = kind.__module__.split(".")[0]
    return kind.__qualname__ if library == "builtins" else f"{library}.{kind.__qualname__}"


def _cap(kind: int, value: int) -> None:
    """Set a hard limit for this process, within the hard limit it already has."""
    _, hard = resource.getrlimit(kind)
    if hard != resource.RLIM_INFINITY:
        value = min(value, hard)
    resource.setrlimit(kind, (value, value))


if __name__ == "__main__":
    sys.exit(main())
