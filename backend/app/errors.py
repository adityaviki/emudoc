"""The base of the errors the app raises on purpose: a message the user can act on, whether the
cause is expected to pass on its own, and what a deployment's members are told when the message
is the operator's; and how any error is told in the log.

The log must never hold what a document says, a question, an answer or a key, and an
exception's message is where those slip in: a validation error quotes the value it refused, a
row the database refused comes back in full in its DETAIL, a malformed model reply is carried
in its error, a provider's error may echo the request. So the log tells an exception by where
it was raised and what kind it was (`described`, `summary`), and shows its message only when
the app wrote it (an `AppError`) or it is about a connection or the operating system, which
quote no data. A database error keeps its SQLSTATE and the names of the constraint, table and
column, which say what went wrong without the values. A provider's error body is told by the
identifiers in it (`provider_codes`), never by its words.

The parse child (app/parsing/limits.py) uses `described` too, so this module imports nothing
of the app.
"""

import re
import traceback
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
WITHHELD = "<message withheld>"
# Exceptions of these names report the database's own operation (a dropped connection, a
# timeout, an exhausted pool) rather than the data, whichever library raised them.
_OPERATIONAL = frozenset({"OperationalError", "TimeoutError"})
# What a provider's error code looks like: a word, never a sentence.
_CODE = re.compile(r"[A-Za-z][A-Za-z0-9_.\-]{0,63}")


class AppError(Exception):
    """An error whose message is a complete sentence a user or operator can act on.

    `transient` says the cause is one that passes by itself (a provider that is down or rate
    limiting, a network that dropped the request), so that work which hit it is worth running
    again in a while. It is set where the error is made, from what the provider answered, never
    guessed from the message. Everything else needs a person to change something first.

    `members` is what a deployment's members are told in its place when the message is the
    operator's: one that names a model, an address or a setting, or says what to change in the
    deployment. It is set where the error is made, as `transient` is, or by a class whose every
    message is the operator's; None leaves the message for everyone. Which of the two a member
    reads is decided where it is written for them (`shown`).

    Its message is shown in the log, so it must never quote a document, a question or a key.
    """

    members: str | None = None

    def __init__(self, message: str, *, transient: bool = False, members: str | None = None):
        super().__init__(message)
        self.transient = transient
        if members is not None:
            self.members = members


def shown(error: BaseException, *, development: bool) -> str:
    """What a member is shown of the error: in development its message, since whoever uses the
    app there runs it too, and in a deployment the members' sentence where the message is the
    operator's (`AppError.members`), which the operator reads in the log and the alerts."""
    if not development and isinstance(error, AppError) and error.members is not None:
        return error.members
    return str(error)


def described(error: BaseException) -> str:
    """The exception and its causes as a traceback for the log, each message replaced by what
    may be shown of it (`_message`)."""
    parts: list[str] = []
    for index, link in enumerate(_chain(error)):
        if index:
            parts.append(
                "\nThe above exception was the direct cause of the following exception:\n\n"
                if link.__cause__ is not None
                else "\nDuring handling of the above exception, another exception occurred:\n\n"
            )
        parts.append("Traceback (most recent call last):\n")
        parts.extend(traceback.format_list(traceback.extract_tb(link.__traceback__)))
        parts.append(f"{_kind(link)}: {_message(link)}\n")
    return "".join(parts).rstrip("\n")


def summary(error: BaseException) -> str:
    """The exception in one line, for a job's `last_error` and the operator's digest: its kind
    and message when the message may be shown, its kind and the line that raised it otherwise."""
    message = _message(error)
    if message != WITHHELD:
        return f"{_kind(error)}: {message}"
    frames = traceback.extract_tb(error.__traceback__)
    if not frames:
        return f"{_kind(error)} ({WITHHELD})"
    last = frames[-1]
    return f"{_kind(error)} at {_short_path(last.filename)}:{last.lineno} in {last.name}"


def shown_messages(error: BaseException) -> list[tuple[str, str]]:
    """The exception and its causes, the first one first, each as its class's name and what
    the log may show of its message: what an error tracker may be told of them
    (app/tracking.py)."""
    return [(type(link).__qualname__, _message(link)) for link in _chain(error)]


def provider_codes(body: object) -> list[str]:
    """The identifiers in a provider's error body: its type, code and status, and the reasons
    of Google's details, such as ["INVALID_ARGUMENT", "API_KEY_INVALID"]. Its words are left
    out: some providers echo the request, or a part of the key, in them."""
    if not isinstance(body, dict):
        return []
    error = body.get("error", body)
    if not isinstance(error, dict):
        return []
    found = [error.get(field) for field in ("type", "code", "status")]
    details = error.get("details")
    if isinstance(details, list):
        found += [detail.get("reason") for detail in details if isinstance(detail, dict)]
    codes: list[str] = []
    for code in found:
        if isinstance(code, str) and _CODE.fullmatch(code) and code not in codes:
            codes.append(code)
    return codes


def _chain(error: BaseException) -> list[BaseException]:
    """The exception and the ones it was raised from or while handling, the first one first."""
    chain: list[BaseException] = []
    current: BaseException | None = error
    while current is not None and all(current is not seen for seen in chain):
        chain.append(current)
        current = current.__cause__ or (
            None if current.__suppress_context__ else current.__context__
        )
    return chain[::-1]


def _kind(error: BaseException) -> str:
    kind = type(error)
    return (
        kind.__qualname__
        if kind.__module__ == "builtins"
        else f"{kind.__module__}.{kind.__qualname__}"
    )


def _message(error: BaseException) -> str:
    """What the log may show of the exception's message."""
    if isinstance(error, AppError | OSError) or _OPERATIONAL & {
        cls.__name__ for cls in type(error).__mro__
    }:
        return str(error)
    if state := _sqlstate(error):
        return state
    return WITHHELD


def _sqlstate(error: BaseException) -> str | None:
    """A database error's SQLSTATE and the names it was raised on, as psycopg reports them,
    whether the error is psycopg's or SQLAlchemy's around it."""
    error = getattr(error, "orig", None) or error
    code = getattr(error, "sqlstate", None)
    if not code:
        return None
    diagnostics = getattr(error, "diag", None)
    names = [
        f"{field} {value}"
        for field in ("constraint", "table", "column")
        if (value := getattr(diagnostics, f"{field}_name", None))
    ]
    return ", ".join([f"SQLSTATE {code}", *names])


def _short_path(filename: str) -> str:
    """A frame's file as the operator finds it: within the backend, or within its library."""
    path = Path(filename)
    if path.is_relative_to(BACKEND_DIR):
        return str(path.relative_to(BACKEND_DIR))
    _, _, library = filename.rpartition("site-packages/")
    return library or filename
