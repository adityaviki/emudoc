"""The upload client (app/client/emudoc_upload.py) run as a member runs it: `send` with folders
and files and `watch` over looks at a folder, each against the suite's app through the client's
transport (`through`), and once over HTTP, uvicorn serving the app to the client downloaded from
it and run by a Python of its own (UPLOAD_CLIENT_PYTHON, the oldest it promises in CI); and the
copy members downloaded before it moved onto the versioned API, over HTTP likewise."""

import ast
import hashlib
import io
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.api.deps import get_account
from app.apikeys import KEY_UNKNOWN, READ_ONLY_KEY
from app.client import UPLOAD_CLIENT
from app.client import emudoc_upload as uploader
from app.config import get_settings
from app.indexing.pipeline import process_document
from app.models import Document, IdempotencyKey
from app.parsing import SUPPORTED_EXTENSIONS
from app.parsing.signatures import RENAME
from tests.test_api import create_project, listed
from tests.test_apikeys import make_key
from tests.test_entitlements import ONE_DOCUMENT, member, plan  # noqa: F401 (fixtures)
from tests.test_folders import tree
from tests.test_jobs import read_until
from tests.test_ratelimit import minute_turned

SERVER = "https://emudoc.example.org"
# The Python the client runs by over HTTP: the suite's own, or in CI the oldest it promises.
PYTHON = os.environ.get("UPLOAD_CLIENT_PYTHON") or sys.executable
# The client as members downloaded it before it moved onto /api/v1, calling the page's routes.
BEFORE_V1 = Path(__file__).with_name("upload_client_before_v1.py")
# The repository, whose proxy configurations the client's uploads must pass.
REPOSITORY = Path(__file__).resolve().parents[2]


def through(client, calls: list | None = None) -> uploader.Transport:
    """The suite's app as the client reaches it: each request as the client makes it, through a
    test client that adds no header or cookie of its own; `calls` keeps every request."""
    app = TestClient(client.app)

    def send(request: uploader.Request) -> uploader.Response:
        if calls is not None:
            calls.append(request)
        assert request.url.startswith(SERVER), request.url
        answer = app.request(
            request.method,
            request.url.removeprefix(SERVER),
            headers=request.headers,
            content=request.body,
        )
        headers = {name.lower(): value for name, value in answer.headers.items()}
        return uploader.Response(answer.status_code, headers, answer.content)

    return send


@dataclass
class Run:
    code: int
    out: list[str]
    err: list[str]

    @property
    def said(self) -> list[str]:
        """What a watch said, without the time each line is stamped with."""
        return [line.split(" ", 1)[1] for line in self.out]


def run(
    client,
    *argv: str,
    key: str | None,
    sleep: Callable[[float], None] = lambda seconds: None,
    calls: list | None = None,
    transport: uploader.Transport | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> Run:
    out, err = io.StringIO(), io.StringIO()
    environ = {uploader.SERVER_VARIABLE: SERVER}
    if key is not None:
        environ[uploader.KEY_VARIABLE] = key
    code = uploader.main(
        list(argv),
        transport=transport or through(client, calls),
        environ=environ,
        sleep=sleep,
        clock=clock,
        out=out,
        err=err,
    )
    return Run(code, out.getvalue().splitlines(), err.getvalue().splitlines())


def write(folder: Path, files: dict[str, str | bytes]) -> Path:
    for name, body in files.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body if isinstance(body, bytes) else body.encode())
    return folder


def reading(session, services) -> Callable[[float], None]:
    """A wait in which the worker reads what is queued, as it would while the client waits."""

    def sleep(seconds: float) -> None:
        queued = session.scalars(select(Document.id).where(Document.status == "queued")).all()
        session.commit()
        for document_id in queued:
            process_document(document_id, services)

    return sleep


def posts(calls: list[uploader.Request]) -> list[uploader.Request]:
    return [call for call in calls if call.method == "POST"]


def files_in(request: uploader.Request) -> int:
    return request.body.count(b'name="files"')


def test_send_files_a_folder_as_it_is_and_says_once_its_files_are_read(
    client, session, services, tmp_path, sample_pdf
):
    project = create_project(client, "Invoices")
    key = make_key(client, scope="write")["key"]
    folder = write(
        tmp_path / "Supplier files",
        {
            "march.txt": "Invoice for March.",
            "2024/april.md": "# Invoice for April",
            "2024/Été.txt": "Facture d'été.",
            # What the folder holds hidden stays on the computer, unsaid.
            ".hidden/secret.txt": "Not for Emudoc.",
            ".DS_Store": b"\0\0\0\1",
            "Thumbs.db": b"\0",
        },
    )
    contract = Path(shutil.copy(sample_pdf, tmp_path / "contract.pdf"))
    calls: list[uploader.Request] = []
    done = run(
        client,
        "send",
        "--project",
        "Invoices",
        "--into",
        "Scans/2026",
        str(folder),
        str(contract),
        key=key,
        sleep=reading(session, services),
        calls=calls,
    )
    assert (done.code, done.err) == (0, []), done
    assert done.out == [
        "Sending 4 files into Invoices, in Scans/2026.",
        "Sent 4 files (1 upload).",
        "Reading: 0 of 4 ready.",
        "Read the 4 files sent.",
    ]
    # A folder is filed as it is, a file named on its own beside it, both where --into says.
    assert tree(client, project["id"]) == {
        "Scans": [],
        "Scans/2026": ["contract.pdf"],
        "Scans/2026/Supplier files": ["march.txt"],
        "Scans/2026/Supplier files/2024": ["april.md", "Été.txt"],
    }
    # Every request is the versioned API's and carries the key alone, and one request carried
    # every file, under an Idempotency-Key.
    assert all(call.url.startswith(f"{SERVER}/api/v1/") for call in calls)
    assert {call.headers["Authorization"] for call in calls} == {f"Bearer {key}"}
    assert all(call.headers.get("Idempotency-Key") for call in posts(calls))
    assert not any("Cookie" in call.headers or "X-Requested-With" in call.headers for call in calls)
    assert [files_in(call) for call in posts(calls)] == [4]
    # Nothing on the computer changed.
    assert (folder / ".hidden" / "secret.txt").exists() and contract.exists()


def test_what_emudoc_would_not_take_is_left_out_and_what_it_refuses_is_said(
    client, tmp_path, monkeypatch
):
    monkeypatch.setattr(get_settings(), "max_upload_mb", 1)
    create_project(client)
    key = make_key(client, scope="write")["key"]
    folder = write(
        tmp_path / "In",
        {
            "notes.odt": "Notes",
            "empty.txt": "",
            "large.txt": "x" * (1024 * 1024 + 1),
            "fake.pdf": "Not a PDF at all.",
            "kept.txt": "A delivery note.",
        },
    )
    calls: list[uploader.Request] = []
    done = run(
        client, "send", "--project", "Contracts", "--no-wait", str(folder), key=key, calls=calls
    )
    assert done.code == 1
    assert done.out == [
        "Sending 2 files into Contracts.",
        "Sent 1 file (1 upload).",
        "Left out 3 files:",
        "  In/empty.txt: Empty file.",
        "  In/large.txt: Larger than 1 MB.",
        f"  In/notes.odt: Unsupported type. Use {', '.join(sorted(SUPPORTED_EXTENSIONS))}.",
        "Refused 1 file:",
        f"  In/fake.pdf: Not a PDF file. {RENAME}",
    ]
    # What was left out was never sent.
    assert [files_in(call) for call in posts(calls)] == [2]


def test_a_file_is_sent_once_by_the_state_file_or_found_in_the_project_already(client, tmp_path):
    project = create_project(client)
    key = make_key(client, scope="write")["key"]
    folder = write(tmp_path / "Scans", {"a.txt": "The first scan.", "b.txt": "The second scan."})
    state = tmp_path / "sent.json"
    first = run(
        client,
        "send",
        "--project",
        "Contracts",
        "--no-wait",
        "--state",
        str(state),
        str(folder),
        key=key,
    )
    assert (first.code, first.out[-1]) == (0, "Sent 2 files (1 upload).")

    # Renamed, a file is still the bytes sent; a new one goes alone.
    (folder / "a.txt").rename(folder / "renamed.txt")
    write(folder, {"c.txt": "The third scan."})
    calls: list[uploader.Request] = []
    again = run(
        client,
        "send",
        "--project",
        "Contracts",
        "--no-wait",
        "--state",
        str(state),
        str(folder),
        key=key,
        calls=calls,
    )
    assert (again.code, again.out) == (
        0,
        [
            "Sending 3 files into Contracts.",
            "Sent 1 file (1 upload).",
            "2 files sent before, as the state file says.",
        ],
    )
    (posted,) = posts(calls)
    assert b"The third scan." in posted.body and files_in(posted) == 1
    kept = json.loads(state.read_text())
    assert (kept["server"], kept["project"]) == (SERVER, project["id"])
    assert sorted(entry["name"] for entry in kept["sent"].values()) == [
        "Scans/a.txt",
        "Scans/b.txt",
        "Scans/c.txt",
    ]

    # Without it Emudoc knows its files itself and says where each is, which is no failure.
    without = run(client, "send", "--project", "Contracts", "--no-wait", str(folder), key=key)
    assert (without.code, without.out) == (
        0,
        [
            "Sending 3 files into Contracts.",
            "3 files in the project already:",
            "  Scans/b.txt: Already in this project as 'b.txt' in Scans.",
            "  Scans/c.txt: Already in this project as 'c.txt' in Scans.",
            "  Scans/renamed.txt: Already in this project as 'a.txt' in Scans.",
        ],
    )

    # A state file is one project's.
    create_project(client, "Other")
    other = run(client, "send", "--project", "Other", "--state", str(state), str(folder), key=key)
    assert (other.code, other.err) == (
        2,
        [
            f"emudoc_upload: The state file {state} is project {project['id']}'s at {SERVER}: "
            "give --state another file for this project."
        ],
    )


def proxy(transport: uploader.Transport, most: int, calls: list) -> uploader.Transport:
    """A proxy before the app that takes a body of `most` bytes at most, as nginx's
    client_max_body_size would, refusing a larger one with a page of its own; `calls` keeps
    every request it was sent."""

    def send(request: uploader.Request) -> uploader.Response:
        calls.append(request)
        if request.body and len(request.body) > most:
            return uploader.Response(413, {"content-type": "text/html"}, b"<h1>Too large</h1>")
        return transport(request)

    return send


@pytest.mark.usefixtures("one_minute")
def test_files_go_in_as_many_requests_as_the_server_and_a_proxy_take(
    client, session, tmp_path, monkeypatch
):
    monkeypatch.setattr(get_settings(), "max_upload_files", 2)
    project = create_project(client)
    key = make_key(client, scope="write")["key"]
    folder = write(tmp_path / "Notes", {f"note{n}.txt": f"Note {n}." for n in range(5)})
    calls: list[uploader.Request] = []
    done = run(
        client, "send", "--project", "Contracts", "--no-wait", str(folder), key=key, calls=calls
    )
    assert (done.code, done.out[-1]) == (0, "Sent 5 files (3 uploads).")
    assert [files_in(call) for call in posts(calls)] == [2, 2, 1]

    # A proxy that takes less than the server says: the request is halved until it passes, and
    # a file too large for it alone is refused with its answer.
    monkeypatch.setattr(get_settings(), "max_upload_files", 100)
    folder = write(tmp_path / "Bigger", {f"part{n}.txt": f"{n}" * 600 for n in range(4)})
    write(folder, {"whole.txt": "w" * 3000})
    calls = []
    done = run(
        client,
        "send",
        "--project",
        "Contracts",
        "--no-wait",
        str(folder),
        key=key,
        transport=proxy(through(client), 2000, calls),
    )
    assert done.code == 1
    assert done.out[1:3] == ["Sent 4 files (3 uploads).", "Refused 1 file:"]
    assert done.out[3].startswith("  Bigger/whole.txt: The server answered 413 ")
    # Five, then two that pass and three, then one that passes and two, each alone.
    assert [files_in(call) for call in posts(calls)] == [5, 2, 3, 1, 2, 1, 1]
    assert len(listed(client, project["id"])) == 0
    assert len(tree(client, project["id"])["Bigger"]) == 4

    # Past the member's rate of uploads, the client waits as long as Emudoc asks.
    monkeypatch.setattr(get_settings(), "max_upload_files", 1)
    monkeypatch.setattr(get_settings(), "rate_uploads", 1)
    minute_turned(session)
    folder = write(tmp_path / "Rated", {"one.txt": "One.", "two.txt": "Two."})
    waits: list[float] = []

    def wait(seconds: float) -> None:
        waits.append(seconds)
        minute_turned(session)

    done = run(
        client, "send", "--project", "Contracts", "--no-wait", str(folder), key=key, sleep=wait
    )
    assert (done.code, done.out[-1]) == (0, "Sent 2 files (2 uploads).")
    assert len(waits) == 1 and 1 <= waits[0] <= 60
    assert done.out[1].endswith(f"Waiting {waits[0]:g} seconds, as Emudoc asks.")


def test_files_sent_again_after_a_lost_answer_are_taken_once_and_answered_as_before(
    client, session, services, tmp_path
):
    project = create_project(client)
    key = make_key(client, scope="write")["key"]
    folder = write(tmp_path / "Scans", {"a.txt": "The first scan.", "b.txt": "The second scan."})
    calls: list[uploader.Request] = []
    app = through(client, calls)
    answers: list[uploader.Response] = []
    kept: list = []

    def cut(request: uploader.Request) -> uploader.Response:
        answer = app(request)
        if request.method != "POST":
            return answer
        answers.append(answer)
        if len(answers) > 1:
            return answer
        # Emudoc took the files and the connection dropped before its answer came; and when the
        # client sends them again, the request is still being carried out.
        kept.extend(session.execute(select(IdempotencyKey.status_code, IdempotencyKey.body)).one())
        session.execute(update(IdempotencyKey).values(status_code=None))
        session.commit()
        raise uploader.Unavailable("Emudoc did not answer (Connection reset by peer).")

    read = reading(session, services)
    waits: list[float] = []

    def wait(seconds: float) -> None:
        waits.append(seconds)
        read(seconds)
        if len(answers) == 2:
            # Until it ends, its answer kept.
            status_code, body = kept
            session.execute(update(IdempotencyKey).values(status_code=status_code, body=body))
            session.commit()

    done = run(
        client, "send", "--project", "Contracts", str(folder), key=key, transport=cut, sleep=wait
    )
    assert (done.code, done.err) == (0, [])
    assert done.out == [
        "Sending 2 files into Contracts.",
        "Emudoc did not answer (Connection reset by peer). Trying again in 1 seconds.",
        "Emudoc is still taking these files from the request before. Waiting 5 seconds for its "
        "answer.",
        "Sent 2 files (1 upload).",
        "Read the 2 files sent.",
    ]
    # Each time the same request with the same key, the files kept once, and the documents of
    # the answer given again followed.
    assert len({call.headers["Idempotency-Key"] for call in posts(calls)}) == 1
    assert [answer.status for answer in answers] == [201, 409, 201]
    assert answers[2].headers["idempotent-replayed"] == "true"
    assert tree(client, project["id"]) == {"Scans": ["a.txt", "b.txt"]}


def test_a_run_cut_off_before_its_answer_leaves_its_key_to_the_next(
    client, session, services, tmp_path
):
    project = create_project(client)
    key = make_key(client, scope="write")["key"]
    folder = write(tmp_path / "Scans", {"a.txt": "The first scan.", "b.txt": "The second scan."})
    state = tmp_path / "sent.json"
    calls: list[uploader.Request] = []
    app = through(client, calls)

    def lost(request: uploader.Request) -> uploader.Response:
        answer = app(request)
        if request.method == "POST":
            raise uploader.Unavailable("Emudoc did not answer (Connection reset by peer).")
        return answer

    cut = run(
        client,
        "send",
        "--project",
        "Contracts",
        "--state",
        str(state),
        str(folder),
        key=key,
        transport=lost,
    )
    assert (cut.code, cut.err) == (
        2,
        [
            "emudoc_upload: Emudoc did not answer (Connection reset by peer). The files not "
            "listed above as sent were not."
        ],
    )
    (first,) = {call.headers["Idempotency-Key"] for call in posts(calls)}
    kept = json.loads(state.read_text())
    assert (kept["sent"], list(kept["unanswered"])) == ({}, [first])

    # The next run sends the same files with the key, and is given the answer Emudoc kept.
    calls.clear()
    again = run(
        client,
        "send",
        "--project",
        "Contracts",
        "--state",
        str(state),
        str(folder),
        key=key,
        calls=calls,
        sleep=reading(session, services),
    )
    assert (again.code, again.out) == (
        0,
        [
            "Sending 2 files into Contracts.",
            "Sent 2 files (1 upload).",
            "Reading: 0 of 2 ready.",
            "Read the 2 files sent.",
        ],
    )
    assert [call.headers["Idempotency-Key"] for call in posts(calls)] == [first]
    kept = json.loads(state.read_text())
    assert (len(kept["sent"]), kept["unanswered"]) == (2, {})
    assert tree(client, project["id"]) == {"Scans": ["a.txt", "b.txt"]}


def test_a_key_emudoc_holds_for_another_request_is_put_aside_for_a_new_one(client, tmp_path):
    project = create_project(client)
    key = make_key(client, scope="write")["key"]
    folder = write(tmp_path / "Scans", {"a.txt": "The first scan."})
    # The state file keeps a key for these files that Emudoc holds for another request, as after
    # a change to what Emudoc compares, and one older than Emudoc keeps an answer.
    taken = "4e1ac0de-0000-4000-8000-000000000001"
    program = TestClient(client.app, headers={"Authorization": f"Bearer {key}"})
    other = program.post(
        f"/api/v1/projects/{project['id']}/documents",
        files=[("files", ("other.txt", b"Another file."))],
        headers={"Idempotency-Key": taken},
    )
    assert other.status_code == 201
    now = datetime.now(UTC)
    state = tmp_path / "sent.json"
    state.write_text(
        json.dumps(
            {
                "server": SERVER,
                "project": project["id"],
                "sent": {},
                "unanswered": {
                    taken: {
                        "files": [
                            ["Scans", "a.txt", hashlib.sha256(b"The first scan.").hexdigest()]
                        ],
                        "at": now.isoformat(),
                    },
                    "forgotten": {"files": [], "at": (now - timedelta(days=2)).isoformat()},
                },
            }
        )
    )
    calls: list[uploader.Request] = []
    done = run(
        client,
        "send",
        "--project",
        "Contracts",
        "--no-wait",
        "--state",
        str(state),
        str(folder),
        key=key,
        calls=calls,
    )
    assert (done.code, done.out, done.err) == (
        0,
        ["Sending 1 file into Contracts.", "Sent 1 file (1 upload)."],
        [],
    )
    first, second = [call.headers["Idempotency-Key"] for call in posts(calls)]
    assert first == taken != second
    assert json.loads(state.read_text())["unanswered"] == {}
    assert [d["filename"] for d in listed(client, project["id"])] == ["other.txt"]
    assert tree(client, project["id"]) == {"Scans": ["a.txt"]}


def rules(text: str, pattern: str) -> list[str]:
    found = re.findall(pattern, text, re.MULTILINE)
    assert found, pattern
    return found


def test_the_proxies_take_a_large_body_on_the_routes_the_clients_upload_to(client, tmp_path):
    """deploying.md's Caddy and nginx and the AWS instance's Caddyfile let a large body through
    to the upload routes alone: the versioned API's, which the client sends to, and the page's,
    which the copies from before send to; every other request is held to a small one."""
    project = create_project(client)
    key = make_key(client, scope="write")["key"]
    calls: list[uploader.Request] = []
    folder = write(tmp_path / "In", {"a.txt": "A."})
    assert (
        run(
            client, "send", "--project", "Contracts", "--no-wait", str(folder), key=key, calls=calls
        ).code
        == 0
    )
    (posted,) = posts(calls)
    uploads = [posted.url.removeprefix(SERVER), f"/api/projects/{project['id']}/documents"]
    others = [
        "/api/v1/status",
        f"/api/v1/projects/{project['id']}/documents/1",
        f"/api/projects/{project['id']}/documents/upload",
        f"/api/v2/projects/{project['id']}/documents",
    ]
    caddyfile = (REPOSITORY / "deploy" / "aws" / "Caddyfile").read_text()
    guide = (REPOSITORY / "deploying.md").read_text()
    for text in (caddyfile, guide):
        large = rules(text, r"^\s*@uploads path_regexp (\S+)$")
        small = rules(text, r"^\s*@others not path_regexp (\S+)$")
        assert large == small
        for rule in large:
            assert all(re.search(rule, path) for path in uploads), rule
            assert not any(re.search(rule, path) for path in others), rule
    for rule in rules(guide, r"^\s*location ~ (\S+) \{$"):
        assert all(re.search(rule, path) for path in uploads), rule
        assert not any(re.search(rule, path) for path in others), rule


def test_a_plan_without_room_stops_the_sending_and_says_why(
    member,  # noqa: F811
    plan,  # noqa: F811
    client,
    tmp_path,
    monkeypatch,
):
    plan(documents=1, api_access=True)
    create_project(client)
    key = make_key(client, scope="write")["key"]
    # From here on the key alone says who calls.
    client.app.dependency_overrides.pop(get_account)
    monkeypatch.setattr(get_settings(), "max_upload_files", 2)
    folder = write(tmp_path / "Many", {"a.txt": "One.", "b.txt": "Two.", "c.txt": "Three."})
    calls: list[uploader.Request] = []
    done = run(
        client, "send", "--project", "Contracts", "--no-wait", str(folder), key=key, calls=calls
    )
    assert (done.code, done.out) == (
        1,
        [
            "Sending 3 files into Contracts.",
            "Sent 1 file (1 upload).",
            f"2 files not sent: {ONE_DOCUMENT}",
        ],
    )
    # The file the plan refused stops the sending: the third is not sent to be refused too.
    assert [files_in(call) for call in posts(calls)] == [2]

    # With the plan full, the request is refused whole.
    full = run(client, "send", "--project", "Contracts", str(folder / "c.txt"), key=key)
    assert (full.code, full.out[-1]) == (1, f"1 file not sent: {ONE_DOCUMENT}")


def test_a_key_or_an_address_the_client_cannot_use_stops_it(client, tmp_path):
    create_project(client)
    folder = write(tmp_path / "In", {"a.txt": "A."})
    read_only = make_key(client, "Reader")["key"]
    refused = run(client, "send", "--project", "Contracts", str(folder), key=read_only)
    assert (refused.code, refused.out, refused.err) == (
        2,
        ["Sending 1 file into Contracts."],
        [f"emudoc_upload: {READ_ONLY_KEY}"],
    )
    unknown = run(client, "send", "--project", "Contracts", str(folder), key="emk_" + "x" * 43)
    assert (unknown.code, unknown.err) == (2, [f"emudoc_upload: {KEY_UNKNOWN}"])

    keyless = run(client, "send", "--project", "Contracts", str(folder), key=None)
    assert keyless.code == 2
    assert keyless.err[0].startswith("emudoc_upload: Set EMUDOC_API_KEY to an API key that reads")

    key = make_key(client, scope="write")["key"]
    plain = run(
        client,
        "send",
        "--server",
        "http://emudoc.example.org/projects/1",
        "--project",
        "1",
        str(folder),
        key=key,
    )
    assert plain.err == [
        "emudoc_upload: The key would cross the network unencrypted to "
        "http://emudoc.example.org/projects/1: give its https:// address."
    ]

    def moved(request: uploader.Request) -> uploader.Response:
        where = "https://emudoc.example.com/api/v1/projects"
        return uploader.Response(308, {"location": where}, b"")

    redirected = run(
        client, "send", "--project", "Contracts", str(folder), key=key, transport=moved
    )
    assert redirected.err == [
        "emudoc_upload: Emudoc answered from https://emudoc.example.com/api/v1/projects: give "
        "that address as --server."
    ]

    def down(request: uploader.Request) -> uploader.Response:
        raise uploader.Unavailable("Emudoc could not be reached (Connection refused).")

    waits: list[float] = []
    away = run(
        client,
        "send",
        "--project",
        "Contracts",
        str(folder),
        key=key,
        transport=down,
        sleep=waits.append,
    )
    # Tried again with the waits doubling, then given up.
    assert waits == [1, 2, 4, 8, 16]
    assert away.code == 2
    assert away.err == ["emudoc_upload: Emudoc could not be reached (Connection refused)."]


def test_a_project_is_found_by_its_name_or_its_number(client, tmp_path):
    upper = create_project(client, "Invoices")
    lower = create_project(client, "invoices")
    key = make_key(client, scope="write")["key"]

    def send_to(project: str, name: str) -> Run:
        folder = write(tmp_path / name, {f"{name}.txt": f"For {name}."})
        return run(client, "send", "--project", project, "--no-wait", str(folder), key=key)

    assert send_to("invoices", "lower").code == 0
    assert send_to(str(upper["id"]), "upper").code == 0
    assert list(tree(client, lower["id"])) == ["lower"]
    assert list(tree(client, upper["id"])) == ["upper"]

    several = send_to("INVOICES", "several")
    assert several.err == [
        f"emudoc_upload: Several projects are named 'INVOICES' (numbers {lower['id']}, "
        f"{upper['id']}): give --project the number of the one to send to, the one in its "
        "address."
    ]
    missing = send_to("Receipts", "missing")
    assert missing.err == [
        "emudoc_upload: No project the key reaches is named or numbered 'Receipts'. It reaches "
        f"invoices ({lower['id']}), Invoices ({upper['id']})."
    ]


def looking(session, services, *changes: Callable[[], object]) -> Callable[[float], None]:
    """The waits between a watch's looks: in each the worker reads what is queued and the next
    change is made to the folder, and after the last the watch is stopped, as Ctrl+C does."""
    steps = iter(changes)
    read = reading(session, services)

    def sleep(seconds: float) -> None:
        read(seconds)
        step = next(steps, None)
        if step is None:
            raise KeyboardInterrupt
        step()

    return sleep


def test_watch_sends_a_file_once_it_has_stopped_changing_and_never_again(
    client, session, services, tmp_path
):
    project = create_project(client)
    key = make_key(client, scope="write")["key"]
    folder = write(tmp_path / "Scanner", {"first.txt": "The first scan."})
    calls: list[uploader.Request] = []
    done = run(
        client,
        "watch",
        "--project",
        "Contracts",
        str(folder),
        key=key,
        calls=calls,
        sleep=looking(
            session,
            services,
            # The scanner begins a second scan while the first is sent,
            lambda: write(folder, {"second.txt": "The second scan, half"}),
            # and finishes it at the next look,
            lambda: write(folder, {"second.txt": "The second scan, half and then the rest."}),
            # which stays as it is, and is sent;
            lambda: None,
            # the first, renamed, is the bytes sent already.
            lambda: (folder / "first.txt").rename(folder / "first again.txt"),
            lambda: None,
            lambda: None,
        ),
    )
    assert (done.code, done.err) == (0, [])
    assert done.said == [
        f"Watching {folder} for Contracts: each file is sent once it has stayed the same for 10 "
        "seconds. Ctrl+C stops.",
        "Sent first.txt.",
        "Read first.txt.",
        "Sent second.txt.",
        "Read second.txt.",
        "Stopped watching.",
    ]
    assert [files_in(call) for call in posts(calls)] == [1, 1]
    assert [d["filename"] for d in listed(client, project["id"])] == ["first.txt", "second.txt"]
    assert sorted(path.name for path in folder.iterdir()) == [
        ".emudoc-upload.json",
        "first again.txt",
        "second.txt",
    ]

    # Started again, it sends nothing the state file holds.
    calls = []
    again = run(
        client,
        "watch",
        "--project",
        "Contracts",
        str(folder),
        key=key,
        calls=calls,
        sleep=looking(session, services, lambda: None, lambda: None),
    )
    assert again.code == 0 and posts(calls) == []


def test_watch_says_once_what_it_refuses_and_sends_again_what_could_not_be_sent(
    client, session, services, tmp_path
):
    create_project(client)
    key = make_key(client, scope="write")["key"]
    folder = write(
        tmp_path / "Scanner",
        {"fake.pdf": "Not a PDF.", "notes.odt": "Notes", "scan.txt": "A scan."},
    )
    app = through(client)
    outage = [True]

    def flaky(request: uploader.Request) -> uploader.Response:
        # Emudoc is out of reach for the first upload.
        if request.method == "POST" and outage and outage.pop():
            raise uploader.Unavailable("Emudoc could not be reached (Connection refused).")
        return app(request)

    done = run(
        client,
        "watch",
        "--project",
        "Contracts",
        "--every",
        "5",
        str(folder),
        key=key,
        transport=flaky,
        sleep=looking(session, services, *[lambda: None] * 3),
    )
    assert done.said == [
        f"Watching {folder} for Contracts: each file is sent once it has stayed the same for 5 "
        "seconds. Ctrl+C stops.",
        f"Left out notes.odt: Unsupported type. Use {', '.join(sorted(SUPPORTED_EXTENSIONS))}.",
        "Emudoc could not be reached (Connection refused). Trying again at the next look.",
        "Sent scan.txt.",
        f"Refused fake.pdf: Not a PDF file. {RENAME}",
        "Read scan.txt.",
        "Stopped watching.",
    ]


def test_watch_waits_while_the_plan_has_no_room(
    member,  # noqa: F811
    plan,  # noqa: F811
    client,
    session,
    services,
    tmp_path,
):
    plan(documents=1, api_access=True)
    project = create_project(client)
    key = make_key(client, scope="write")["key"]
    client.app.dependency_overrides.pop(get_account)
    api = TestClient(client.app, headers={"Authorization": f"Bearer {key}"})
    folder = write(tmp_path / "Scanner", {"a.txt": "One.", "b.txt": "Two."})
    now = [0.0]
    read = reading(session, services)

    def deleted() -> None:
        (document,) = listed(api, project["id"])
        assert api.delete(f"/api/documents/{document['id']}").status_code == 204

    steps = iter(
        [
            lambda: None,
            lambda: None,
            # While the watch waits, the member makes room,
            deleted,
            # and the wait ends.
            lambda: now.__setitem__(0, now[0] + uploader.PLAN_PAUSE),
            lambda: None,
        ]
    )

    def sleep(seconds: float) -> None:
        now[0] += seconds
        read(seconds)
        step = next(steps, None)
        if step is None:
            raise KeyboardInterrupt
        step()

    calls: list[uploader.Request] = []
    done = run(
        client,
        "watch",
        "--project",
        "Contracts",
        str(folder),
        key=key,
        calls=calls,
        sleep=sleep,
        clock=lambda: now[0],
    )
    said = done.said
    assert said[1] == "Sent a.txt."
    assert said[2].startswith(f"{ONE_DOCUMENT} 1 file waiting, tried again at ")
    assert said[3:] == ["Read a.txt.", "Sent b.txt.", "Read b.txt.", "Stopped watching."]
    assert [files_in(call) for call in posts(calls)] == [2, 1]


@contextmanager
def serving(app) -> Iterator[str]:
    """The app served over HTTP by uvicorn in a thread, at the address yielded."""
    listening = socket.socket()
    listening.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listening]}, daemon=True)
    thread.start()
    started = time.monotonic()
    while not server.started:
        assert time.monotonic() - started < 10, "uvicorn did not start"
        time.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{listening.getsockname()[1]}"
    finally:
        server.should_exit = True
        thread.join(10)
        listening.close()


def downloaded(server: str, tmp_path: Path) -> Path:
    """The client as a member downloads it from the app, into a folder of its own."""
    script = tmp_path / "bin" / UPLOAD_CLIENT.name
    script.parent.mkdir(exist_ok=True)
    with urllib.request.urlopen(f"{server}/api/help/{UPLOAD_CLIENT.name}") as answer:
        script.write_bytes(answer.read())
    return script


def started(script: Path, *argv: str, key: str, cwd: Path) -> subprocess.Popen:
    """The client run as a member runs it, by the Python of PYTHON, isolated from the suite's
    environment but for the key."""
    return subprocess.Popen(
        [PYTHON, "-I", str(script), *argv],
        cwd=cwd,
        env={"PATH": os.environ["PATH"], "LC_ALL": "C.UTF-8", uploader.KEY_VARIABLE: key},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def test_the_client_downloaded_from_the_app_sends_a_folder_over_http(
    client, session, services, tmp_path
):
    project = create_project(client, "Factures")
    key = make_key(client, scope="write")["key"]
    folder = write(tmp_path / "Été 2026", {"reçu.txt": "Un reçu.", "Juillet/facture.md": "# Une"})
    read = reading(session, services)
    with serving(client.app) as server:
        script = downloaded(server, tmp_path)
        process = started(
            script,
            "send",
            "--server",
            server,
            "--project",
            "Factures",
            "--state",
            "sent.json",
            folder.name,
            key=key,
            cwd=tmp_path,
        )
        # The worker reads what the client sent while the client follows it.
        while process.poll() is None:
            read(0)
            time.sleep(0.1)
        out, err = process.communicate(timeout=10)
    assert (process.returncode, err.decode()) == (0, "")
    assert out.decode().splitlines() == [
        "Sending 2 files into Factures.",
        "Sent 2 files (1 upload).",
        "Reading: 0 of 2 ready.",
        "Read the 2 files sent.",
    ]
    assert tree(client, project["id"]) == {
        "Été 2026": ["reçu.txt"],
        "Été 2026/Juillet": ["facture.md"],
    }
    assert len(json.loads((tmp_path / "sent.json").read_text())["sent"]) == 2


def test_a_copy_from_before_the_versioned_api_goes_on_sending_over_http(
    client, session, services, tmp_path
):
    """The copy members downloaded before the client moved onto /api/v1 calls the page's routes
    (`GET /api/projects`, `GET /api/status`, `POST /api/projects/{id}/documents`, `GET
    /api/projects/{id}/uploads`), which go on answering a key; and the client of today takes
    its state file up."""
    project = create_project(client, "Factures")
    key = make_key(client, scope="write")["key"]
    write(tmp_path / "Scans", {"a.txt": "The first scan.", "b.txt": "The second scan."})
    read = reading(session, services)
    with serving(client.app) as server:
        argv = (
            "send",
            "--server",
            server,
            "--project",
            "Factures",
            "--state",
            "sent.json",
            "Scans",
        )
        process = started(BEFORE_V1, *argv, key=key, cwd=tmp_path)
        while process.poll() is None:
            read(0)
            time.sleep(0.1)
        out, err = process.communicate(timeout=10)
        assert (process.returncode, err.decode()) == (0, "")
        assert out.decode().splitlines() == [
            "Sending 2 files into Factures.",
            "Sent 2 files (1 upload).",
            "Reading: 0 of 2 ready.",
            "Read the 2 files sent.",
        ]
        today = started(downloaded(server, tmp_path), *argv, key=key, cwd=tmp_path)
        out, err = today.communicate(timeout=30)
    assert (today.returncode, err.decode()) == (0, "")
    assert out.decode().splitlines() == [
        "Sending 2 files into Factures.",
        "2 files sent before, as the state file says.",
    ]
    assert tree(client, project["id"]) == {"Scans": ["a.txt", "b.txt"]}


def test_the_client_downloaded_from_the_app_watches_a_folder_over_http(client, tmp_path):
    project = create_project(client)
    key = make_key(client, scope="write")["key"]
    folder = write(tmp_path / "Scanner", {"scan.txt": "A scan."})
    with serving(client.app) as server:
        process = started(
            downloaded(server, tmp_path),
            "watch",
            "--server",
            server,
            "--project",
            "Contracts",
            "--every",
            "1",
            "Scanner",
            key=key,
            cwd=tmp_path,
        )
        try:
            said = read_until(process.stdout, "Sent scan.txt.", timeout=30)
        finally:
            # Ctrl+C, as a member stops it.
            process.send_signal(signal.SIGINT)
            out, err = process.communicate(timeout=10)
    assert (process.returncode, err.decode()) == (0, "")
    assert (said + out.decode()).splitlines()[-1].endswith(" Stopped watching.")
    assert [d["filename"] for d in listed(client, project["id"])] == ["scan.txt"]
    assert (folder / ".emudoc-upload.json").exists() and (folder / "scan.txt").exists()


def test_the_app_serves_the_client_to_anyone(client, signed_in_only):
    answer = client.get(f"/api/help/{UPLOAD_CLIENT.name}")
    assert answer.status_code == 200
    assert answer.headers["content-disposition"] == f'attachment; filename="{UPLOAD_CLIENT.name}"'
    assert answer.headers["content-type"].startswith("text/x-python")
    assert answer.content == UPLOAD_CLIENT.read_bytes()


def test_the_client_is_the_standard_library_alone_for_python_3_9():
    source = UPLOAD_CLIENT.read_text()
    assert source.startswith("#!/usr/bin/env python3\n")
    # The grammar of the oldest Python it promises, as far as ast knows it, and nothing of the
    # app or of the packages beside it.
    parsed = ast.parse(source, feature_version=uploader.OLDEST_PYTHON)
    imported = {
        name.split(".")[0]
        for node in ast.walk(parsed)
        for name in (
            [alias.name for alias in node.names]
            if isinstance(node, ast.Import)
            else [node.module or ""]
            if isinstance(node, ast.ImportFrom)
            else []
        )
    }
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}, imported
