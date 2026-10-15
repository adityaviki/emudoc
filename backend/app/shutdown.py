"""How the API hears that it is being stopped, for the responses that would hold the stop up.

uvicorn stops on SIGTERM or SIGINT: it closes its listening socket, then waits for every
response in progress to end before it stops the app, for at most --timeout-graceful-shutdown
seconds, after which it cuts them and logs an error. An answer's event stream ends only when
its answer does, which can be minutes away, so it would make every stop wait the whole timeout
and end with the follower's connection broken mid-body. The follower loses nothing by an
earlier end: the answer is the worker's to write, and the follower reconnects with
Last-Event-ID, to another replica or to this one started again. So the stream ends once the
server was told to stop (`_follow` in app/api/chats.py), and the timeout is left to the
requests that do end by themselves, such as an upload.

uvicorn tells the app nothing of a stop until the open responses have ended. It installs its
handlers with signal.signal and keeps the ones before them to put back, so `heard_stop` puts a
handler in front of each of its own that notes the stop and calls uvicorn's, and puts uvicorn's
back when the app stops.
"""

import signal
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from types import FrameType

STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM)


@dataclass
class Stop:
    """Whether the server was told to stop. A plain flag rather than an event with a lock: a
    signal handler runs between any two steps of the main thread, which may hold that lock."""

    requested: bool = False


@contextmanager
def heard_stop(stop: Stop) -> Iterator[None]:
    """Set `stop.requested` when the process is sent a stop signal, then pass the signal on to
    the handler that was there, for as long as the block runs.

    Only from the main thread, the one Python handles signals in, and only in front of a
    handler written in Python: a server that runs the app in another thread, or handles its
    signals some other way, stops as it would have, with the streams cut at its timeout.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = {
        signum: handler for signum in STOP_SIGNALS if callable(handler := signal.getsignal(signum))
    }

    def heard(signum: int, frame: FrameType | None) -> None:
        stop.requested = True
        previous[signum](signum, frame)

    for signum in previous:
        signal.signal(signum, heard)
    try:
        yield
    finally:
        for signum in previous:
            # Unless something put a handler of its own in front since, which stays.
            if signal.getsignal(signum) is heard:
                signal.signal(signum, previous[signum])
