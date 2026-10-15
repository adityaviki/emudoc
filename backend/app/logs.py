"""How the app's processes log, and what the log never holds.

Whoever runs the deployment reads the log, and whatever collects it keeps it, so it must never
hold what a document says, a question, an answer or a key. The app's own lines name ids, counts
and its own sentences; what could still slip in comes from outside them, and is stopped here:

- An exception's traceback keeps its frames and the kinds of its exceptions, but not their
  messages, unless the app wrote them (`described` in app/errors.py). `WithheldMessages` puts
  that version in place of the usual one on every handler, uvicorn's included, before any
  formatter runs.
- The access log names each request by its path and the names of its query's parameters, not
  their values (`QueryValuesHidden`): a search carries its words there (`?q=`), the provider's
  sign-in its one-time code. The page links that carry a token keep it in the fragment, which
  never reaches a server (`_link` in app/api/auth.py).
- A provider's error is told by its status and codes (`describe_error` in app/llm.py, the
  embedder), the database is never asked to repeat a statement's values in an error
  (`hide_parameters` in app/db.py), and the parse child prints its traceback the same way.

uvicorn sets up its loggers before it imports the app, so `configure_logging`, called when
app/main.py is imported, finds its handlers there; the worker and the maintenance commands
call it as they start. The structured logs of 1.7 replace the format, not these filters.
"""

import logging
from urllib.parse import parse_qsl

from app.errors import described

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
# The loggers that uvicorn gives handlers of their own, which the root's filter never sees.
UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


class WithheldMessages(logging.Filter):
    """Render a record's exception without the messages that may quote data, before any
    formatter renders it with them. A formatter keeps the first rendering in `exc_text`, so
    this one is what every handler prints."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.exc_info and record.exc_info[1] is not None:
            record.exc_text = described(record.exc_info[1])
        return True


class QueryValuesHidden(logging.Filter):
    """Keep the values of a request's query out of uvicorn's access log: "GET
    /api/projects/1/search?q=…&limit=… 200"."""

    def filter(self, record: logging.LogRecord) -> bool:
        # uvicorn logs the client, the method, the path with its query, the HTTP version and
        # the status, in that order.
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            record.args = (*args[:2], without_query_values(args[2]), *args[3:])
        return True


def without_query_values(target: str) -> str:
    """The path with only the names of its query's parameters."""
    path, mark, query = target.partition("?")
    if not mark:
        return target
    names = [name for name, _ in parse_qsl(query, keep_blank_values=True)]
    return f"{path}?{'&'.join(f'{name}=…' for name in names)}" if names else path


def configure_logging(format: str = FORMAT) -> None:
    """Log at INFO in `format`, and put the filters on every handler there is so far."""
    logging.basicConfig(level=logging.INFO, format=format)
    handlers = list(logging.getLogger().handlers)
    for name in UVICORN_LOGGERS:
        handlers += logging.getLogger(name).handlers
    for handler in handlers:
        if not any(isinstance(found, WithheldMessages) for found in handler.filters):
            handler.addFilter(WithheldMessages())
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(found, QueryValuesHidden) for found in access.filters):
        access.addFilter(QueryValuesHidden())
