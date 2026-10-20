"""A member as the load test plays one: the page's requests, timed as the member waits for
them. Signed in once; every coroutine of a phase shares the member's client and session."""

import asyncio
import json
import mimetypes
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

# The page sends it with every change (`send()` in src/api.ts), the CSRF defence asks for it.
HEADERS = {"X-Requested-With": "loadtest"}
# How the page follows an answer again after a drop (src/chat.ts).
RECONNECTS = 5
RECONNECT_WAIT = 1.0


@dataclass
class Answer:
    """How long a member waited for each part of an answer, in seconds from the question."""

    asked: float
    first_event: float | None
    first_text: float | None
    ended: float
    status: str
    # How often the stream ended without its end event and was followed again.
    drops: int = 0


class Member:
    def __init__(self, base: str, connections: int = 64):
        self.client = httpx.AsyncClient(
            base_url=base,
            headers=HEADERS,
            timeout=httpx.Timeout(600, connect=10),
            limits=httpx.Limits(max_connections=connections, max_keepalive_connections=connections),
            follow_redirects=True,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def sign_up(self, email: str, link_of: Callable[[str], str]) -> dict:
        """Ask for an account, then make it on the page the link mailed to `email` opens, as a
        person does; `link_of` finds the link wherever the run's mail goes."""
        version = (await self._ok("GET", "/api/legal")).json()["version"]
        await self._ok("POST", "/api/auth/signup", json={"email": email})
        link = await asyncio.to_thread(link_of, email)
        body = {
            "token": parse_qs(urlsplit(link).fragment)["token"][0],
            "name": "Load",
            "password": "a load test's password",
            "terms_version": version,
        }
        return (await self._ok("POST", "/api/auth/signup/finish", json=body)).json()

    async def project(self, name: str) -> int:
        return (await self._ok("POST", "/api/projects", json={"name": name})).json()["id"]

    async def upload(self, project_id: int, paths: list[Path]) -> tuple[float, dict]:
        """The seconds the upload of `paths` in one request took, and its result."""
        files = [
            ("files", (path.name, path.read_bytes(), mimetypes.guess_type(path.name)[0]))
            for path in paths
        ]
        began = time.perf_counter()
        response = await self._ok("POST", f"/api/projects/{project_id}/documents", files=files)
        return time.perf_counter() - began, response.json()

    async def timed_get(self, url: str, **params: object) -> float:
        began = time.perf_counter()
        await self._ok("GET", url, params=params)
        return time.perf_counter() - began

    async def chat(self, project_id: int) -> int:
        return (await self._ok("POST", f"/api/projects/{project_id}/chats")).json()["id"]

    async def ask(self, chat_id: int, question: str) -> Answer:
        """Ask, then follow the answer's events to their end as the page does (`follow` in
        src/chat.ts): a stream that ends without an end event is followed again from the last
        event seen, after a second times the drops in a row, until RECONNECTS in a row."""
        began = time.perf_counter()
        response = await self._ok(
            "POST", f"/api/chats/{chat_id}/messages", json={"content": question}
        )
        asked = time.perf_counter() - began
        answer_id = response.json()["answer"]["id"]
        first_event = first_text = None
        after: str | None = None
        drops = in_row = 0
        url = f"/api/chats/{chat_id}/messages/{answer_id}/events"
        while True:
            headers = {"Last-Event-ID": after} if after is not None else {}
            async with self.client.stream("GET", url, headers=headers) as stream:
                async for line in stream.aiter_lines():
                    if line.startswith("id: "):
                        after = line.removeprefix("id: ")
                    if not line.startswith("data: "):
                        continue
                    event = json.loads(line.removeprefix("data: "))
                    in_row = 0
                    now = time.perf_counter() - began
                    if first_event is None:
                        first_event = now
                    if event["type"] == "text" and first_text is None:
                        first_text = now
                    if event["type"] == "end":
                        ended = time.perf_counter() - began
                        return Answer(asked, first_event, first_text, ended, event["status"], drops)
            if in_row == RECONNECTS:
                ended = time.perf_counter() - began
                return Answer(asked, first_event, first_text, ended, "lost", drops)
            drops, in_row = drops + 1, in_row + 1
            await asyncio.sleep(RECONNECT_WAIT * in_row)

    async def _ok(self, method: str, url: str, **options: object) -> httpx.Response:
        response = await self.client.request(method, url, **options)
        if response.status_code >= 400:
            raise RuntimeError(f"{method} {url} answered {response.status_code}: {response.text}")
        return response
