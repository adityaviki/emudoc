#!/usr/bin/env python3
"""Emudoc's upload client: sends the files of a folder into a project, or goes on sending each
file a scanner saves to one, through Emudoc's API with an API key.

    python3 emudoc_upload.py send --server https://emudoc.example.org --project Invoices FOLDER
    python3 emudoc_upload.py watch --server https://emudoc.example.org --project Invoices FOLDER

The key is read from the environment variable EMUDOC_API_KEY, never from the command line, which
other users of the computer can read: an owner or admin makes one on the organisation's page,
under API keys, with the scope "Read and write". --server may be EMUDOC_URL instead, and
--project is the project's name or its number, the one in its address.

`send` files a folder as it is, inside the project or inside the folder --into names, and a file
named on its own into that folder, then follows Emudoc's reading of them until it is done
(unless --no-wait). `watch` looks at the folder every few seconds (--every) and sends a file once
its size and time have stayed the same from one look to the next, since a scanner writes slowly;
the files go in as they are below the folder. Both leave out, before anything is sent, what
Emudoc would not take (a type it does not read, more bytes than a file may have, nothing at all)
and what a folder holds hidden, send the rest in requests within the server's limits, and say
what Emudoc refused and why. Neither deletes, moves or changes a file.

What was sent is kept in a state file (--state; `watch` keeps one in its folder unless told
otherwise, `.emudoc-upload.json`) by the SHA-256 of each file's bytes, so that a file is sent
once, however it is renamed and however often the client starts. Without one, Emudoc itself
knows a file it holds already, and says where it is. Each request carries an Idempotency-Key,
kept (in the state file too) until its answer arrives, so that files sent again after a lost
answer are taken once and answered as the first time.

It calls Emudoc's versioned API, /api/v1, which changes by addition alone, so that a copy
downloaded once goes on working as Emudoc changes.

The exit status is 0 when every file was taken and read, 1 when one was left out, refused or
could not be read, 2 when the client could not do its work (no key, or an address, key or
project it cannot use, or a server that stayed out of reach), and 130 when it was stopped while
sending or following.

Standard library only, so any Python from 3.9 runs it, a Mac's own python3 among them.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import platform
import re
import ssl
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import IO, Any, TypeVar

OLDEST_PYTHON = (3, 9)
KEY_VARIABLE = "EMUDOC_API_KEY"
SERVER_VARIABLE = "EMUDOC_URL"
STATE_NAME = ".emudoc-upload.json"
# How often `watch` looks at its folder, in seconds, unless told otherwise.
EVERY = 10.0
MB = 1024 * 1024
# The most bytes one request reads into memory: a request holds its files' bytes, so that what
# it sends is what was hashed, and a computer by a scanner may have little memory. A file larger
# than this goes alone.
MEMORY = 64 * MB
# Files a folder holds that its owner does not see, besides those hidden by a leading dot, as the
# page leaves them out (frontend/src/workspace/uploads.ts).
SYSTEM_FILES = {"thumbs.db", "ehthumbs.db", "desktop.ini"}
SEPARATOR = "/"
# Plain HTTP carries the key in the clear, so it is for the computer's own server alone.
LOOPBACK = {"localhost", "127.0.0.1", "::1"}
# The longest a request may take: the largest upload on a slow line.
TIMEOUT = 600
# How often `send` asks how far its files are read.
FOLLOW_EVERY = 5
# The most items a page of the API's lists holds, and so the most documents one request asks
# about (MOST in backend/app/api/v1/deps.py).
MOST = 200
# How long Emudoc keeps the answer to a request for the request sent again with its
# Idempotency-Key, in seconds (KEPT in backend/app/idempotency.py): a key whose answer never
# came is forgotten after that.
KEPT = 24 * 3600
# What a document that could not be indexed says when Emudoc gives no reason (NOT_INDEXED in
# backend/app/notices.py).
NOT_INDEXED = "It could not be indexed."
# How many times `send` makes a request that failed for a reason that passes (no answer, a
# server restarting) before it gives up, the wait between doubling up to MAX_WAIT seconds; and
# the longest a rate limit's Retry-After is waited for.
TRIES = 6
MAX_WAIT = 60
LONGEST_RETRY_AFTER = 3600
# How long `watch` waits before it sends again once the plan has had no room.
PLAN_PAUSE = 15 * 60
# How many files a list in the output names before it counts the rest.
NAMED = 20
USER_AGENT = f"emudoc-upload Python/{platform.python_version()}"

T = TypeVar("T")


class Unusable(Exception):
    """What stops the client, in a sentence that says what to do: exit status 2."""


class Unavailable(Exception):
    """Emudoc could not be reached, or could not answer just now: a reason that passes."""


class PlanFull(Exception):
    """The organisation's plan has no room for a document, in Emudoc's words."""


class TooLarge(Exception):
    """The server, or a proxy before it, would not take a request of this size."""


class Invalid(Unusable):
    """Emudoc found the request itself wrong (422), such as an Idempotency-Key it holds for
    another request."""


@dataclass
class Request:
    method: str
    url: str
    headers: dict[str, str]
    body: bytes | None = None


@dataclass
class Response:
    status: int
    # Their names in lower case.
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body) if self.body else None


# How a request reaches the server: over HTTP for a member, the test client in the suite.
Transport = Callable[[Request], Response]


def over_http(timeout: float = TIMEOUT) -> Transport:
    """Requests sent with urllib, with no redirect followed: a POST must never be sent again as
    a GET, and an address that moved is the member's to give."""
    opener = urllib.request.build_opener(_Unredirected)

    def send(request: Request) -> Response:
        sent = urllib.request.Request(
            request.url, data=request.body, headers=request.headers, method=request.method
        )
        try:
            with opener.open(sent, timeout=timeout) as answer:
                return Response(answer.status, _headers(answer.headers), answer.read())
        except urllib.error.HTTPError as error:
            try:
                return Response(error.code, _headers(error.headers), error.read())
            finally:
                error.close()
        except urllib.error.URLError as error:
            reason = error.reason
            if isinstance(reason, ssl.SSLCertVerificationError):
                raise Unusable(
                    f"The server's certificate could not be verified ({reason.verify_message}): "
                    "check the address, or the computer's certificates."
                ) from None
            raise Unavailable(f"Emudoc could not be reached ({reason}).") from None
        except (OSError, http.client.HTTPException) as error:
            raise Unavailable(f"Emudoc did not answer ({error or type(error).__name__}).") from None

    return send


class _Unredirected(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _headers(message: Any) -> dict[str, str]:
    return {name.lower(): value for name, value in message.items()}


def detail(response: Response) -> str:
    """Emudoc's sentence for an answer that is not a success, or what its status says when
    something else answered, such as a proxy."""
    try:
        said = response.json()
    except ValueError:
        said = None
    if isinstance(said, dict) and isinstance(said.get("detail"), str):
        return said["detail"]
    reason = http.client.responses.get(response.status, "")
    return f"The server answered {response.status} {reason}".rstrip() + "."


def retry_after(response: Response) -> int:
    """The seconds a rate limit asks the client to wait (Retry-After), a minute when it does not
    say."""
    try:
        seconds = int(response.headers.get("retry-after", ""))
    except ValueError:
        return 60
    return min(max(seconds, 1), LONGEST_RETRY_AFTER)


class Api:
    """Emudoc's API as a key calls it: the key alone on every request, a rate limit waited out,
    and an answer other than a success raised as what it means to the client."""

    def __init__(
        self,
        server: str,
        key: str,
        transport: Transport,
        sleep: Callable[[float], None],
        say: Callable[[str], None],
    ) -> None:
        self.server = server
        self.key = key
        self.transport = transport
        self.sleep = sleep
        self.say = say

    def call(
        self,
        method: str,
        path: str,
        query: Sequence[tuple[str, Any]] = (),
        body: bytes | None = None,
        content_type: str | None = None,
        key: str | None = None,
    ) -> Any:
        """The answer's JSON. `key` is the request's Idempotency-Key, while the request first
        sent with it is still being carried out waited for as Emudoc asks."""
        url = self.server + path
        if query:
            url += "?" + urllib.parse.urlencode(list(query))
        headers = {
            "Authorization": f"Bearer {self.key}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        if content_type:
            headers["Content-Type"] = content_type
        if key is not None:
            headers["Idempotency-Key"] = key
        while True:
            response = self.transport(Request(method, url, headers, body))
            if response.status == 429:
                wait = retry_after(response)
                self.say(f"{detail(response)} Waiting {wait} seconds, as Emudoc asks.")
            elif response.status == 409 and key is not None and "retry-after" in response.headers:
                wait = retry_after(response)
                self.say(
                    f"Emudoc is still taking these files from the request before. Waiting {wait} "
                    "seconds for its answer."
                )
            else:
                break
            self.sleep(wait)
        if 200 <= response.status < 300:
            return response.json()
        said = detail(response)
        if response.status == 402:
            raise PlanFull(said)
        if response.status == 413:
            raise TooLarge(said)
        if response.status >= 500:
            raise Unavailable(said)
        if 300 <= response.status < 400:
            where = response.headers.get("location", "another address")
            raise Unusable(f"Emudoc answered from {where}: give that address as --server.")
        if response.status == 422:
            raise Invalid(said)
        raise Unusable(said)


def patiently(
    work: Callable[[], T], sleep: Callable[[float], None], say: Callable[[str], None], tries: int
) -> T:
    """What `work` returns, made again while Emudoc is out of reach, up to `tries` times."""
    for attempt in range(tries):
        try:
            return work()
        except Unavailable as error:
            if attempt == tries - 1:
                raise
            wait = min(2**attempt, MAX_WAIT)
            say(f"{error} Trying again in {wait} seconds.")
            sleep(wait)
    raise AssertionError("unreachable")


def address(text: str) -> str:
    """The server's origin from an address a member typed or copied from the app, a page's path
    and all: its scheme and host."""
    text = text.strip()
    if not text:
        raise Unusable(
            "Give Emudoc's address with --server, such as https://emudoc.example.org, or set "
            f"{SERVER_VARIABLE}."
        )
    if "://" not in text:
        text = f"https://{text}"
    parts = urllib.parse.urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise Unusable(f"{text} is not a web address: give one such as https://emudoc.example.org.")
    if parts.scheme == "http" and parts.hostname not in LOOPBACK:
        raise Unusable(
            f"The key would cross the network unencrypted to {text}: give its https:// address."
        )
    return f"{parts.scheme}://{parts.netloc}"


@dataclass(frozen=True)
class Project:
    id: int
    name: str


def every(api: Api, path: str, query: Sequence[tuple[str, Any]] = ()) -> Iterator[Any]:
    """Each item of a list of the API, page after page."""
    after = None
    while True:
        asked = [*query, ("limit", MOST), *([("after", after)] if after else [])]
        page = api.call("GET", path, asked)
        yield from page["data"]
        after = page.get("next")
        if not after:
            return


def find_project(api: Api, wanted: str) -> Project:
    """The project a key reaches by its name, as written, or its number, or its name whatever
    its case."""
    projects = [Project(found["id"], found["name"]) for found in every(api, "/api/v1/projects")]
    wanted = wanted.strip()
    matched = [project for project in projects if project.name == wanted]
    if not matched and re.fullmatch(r"[0-9]+", wanted):
        matched = [project for project in projects if project.id == int(wanted)]
    if not matched:
        matched = [project for project in projects if project.name.casefold() == wanted.casefold()]
    if len(matched) == 1:
        return matched[0]
    if matched:
        numbers = ", ".join(str(project.id) for project in matched)
        raise Unusable(
            f"Several projects are named {wanted!r} (numbers {numbers}): give --project the "
            "number of the one to send to, the one in its address."
        )
    if not projects:
        raise Unusable("The key reaches no project: create one in Emudoc first.")
    named = ", ".join(f"{project.name} ({project.id})" for project in projects[:NAMED])
    more = f", and {len(projects) - NAMED} more" if len(projects) > NAMED else ""
    raise Unusable(
        f"No project the key reaches is named or numbered {wanted!r}. It reaches {named}{more}."
    )


@dataclass(frozen=True)
class Limits:
    """What one upload request may carry, and the file types Emudoc reads (GET
    /api/v1/status)."""

    files: int
    request_bytes: int
    file_bytes: int
    # The extensions, None from a server that does not say, which then refuses the rest itself.
    extensions: frozenset[str] | None

    @classmethod
    def of(cls, status: Mapping[str, Any]) -> Limits:
        uploads = status["uploads"]
        extensions = uploads.get("extensions")
        return cls(
            files=uploads["files"],
            request_bytes=uploads["request_bytes"],
            file_bytes=uploads["file_bytes"],
            extensions=frozenset(extensions) if extensions is not None else None,
        )


def connect(
    api: Api, wanted: str, sleep: Callable[[float], None], say: Callable[[str], None]
) -> tuple[Project, Limits]:
    project = patiently(lambda: find_project(api, wanted), sleep, say, TRIES)
    limits = Limits.of(patiently(lambda: api.call("GET", "/api/v1/status"), sleep, say, TRIES))
    return project, limits


@dataclass(frozen=True)
class Local:
    """A file on the computer, and the folders it goes into below where the files are sent."""

    path: Path
    folders: tuple[str, ...]
    size: int
    mtime: int

    @property
    def name(self) -> str:
        """Its name with its folders, as the client names it."""
        return SEPARATOR.join([*self.folders, self.path.name])

    @property
    def version(self) -> tuple[int, int]:
        return self.size, self.mtime


def hidden(name: str) -> bool:
    return name.startswith(".") or name.lower() in SYSTEM_FILES


def walk(
    root: Path,
    top: tuple[str, ...],
    skip: Path | None,
    unreadable: Callable[[str, OSError], None],
) -> Iterator[Local]:
    """The files below the folder `root`, by name, each going into `top` and the folders it is
    in below `root`; what the folder holds hidden is left out, as the file `skip` is."""

    def failed(error: OSError) -> None:
        unreadable(str(error.filename or root), error)

    for directory, folders, names in os.walk(root, onerror=failed):
        folders[:] = sorted(folder for folder in folders if not hidden(folder))
        below = Path(directory).relative_to(root).parts
        for name in sorted(names):
            path = Path(directory, name)
            if hidden(name) or path == skip:
                continue
            try:
                found = path.stat()
            except OSError as error:
                unreadable(str(path), error)
                continue
            # Windows hides some files by an attribute rather than a name: Office's lock files.
            if not stat.S_ISREG(found.st_mode) or (
                getattr(found, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_HIDDEN
            ):
                continue
            yield Local(path, (*top, *below), found.st_size, found.st_mtime_ns)


def gather(
    paths: Sequence[Path], skip: Path | None, unreadable: Callable[[str, OSError], None]
) -> list[Local]:
    """The files `send` sends: each folder's under its own name, each file named on its own
    into where they all go."""
    files: list[Local] = []
    for path in paths:
        if path.is_dir():
            files.extend(walk(path.resolve(), (path.resolve().name,), skip, unreadable))
        elif path.is_file():
            found = path.stat()
            files.append(Local(path, (), found.st_size, found.st_mtime_ns))
        else:
            raise Unusable(f"{path} is neither a file nor a folder.")
    return files


def screen(files: Sequence[Local], limits: Limits) -> tuple[list[Local], list[tuple[Local, str]]]:
    """The files to send, and those left out with why, in Emudoc's words for a file it would
    refuse: of a type it does not read, larger than one file or one request may be, or empty."""
    largest = min(limits.file_bytes, limits.request_bytes)
    kept: list[Local] = []
    left: list[tuple[Local, str]] = []
    for local in files:
        if limits.extensions is not None and local.path.suffix.lower() not in limits.extensions:
            left.append((local, f"Unsupported type. Use {', '.join(sorted(limits.extensions))}."))
        elif local.size > largest:
            left.append((local, f"Larger than {largest // MB} MB."))
        elif local.size == 0:
            left.append((local, "Empty file."))
        else:
            kept.append(local)
    return kept, left


def batches(files: Sequence[Local], limits: Limits) -> list[list[Local]]:
    """The files in the requests that carry them, in order, each of at most `files` files and
    `request_bytes` bytes, and no more than MEMORY unless one file is larger alone."""
    most = min(limits.request_bytes, MEMORY)
    requests: list[list[Local]] = []
    size = 0
    for local in files:
        if requests and len(requests[-1]) < limits.files and size + local.size <= most:
            requests[-1].append(local)
            size += local.size
        else:
            requests.append([local])
            size = local.size
    return requests


def form(files: Sequence[tuple[str, str, bytes]]) -> tuple[bytes, str]:
    """A multipart/form-data body of the files, each its folders below where they are sent,
    its name and its bytes, written as a browser writes one: a name's quote, CR and LF
    percent-encoded, the rest UTF-8. Returns the body and its content type."""
    boundary = f"emudoc-{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for folders, name, data in files:
        chunks.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="paths"\r\n\r\n'
            f"{_escaped(folders)}\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; "
            f'name="files"; filename="{_escaped(name)}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n".encode()
        )
        chunks.append(data)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _escaped(text: str) -> str:
    return text.replace('"', "%22").replace("\r", "%0D").replace("\n", "%0A")


class State:
    """What was sent to one project, by the SHA-256 of each file's bytes, kept in a JSON file so
    that a file is sent once however it is renamed and however often the client starts. A file
    Emudoc held already counts as sent; one it refused does not, and is tried again by the
    client's next run. It also keeps the Idempotency-Key of each request whose answer never
    came, with the files it carried, so that a later run sending them again is given that
    answer."""

    def __init__(self, path: Path, server: str, project: Project) -> None:
        self.path = path
        self.server = server
        self.project = project.id
        self.sent: dict[str, dict[str, Any]] = {}
        self.unanswered: dict[str, dict[str, Any]] = {}
        try:
            text = path.read_text("utf-8")
        except FileNotFoundError:
            return
        except OSError as error:
            raise Unusable(f"The state file {path} cannot be read ({error.strerror}).") from None
        try:
            kept = json.loads(text)
            sent = dict(kept["sent"])
            # A state file of a client from before the keys holds none.
            unanswered = dict(kept.get("unanswered", {}))
            for request in unanswered.values():
                if "files" not in request or datetime.fromisoformat(request["at"]).tzinfo is None:
                    raise ValueError
        except (ValueError, KeyError, TypeError):
            raise Unusable(
                f"{path} is not a state file of this client: give --state another file."
            ) from None
        if (kept.get("server"), kept.get("project")) != (server, project.id):
            raise Unusable(
                f"The state file {path} is project {kept.get('project')}'s at "
                f"{kept.get('server')}: give --state another file for this project."
            )
        self.sent = sent
        self.unanswered = unanswered

    def holds(self, digest: str) -> bool:
        return digest in self.sent

    def keep(self, digest: str, name: str, document: int) -> None:
        at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.sent[digest] = {"name": name, "document": document, "at": at}

    def save(self) -> None:
        """Written whole beside itself, then moved over the file, so that a stop halfway leaves
        the one before."""
        written = self.path.with_name(f".{self.path.name}.part")
        kept = {
            "server": self.server,
            "project": self.project,
            "sent": self.sent,
            "unanswered": self.unanswered,
        }
        try:
            written.write_text(json.dumps(kept, indent=1, sort_keys=True), "utf-8")
            os.replace(written, self.path)
        except OSError as error:
            raise Unusable(
                f"The state file {self.path} cannot be written ({error.strerror}): give --state "
                "a file the client may write."
            ) from None


@dataclass
class Upload:
    """One request's files kept, and the documents Emudoc made of them, followed as they are
    read."""

    documents: list[int]
    names: list[str]


@dataclass
class Sent:
    """What sending some files came to."""

    kept: list[Local] = field(default_factory=list)
    # In the project already, with Emudoc's sentence of where.
    present: list[tuple[Local, str]] = field(default_factory=list)
    # Sent before, as the state file says.
    before: list[Local] = field(default_factory=list)
    refused: list[tuple[Local, str]] = field(default_factory=list)
    # Not sent, since they could not be read from the computer.
    unread: list[tuple[Local, str]] = field(default_factory=list)
    # Not sent, or refused, for want of room in the plan, which `plan` says.
    waiting: list[Local] = field(default_factory=list)
    plan: str | None = None
    uploads: list[Upload] = field(default_factory=list)
    # The SHA-256 of each file read.
    digests: dict[Path, str] = field(default_factory=dict)


class Sender:
    """Sends files into a project, request by request within the server's limits, and notes in
    the state what Emudoc kept or held already. `tries` is how often a request is made while
    Emudoc is out of reach before `Unavailable` ends the sending."""

    def __init__(
        self,
        api: Api,
        project: Project,
        limits: Limits,
        into: tuple[str, ...],
        state: State | None,
        tries: int,
    ) -> None:
        self.api = api
        self.project = project
        self.limits = limits
        self.into = into
        self.state = state
        self.tries = tries
        # The Idempotency-Key of each request whose answer never came, by the files it carried:
        # the state's, or this run's own without one.
        self.unanswered = state.unanswered if state is not None else {}

    def send(self, files: Sequence[Local], sent: Sent) -> None:
        queue = batches(files, self.limits)
        while queue:
            batch = queue.pop(0)
            loaded = [read for read in (self._read(local, sent) for local in batch) if read]
            if not loaded:
                continue
            parts = [
                (SEPARATOR.join([*self.into, *local.folders]), local.path.name, data, digest)
                for local, data, digest in loaded
            ]
            body, content_type = form([(folders, name, data) for folders, name, data, _ in parts])
            carried = [[folders, name, digest] for folders, name, _, digest in parts]
            try:
                result = self._post(carried, body, content_type)
            except TooLarge as error:
                # A proxy before the server may take less than the server says: halves, then
                # the file alone, which is refused with the proxy's answer.
                if len(loaded) > 1:
                    half = len(loaded) // 2
                    queue[:0] = [
                        [local for local, _, _ in loaded[:half]],
                        [local for local, _, _ in loaded[half:]],
                    ]
                else:
                    sent.refused.append((loaded[0][0], str(error)))
                continue
            except PlanFull as error:
                sent.plan = str(error)
                sent.waiting.extend(local for local, _, _ in loaded)
                sent.waiting.extend(local for rest in queue for local in rest)
                return
            self._settle(loaded, result, sent)
            if self.state is not None:
                self.state.save()
            if sent.plan is not None:
                sent.waiting.extend(local for rest in queue for local in rest)
                return

    def _post(self, carried: list[list[str]], body: bytes, content_type: str) -> Any:
        """Emudoc's answer to the upload of the files `carried` names (each its folders, name
        and SHA-256), under an Idempotency-Key kept until an answer comes, so that the files
        sent again, by this run or a later one, are taken once and given that answer. A key
        Emudoc refuses, holding it for another request, is put aside for a new one."""
        key, before = self._key(carried)
        try:
            answer = patiently(
                lambda: self.api.call(
                    "POST",
                    f"/api/v1/projects/{self.project.id}/documents",
                    body=body,
                    content_type=content_type,
                    key=key,
                ),
                self.api.sleep,
                self.api.say,
                self.tries,
            )
        except Invalid:
            self._answered(key)
            if not before:
                raise
            return self._post(carried, body, content_type)
        except (Unusable, TooLarge, PlanFull):
            self._answered(key)
            raise
        # Without an answer (Emudoc out of reach, Ctrl+C) the key stays for the files sent again;
        # with one it goes, saved with what the answer settles.
        del self.unanswered[key]
        return answer

    def _key(self, carried: list[list[str]]) -> tuple[str, bool]:
        """The Idempotency-Key of a request carrying these files, and whether it was sent
        before: that of a request whose answer never came that carried the same files in the
        same order, unless Emudoc no longer keeps its answer, else a new one, saved before it
        is sent."""
        now = datetime.now(timezone.utc)
        for key, request in list(self.unanswered.items()):
            if now - datetime.fromisoformat(request["at"]) > timedelta(seconds=KEPT):
                del self.unanswered[key]
            elif request["files"] == carried:
                return key, True
        key = str(uuid.uuid4())
        self.unanswered[key] = {"files": carried, "at": now.isoformat(timespec="seconds")}
        if self.state is not None:
            self.state.save()
        return key, False

    def _answered(self, key: str) -> None:
        del self.unanswered[key]
        if self.state is not None:
            self.state.save()

    def _read(self, local: Local, sent: Sent) -> tuple[Local, bytes, str] | None:
        try:
            data = local.path.read_bytes()
        except OSError as error:
            sent.unread.append((local, f"It could not be read ({error.strerror or error})."))
            return None
        digest = hashlib.sha256(data).hexdigest()
        sent.digests[local.path] = digest
        if self.state is not None and self.state.holds(digest):
            sent.before.append(local)
            return None
        return local, data, digest

    def _settle(
        self, loaded: list[tuple[Local, bytes, str]], result: Mapping[str, Any], sent: Sent
    ) -> None:
        """Each file sent as Emudoc answered it: its refusals name their files by their place in
        the request, and the documents kept are the others, in order."""
        refusals = {refusal.get("index"): refusal for refusal in result["rejected"]}
        if None in refusals:
            raise Unusable(
                "This Emudoc does not say which of the files it refused: download the client "
                "again from the organisation's page, under API keys."
            )
        documents = iter(result["documents"])
        kept: list[str] = []
        made: list[int] = []
        for index, (local, _, digest) in enumerate(loaded):
            refusal = refusals.get(index)
            if refusal is None:
                document = next(documents)
                sent.kept.append(local)
                kept.append(local.name)
                made.append(document["id"])
                if self.state is not None:
                    self.state.keep(digest, local.name, document["id"])
            elif refusal.get("duplicate_of") is not None:
                sent.present.append((local, refusal["reason"]))
                if self.state is not None:
                    self.state.keep(digest, local.name, refusal["duplicate_of"])
            elif refusal.get("limit") is not None:
                sent.plan = refusal["reason"]
                sent.waiting.append(local)
            else:
                sent.refused.append((local, refusal["reason"]))
        if made:
            sent.uploads.append(Upload(made, kept))


def clock_time(moment: datetime | None = None) -> str:
    """A moment as the computer's clock shows it, to the minute."""
    return (moment or datetime.now()).astimezone().strftime("%H:%M")


@dataclass
class Standing:
    """Where some documents stand, each counted once: read (partly or not), being read, waiting
    for Emudoc to try again a stage that failed for a reason that passes, or failed. One deleted
    since is not counted."""

    documents: int = 0
    ready: int = 0
    # Of the ready, those that went without their summaries and facts or their vectors.
    partly_indexed: int = 0
    indexing: int = 0
    retrying: int = 0
    failed: int = 0
    # When the soonest retry is due, while one waits.
    retry_at: datetime | None = None
    # Each that failed, by its name, with why.
    failures: list[tuple[str, str]] = field(default_factory=list)

    def count(self, document: Mapping[str, Any]) -> None:
        """One more document, as the API words it."""
        self.documents += 1
        if document.get("retry_at"):
            self.retrying += 1
            # Python 3.9 reads no Z.
            when = datetime.fromisoformat(document["retry_at"].replace("Z", "+00:00"))
            self.retry_at = min(self.retry_at or when, when)
        elif document["status"] == "ready":
            self.ready += 1
            self.partly_indexed += bool(document.get("partly_indexed"))
        elif document["status"] in ("queued", "processing"):
            self.indexing += 1
        else:
            self.failed += 1
            self.failures.append((document["filename"], document.get("error") or NOT_INDEXED))


def standing(api: Api, project: Project, documents: Sequence[int]) -> Standing:
    """Where the documents stand, asked in as many requests as their number takes (GET
    /api/v1/projects/{id}/documents with their ids)."""
    now = Standing()
    for start in range(0, len(documents), MOST):
        ids = [("ids", document) for document in documents[start : start + MOST]]
        for document in every(api, f"/api/v1/projects/{project.id}/documents", ids):
            now.count(document)
    now.failures.sort()
    return now


def progress(now: Standing) -> str:
    line = f"Reading: {now.ready} of {now.documents} ready"
    if now.failed:
        line += f", {now.failed} could not be read"
    return line + "."


def outcome(now: Standing, of: str) -> list[str]:
    """What became of documents once nothing of theirs is being read, `of` naming them."""
    if now.documents == 0:
        return [f"Deleted before being read: {of}."]
    if now.ready == now.documents and not now.partly_indexed:
        return [f"Read {of}."]
    parts = [f"{now.ready} {'is' if now.ready == 1 else 'are'} read"]
    if now.partly_indexed:
        parts[0] += f", {now.partly_indexed} of them partly (the library says what each lacks)"
    if now.failed:
        parts.append(f"{now.failed} could not be read")
    if now.retrying:
        waits = "waits" if now.retrying == 1 else "wait"
        parts.append(
            f"{now.retrying} {waits} for Emudoc to try again at {clock_time(now.retry_at)}"
        )
    lines = [f"Of {of}, {'; '.join(parts)}."]
    lines.extend(f"  {name}: {reason}" for name, reason in now.failures[:NAMED])
    if now.failed > NAMED:
        lines.append(f"  and {now.failed - NAMED} more, which the library lists.")
    return lines


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def listed(rows: Sequence[tuple[str, str]]) -> list[str]:
    lines = [f"  {name}: {reason}" for name, reason in rows[:NAMED]]
    if len(rows) > NAMED:
        lines.append(f"  and {len(rows) - NAMED} more.")
    return lines


def report(sent: Sent, left: Sequence[tuple[Local, str]]) -> list[str]:
    """What `send` says once its files are sent: what went in, and each file that did not."""
    lines: list[str] = []
    if sent.kept:
        lines.append(
            f"Sent {_plural(len(sent.kept), 'file')} ({_plural(len(sent.uploads), 'upload')})."
        )
    if sent.before:
        lines.append(f"{_plural(len(sent.before), 'file')} sent before, as the state file says.")
    if sent.present:
        lines.append(f"{_plural(len(sent.present), 'file')} in the project already:")
        lines.extend(listed([(local.name, reason) for local, reason in sent.present]))
    if left:
        lines.append(f"Left out {_plural(len(left), 'file')}:")
        lines.extend(listed([(local.name, reason) for local, reason in left]))
    if sent.refused:
        lines.append(f"Refused {_plural(len(sent.refused), 'file')}:")
        lines.extend(listed([(local.name, reason) for local, reason in sent.refused]))
    if sent.unread:
        lines.append(f"Could not read {_plural(len(sent.unread), 'file')}:")
        lines.extend(listed([(local.name, reason) for local, reason in sent.unread]))
    if sent.waiting:
        lines.append(f"{_plural(len(sent.waiting), 'file')} not sent: {sent.plan}")
    return lines


class Printer:
    """The client's lines: what it does on standard output, each stamped with the time while it
    watches, and what stops it on standard error."""

    def __init__(self, out: IO[str], err: IO[str], stamped: bool) -> None:
        self.out = out
        self.err = err
        self.stamped = stamped

    def say(self, line: str) -> None:
        if self.stamped:
            line = f"{datetime.now().strftime('%H:%M:%S')} {line}"
        print(line, file=self.out, flush=True)

    def fail(self, line: str) -> None:
        print(f"emudoc_upload: {line}", file=self.err, flush=True)


def folders_of(into: str) -> tuple[str, ...]:
    return tuple(part for part in re.split(r"[/\\]", into) if part.strip())


def where(project: Project, into: tuple[str, ...]) -> str:
    return f"{project.name}" + (f", in {SEPARATOR.join(into)}" if into else "")


def send(
    api: Api,
    arguments: argparse.Namespace,
    into: tuple[str, ...],
    printer: Printer,
) -> int:
    say = printer.say
    project, limits = connect(api, arguments.project, api.sleep, say)
    state = None
    if arguments.state is not None:
        state = State(arguments.state, api.server, project)
        state.save()
    unreadable: list[tuple[str, str]] = []
    files, left = screen(
        gather(
            arguments.paths,
            arguments.state.resolve() if arguments.state is not None else None,
            lambda path, error: unreadable.append((path, str(error.strerror or error))),
        ),
        limits,
    )
    sent = Sent()
    if files:
        say(f"Sending {_plural(len(files), 'file')} into {where(project, into)}.")
    try:
        Sender(api, project, limits, into, state, TRIES).send(files, sent)
    except KeyboardInterrupt:
        for line in report(sent, left):
            say(line)
        say("Stopped before every file was sent.")
        return 130
    except Unavailable as error:
        for line in report(sent, left):
            say(line)
        printer.fail(f"{error} The files not listed above as sent were not.")
        return 2
    lines = report(sent, left)
    if unreadable:
        what = "file or folder" if len(unreadable) == 1 else "files or folders"
        lines.append(f"Could not look at {len(unreadable)} {what}:")
        lines.extend(listed(unreadable))
    if not files and not left:
        lines.append("Nothing to send.")
    for line in lines:
        say(line)
    failed = False
    if sent.uploads and not arguments.no_wait:
        try:
            documents = [document for upload in sent.uploads for document in upload.documents]
            now = follow(api, project, documents, say)
        except KeyboardInterrupt:
            say("Stopped following: Emudoc goes on reading the files sent.")
            return 130
        except Unavailable as error:
            printer.fail(f"{error} The files are sent; the library shows how far they are read.")
            return 2
        sent_words = "the file sent" if len(sent.kept) == 1 else f"the {len(sent.kept)} files sent"
        for line in outcome(now, sent_words):
            say(line)
        failed = now.failed > 0
    trouble = left or sent.refused or sent.unread or sent.waiting or unreadable or failed
    return 1 if trouble else 0


def follow(
    api: Api, project: Project, documents: Sequence[int], say: Callable[[str], None]
) -> Standing:
    """Where the documents stand once none is being read, saying how far they are whenever that
    changes."""
    said = None
    while True:
        now = patiently(lambda: standing(api, project, documents), api.sleep, say, TRIES)
        if now.indexing == 0:
            return now
        line = progress(now)
        if line != said:
            say(line)
            said = line
        api.sleep(FOLLOW_EVERY)


class Watcher:
    """A folder looked at again and again: a file is sent once its size and time stayed the same
    from one look to the next, and each upload is followed until its documents are read. What
    it cannot do now (Emudoc out of reach, the plan full) it tries again at a later look."""

    def __init__(
        self,
        folder: Path,
        sender: Sender,
        state: State,
        say: Callable[[str], None],
    ) -> None:
        self.folder = folder
        self.sender = sender
        self.state = state
        self.say = say
        self.skip = state.path.resolve()
        # Each file's size and time at the last look, and when it was last read, its SHA-256.
        self.looked: dict[Path, tuple[int, int]] = {}
        self.read: dict[Path, tuple[tuple[int, int], str]] = {}
        # What was said once, so that a look says it no more: the files refused, by SHA-256,
        # tried again when the client starts again; those left out or that could not be read,
        # by their version; the folders it could not look into.
        self.refused: set[str] = set()
        self.said: dict[Path, tuple[int, int]] = {}
        self.unreadable: set[str] = set()
        self.uploads: list[Upload] = []
        self.paused_until = 0.0

    def look(self, now: float) -> None:
        seen: dict[Path, tuple[int, int]] = {}
        ready: list[Local] = []
        for local in walk(self.folder.resolve(), (), self.skip, self._unreadable):
            seen[local.path] = local.version
            if self.looked.get(local.path) != local.version or local.size == 0:
                continue
            known = self.read.get(local.path)
            if known and known[0] == local.version:
                if self.state.holds(known[1]) or known[1] in self.refused:
                    continue
            ready.append(local)
        self.looked = seen
        ready, left = screen(ready, self.sender.limits)
        for local, reason in left:
            self._once(local, f"Left out {local.name}: {reason}")
        if ready and now >= self.paused_until:
            sent = Sent()
            try:
                self.sender.send(ready, sent)
            except Unavailable as error:
                self.say(f"{error} Trying again at the next look.")
                return
            finally:
                self._heard(sent, now)
        self._follow()

    def _heard(self, sent: Sent, now: float) -> None:
        """What a look's sending came to, said, and noted so that no later look sends a file
        again that Emudoc kept, held already or refused."""
        for local in [*sent.kept, *sent.before, *(local for local, _ in sent.present)]:
            self.read[local.path] = (local.version, sent.digests[local.path])
        for upload in sent.uploads:
            if upload.names:
                self.say(f"Sent {_named(upload.names)}.")
        self.uploads.extend(sent.uploads)
        for local, reason in sent.present:
            self.say(f"{local.name}: {reason}")
        for local, reason in sent.refused:
            if local.path in sent.digests:
                self.read[local.path] = (local.version, sent.digests[local.path])
                self.refused.add(sent.digests[local.path])
            self.say(f"Refused {local.name}: {reason}")
        for local, reason in sent.unread:
            self._once(local, f"Could not read {local.name}: {reason}")
        if sent.waiting:
            self.paused_until = now + PLAN_PAUSE
            again = clock_time(datetime.now() + timedelta(seconds=PLAN_PAUSE))
            self.say(
                f"{sent.plan} {_plural(len(sent.waiting), 'file')} waiting, tried again at {again}."
            )

    def _once(self, local: Local, line: str) -> None:
        if self.said.get(local.path) != local.version:
            self.said[local.path] = local.version
            self.say(line)

    def _follow(self) -> None:
        for upload in list(self.uploads):
            try:
                now = standing(self.sender.api, self.sender.project, upload.documents)
            except Unavailable:
                return
            if now.indexing or now.retrying:
                continue
            self.uploads.remove(upload)
            for line in outcome(now, _named(upload.names)):
                self.say(line)

    def _unreadable(self, path: str, error: OSError) -> None:
        if path not in self.unreadable:
            self.unreadable.add(path)
            self.say(f"Could not look at {path} ({error.strerror or error}).")


def _named(names: Sequence[str]) -> str:
    if len(names) == 1:
        return names[0]
    shown = ", ".join(names[:3])
    return f"{len(names)} files ({shown}{', …' if len(names) > 3 else ''})"


def watch(
    api: Api,
    arguments: argparse.Namespace,
    into: tuple[str, ...],
    printer: Printer,
    clock: Callable[[], float],
) -> int:
    folder: Path = arguments.folder
    if not folder.is_dir():
        raise Unusable(f"{folder} is not a folder.")
    project, limits = connect(api, arguments.project, api.sleep, printer.say)
    state = State(arguments.state or folder / STATE_NAME, api.server, project)
    state.save()
    watcher = Watcher(folder, Sender(api, project, limits, into, state, 1), state, printer.say)
    printer.say(
        f"Watching {folder} for {where(project, into)}: each file is sent once it has stayed the "
        f"same for {arguments.every:g} seconds. Ctrl+C stops."
    )
    try:
        while True:
            watcher.look(clock())
            api.sleep(arguments.every)
    except KeyboardInterrupt:
        printer.say("Stopped watching.")
        return 0


def _seconds(text: str) -> float:
    seconds = float(text)
    if not 1 <= seconds <= 3600:
        raise argparse.ArgumentTypeError("give a number of seconds from 1 to 3600")
    return seconds


def parser() -> argparse.ArgumentParser:
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument(
        "--server",
        help=f"Emudoc's address, such as https://emudoc.example.org (or {SERVER_VARIABLE})",
    )
    shared.add_argument(
        "--project", required=True, help="the project's name, or its number, from its address"
    )
    shared.add_argument(
        "--into",
        default="",
        metavar="FOLDER",
        help="the project's folder to file them in, such as Scans/2026, made where it is not",
    )
    top = argparse.ArgumentParser(
        prog="emudoc_upload.py",
        description="Send files into an Emudoc project, or each file a scanner saves to a folder.",
        epilog=f"The API key, one that reads and writes, is read from {KEY_VARIABLE}.",
    )
    commands = top.add_subparsers(dest="command", required=True, metavar="COMMAND")
    sending = commands.add_parser(
        "send",
        parents=[shared],
        help="send files and folders, then follow how far Emudoc has read them",
        description="Send files and folders into a project, each folder filed as it is.",
    )
    sending.add_argument("paths", nargs="+", type=Path, metavar="PATH")
    sending.add_argument(
        "--state", type=Path, help="a file that keeps what was sent, so that it is not sent again"
    )
    sending.add_argument(
        "--no-wait", action="store_true", help="end once the files are sent, without following"
    )
    watching = commands.add_parser(
        "watch",
        parents=[shared],
        help="send each file a folder receives, once it has stopped changing",
        description="Send each file the folder receives once it has stopped changing, until "
        "Ctrl+C.",
    )
    watching.add_argument("folder", type=Path, metavar="FOLDER")
    watching.add_argument(
        "--every",
        type=_seconds,
        default=EVERY,
        metavar="SECONDS",
        help=f"how often to look at the folder (default {EVERY:g})",
    )
    watching.add_argument(
        "--state",
        type=Path,
        help=f"the file that keeps what was sent (default {STATE_NAME} in the folder)",
    )
    return top


def main(
    argv: Sequence[str] | None = None,
    *,
    transport: Transport | None = None,
    environ: Mapping[str, str] = os.environ,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    out: IO[str] | None = None,
    err: IO[str] | None = None,
) -> int:
    """The command, with what it reaches the world through, which a test replaces."""
    arguments = parser().parse_args(argv)
    printer = Printer(out or sys.stdout, err or sys.stderr, stamped=arguments.command == "watch")
    if sys.version_info < OLDEST_PYTHON:
        printer.fail(f"The client needs Python {'.'.join(map(str, OLDEST_PYTHON))} or later.")
        return 2
    try:
        key = environ.get(KEY_VARIABLE, "").strip()
        if not key:
            raise Unusable(
                f"Set {KEY_VARIABLE} to an API key that reads and writes: an owner or admin of "
                "the organisation makes one on its page, under API keys."
            )
        server = address(arguments.server or environ.get(SERVER_VARIABLE, ""))
        api = Api(server, key, transport or over_http(), sleep, printer.say)
        into = folders_of(arguments.into)
        if arguments.command == "send":
            return send(api, arguments, into, printer)
        return watch(api, arguments, into, printer, clock)
    except (Unusable, Unavailable) as error:
        printer.fail(str(error))
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        # A name the console cannot show is shown as it can, rather than stopping the client.
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    sys.exit(main())
