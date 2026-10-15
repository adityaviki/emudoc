"""Stopping the API: an answer's event stream ends once the server is told to stop, rather
than hold the stop up until uvicorn's timeout cuts it, and its follower picks the answer up
again from the next server (app/shutdown.py)."""

import os
import signal
import socket
import subprocess
import sys
import threading
import time

import httpx
from sqlalchemy import update

from app.config import BACKEND_DIR
from app.db import SessionLocal
from app.models import ChatEvent, ChatMessage
from app.shutdown import Stop, heard_stop
from tests.test_chats import ask, follow, new_chat, sse_of
from tests.test_jobs import read_until


def streaming(client, session) -> tuple[int, int]:
    """A chat and its answer, still being written, with one event so far."""
    project = client.post("/api/projects", json={"name": "Contracts"}).json()
    chat = new_chat(client, project["id"])
    message_id = ask(client, chat["id"], "Notice period?")["answer"]["id"]
    session.add(ChatEvent(message_id=message_id, seq=0, event={"type": "thinking"}))
    session.commit()
    return chat["id"], message_id


def settle_by_hand(message_id: int) -> None:
    with SessionLocal() as session:
        session.execute(
            update(ChatMessage).where(ChatMessage.id == message_id).values(status="failed")
        )
        session.commit()


def test_a_stopping_server_ends_the_stream_without_an_end_event(client, session):
    chat_id, message_id = streaming(client, session)

    # Told to stop, the server sends what was written and ends the stream without an end
    # event, which the client takes as a dropped connection, to follow again past the last
    # event it saw (test_a_follower_resumes_from_the_last_event_it_saw in test_chats.py).
    client.app.state.stop.requested = True
    # Settled from elsewhere after a while, so that a server that does not end the stream
    # fails this test rather than hang it.
    backstop = threading.Timer(5, settle_by_hand, (message_id,))
    backstop.start()
    try:
        assert follow(client, chat_id, message_id) == [{"type": "thinking"}]
    finally:
        backstop.cancel()

    # A settled answer still gets its end event: there is nothing left to follow again for.
    assert client.post(f"/api/chats/{chat_id}/messages/{message_id}/stop").status_code == 200
    assert follow(client, chat_id, message_id, after=0) == [{"type": "end", "status": "stopped"}]


def test_a_stop_signal_is_noted_and_passed_on_while_the_app_runs():
    passed_on = []

    def before(signum, frame):
        passed_on.append(signum)

    original = signal.signal(signal.SIGTERM, before)
    try:
        stop = Stop()
        with heard_stop(stop):
            assert not stop.requested
            signal.raise_signal(signal.SIGTERM)
            assert stop.requested and passed_on == [signal.SIGTERM]
        # The handler that was there before the app is there again after it.
        assert signal.getsignal(signal.SIGTERM) is before
    finally:
        signal.signal(signal.SIGTERM, original)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_the_api_stops_at_once_with_an_answer_streaming(client, session):
    # uvicorn as the image runs it, with an answer being followed: SIGTERM ends the stream
    # cleanly, without an end event, and the server stops in a moment rather than when its
    # timeout cuts the stream and logs it as an error.
    chat_id, message_id = streaming(client, session)
    port = free_port()
    process = subprocess.Popen(
        [
            *(sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)),
            *("--timeout-graceful-shutdown", "10"),
        ],
        cwd=BACKEND_DIR,
        env=dict(os.environ),
        stderr=subprocess.PIPE,
    )
    try:
        output = read_until(process.stderr, "Application startup complete", timeout=60)
        url = f"http://127.0.0.1:{port}/api/chats/{chat_id}/messages/{message_id}/events"
        with httpx.stream("GET", url, timeout=30) as response:
            assert response.status_code == 200
            lines = response.iter_lines()
            # The first event arrived, so the stream is open.
            assert next(lines) == "id: 0"
            process.send_signal(signal.SIGTERM)
            signalled = time.monotonic()
            # A stream cut mid-body would raise here (RemoteProtocolError).
            rest = list(lines)
        _, errors = process.communicate(timeout=30)
        took = time.monotonic() - signalled
        output += errors.decode()
    finally:
        process.kill()
    assert sse_of("\n".join(["id: 0", *rest])) == [(0, {"type": "thinking"})]
    assert took < 5, output
    assert "Shutting down" in output
    assert "ERROR" not in output and "Traceback" not in output, output
    # uvicorn ends with the signal it was stopped by, once it has stopped.
    assert process.returncode == -signal.SIGTERM
