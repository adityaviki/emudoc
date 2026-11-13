"""A stand-in for the model, the embeddings endpoint and Google Drive, for the end-to-end test
(e2e/).

CI holds no key, and a real model answers differently from one run to the next, so the app is
pointed at this instead: an OpenAI-compatible server, the API that Ollama speaks, reached as
`ollama:standin` through OLLAMA_BASE_URL for both kinds of work and as EMBEDDING_BASE_URL for
the passages. It answers as a model would that does the least the app asks:

- an indexing request (a JSON schema in `response_format`) gets the smallest reply the schema
  takes: no sections, no facts, empty strings, which the pipeline reads as nothing learnt;
- an indexing request that lists the project's extraction templates (`<templates>`) gets the
  one whose kind the document's opening words hold, and the facts its "Name: value" lines
  state of that template's fields, so that a document of the kind is a row of its facts table
  and a field added to the template is found when the document is read again;
- an agent turn whose conversation has no tool result yet calls `search` with the question,
  streamed as the app streams every turn;
- an agent turn after a search answers with the first sentence of the best passage, cited by
  its [P...] reference;
- an agent turn offered `create_draft`, for a question that begins "Draft ", offers after its
  search a draft of the kind the question names (a reply email unless it says dispute, memo,
  table or report) about the best passage's document, the question its instructions, then says
  that it is offered;
- an agent turn offered `create_document`, for a question that begins "Write a Word document",
  writes after its search a document, "Notes on the invoice", of the best passage's first sentence
  and its reference into the member's My files, then says that it is there;
- an agent turn offered `create_spreadsheet`, for a question that begins "Make an Excel
  workbook", makes after its search a workbook, "Lines of the invoice", of a sheet holding the
  best passage's reference, its first sentence and a formula of that sentence's length, then says
  that it is there;
- an embedding is the text's words hashed into EMBEDDING_DIM dimensions, so a passage and a
  question that share words are near each other.

`POST /hold` makes the next agent turn wait until `POST /release` (or HELD_FOR seconds), as a
model that thinks for long, so that the browser test stops an answer while it is thought about
and tries it again: the try's turns are not held, and the held one answers once released, into
an answer that is no longer its run's. `GET /held` says how many turns are waiting so, which the
test waits for before it stops the answer: one stopped before its turn reached the model would
leave the hold to the try. `POST /refuse-embeddings` with `{"containing": word}`
refuses the next embeddings request any of whose texts holds the word with a 400, which the app
does not try again, so that the browser test finds a document partly indexed and indexes the
rest of it. `POST /hooks` is a program's webhook (backend/app/webhooks.py): it keeps each event
with its signature and answers 204, and `GET /hooks` lists what it kept.

Under `/google` it is Google Drive as a connected folder reads it (backend/app/connectors/google.py,
reached through CONNECTOR_BASE_URL): its consent page sends the browser straight back with a code,
its token endpoint gives a grant for the code and an access token for the grant, and its Drive
API serves a tree of its own, My Drive holding the folder "Supplier files" with a credit note in
it and another in a folder below, whose change log never moves.

The load test (backend/loadtest) asks for more, with options the end-to-end test leaves off:
`--fill` answers an indexing request as a model that read it would (a scanned page gets text
made up from its picture, a document its title, kind, date, summary, section summaries and the
facts its "Name: value" lines state), so that the indexes it searches are as full as a real
model's; `--first-token` and `--words-per-second` make an agent turn take a hosted model's time,
and `--index-seconds` an indexing request.

Standard library only, so any Python 3 runs it:

    python3 e2e/standin.py --port 18001 [--dim 768] [--fill] [--first-token 1]
        [--words-per-second 40] [--index-seconds 0]
"""

import argparse
import hashlib
import json
import math
import random
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

# The longest a held agent turn waits for its release.
HELD_FOR = 60.0

WORD = re.compile(r"\w+")
CITED = re.compile(r"^(\[P\d+\]) .*\n(.+)", re.MULTILINE)
SENTENCE = re.compile(r"(.+?[.!?])(\s|$)")
# The document of a search's best passage, and the kind of draft a question's word asks for.
HIT = re.compile(r"^\[P\d+\] doc (\d+)", re.MULTILINE)
DRAFT_WORDS = (
    ("dispute", "dispute_letter"),
    ("memo", "summary_memo"),
    ("table", "comparison_table"),
    ("report", "report"),
)
OFFERED = "The member is offered a"
KEPT = "in the member's My files as file"

# Google Drive's tree under /google: each item by its id, its name, its type, its parent, and a
# file's bytes.
DRIVE_FOLDER = "application/vnd.google-apps.folder"
DRIVE = {
    "root": {"name": "My Drive", "mimeType": DRIVE_FOLDER, "parent": None},
    "supplier-files": {"name": "Supplier files", "mimeType": DRIVE_FOLDER, "parent": "root"},
    "archive": {"name": "Archive", "mimeType": DRIVE_FOLDER, "parent": "supplier-files"},
    "credit-note": {
        "name": "credit-note-CN-12.md",
        "mimeType": "text/markdown",
        "parent": "supplier-files",
        "content": b"# Credit note CN-12\n\nNorthwind credits 120 EUR against INV-2026-114.\n",
    },
    "old-credit-note": {
        "name": "credit-note-CN-3.md",
        "mimeType": "text/markdown",
        "parent": "archive",
        "content": b"# Credit note CN-3\n\nNorthwind credits 30 EUR against INV-2026-090.\n",
    },
}
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
DRIVE_PARENT = re.compile(r"'([^']+)' in parents")

# What --fill reads in an indexing request (app/indexing/enrich.py writes them): a section's
# marker, a block's line, and a block that states a field.
MARKER = re.compile(r"^## \[S(\d+)\].*$", re.MULTILINE)
BLOCK_LINE = re.compile(r"^\[B(\d+)\](?: \(p\.\d+\))? (.+)$", re.MULTILINE)
FIELD = re.compile(r"^(?:[-*] )?([A-Z][A-Za-z ]{1,40}): (\S.{0,160})$")
ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
MONEY = re.compile(r"^(EUR|USD|GBP) ([\d,]+(?:\.\d+)?)$")
NUMBER = re.compile(r"^[\d,]+(?:\.\d+)?$")
KINDS = ("delivery note", "price list", "invoice", "agreement", "contract", "review", "email")
# The templates an indexing request lists, and each one's kind, which starts its line there
# (the fields' lines below it are indented).
TEMPLATES = re.compile(r"^<templates>\n(.*?)\n</templates>$", re.MULTILINE | re.DOTALL)
LISTED_KIND = re.compile(r"^(\S[^:\n]*): ", re.MULTILINE)
LISTED_FIELD = re.compile(r"^  - (\w+) \(")
# The words of a scanned page's made-up text: the stand-in cannot read a picture.
SCAN_WORDS = (
    "pallet carton crate delivered received signed checked damaged missing short late early "
    "warehouse dock bay driver carrier route depot order batch lot serial invoice note copy "
    "bolts nuts washers brackets hinges rivets screws anchors fittings valves seals gaskets "
    "steel zinc brass nylon stainless galvanised coated threaded hex flange countersunk "
    "quantity weight volume count unit box bag roll drum tray kilogram metre litre piece "
    "inspection remark condition agreed rejected returned credited replaced held quarantine "
    "monday tuesday wednesday thursday friday morning afternoon shift supervisor clerk"
).split()


def instance(schema: dict[str, Any], defs: dict[str, Any]) -> Any:
    """The smallest value `schema` takes: what a model returns that found nothing to say."""
    if "$ref" in schema:
        return instance(defs[schema["$ref"].rsplit("/", 1)[-1]], defs)
    if "anyOf" in schema:
        options = schema["anyOf"]
        if any(option.get("type") == "null" for option in options):
            return None
        return instance(options[0], defs)
    if "enum" in schema:
        return schema["enum"][0]
    if "const" in schema:
        return schema["const"]
    kind = schema.get("type")
    if kind == "object":
        properties = schema.get("properties", {})
        return {name: instance(properties[name], defs) for name in schema.get("required", [])}
    if kind == "array":
        return [instance(schema["items"], defs) for _ in range(schema.get("minItems", 0))]
    if kind == "string":
        return "x" * schema.get("minLength", 0)
    if kind in ("integer", "number"):
        return schema.get("minimum", 0)
    if kind == "boolean":
        return False
    return None


def filled(schema: dict[str, Any], defs: dict[str, Any], messages: list[dict[str, Any]]) -> Any:
    """What a model that read an indexing request returns (--fill): a scanned page's text, made
    up from a seed its picture gives, or what a document's blocks state."""
    reply = instance(schema, defs)
    properties = schema.get("properties", {})
    if "blocks" in properties:
        seed = hashlib.sha256(json.dumps(messages).encode()).hexdigest()
        return {"blocks": transcription(random.Random(seed))}
    prompt = "\n".join(text_of(m.get("content")) for m in messages if m.get("role") == "user")
    blocks = {int(number): text for number, text in BLOCK_LINE.findall(prompt)}
    texts = list(blocks.values())
    if "title" in properties:
        opening = " ".join(texts[:3]).lower()
        found = ISO_DATE.search(prompt)
        reply["title"] = texts[0][:120] if texts else ""
        reply["doc_type"] = next((kind for kind in KINDS if kind in opening), "document")
        reply["doc_date"] = found.group(0) if found else None
        reply["summary"] = " ".join(first_sentence(text) for text in texts[1:4])
    if "sections" in properties and "InferredSection" not in defs:
        reply["sections"] = section_summaries(prompt)
    if "facts" in properties:
        reply["facts"] = facts_of(blocks)
    return reply


def named(reply: Any, schema: dict[str, Any], messages: list[dict[str, Any]]) -> Any:
    """The reply to a request that lists the project's extraction templates, with the template
    whose kind the document's opening words hold, and the facts its "Name: value" lines state of
    that template's fields; any other reply as it is."""
    prompt = "\n".join(text_of(m.get("content")) for m in messages if m.get("role") == "user")
    listing = TEMPLATES.search(prompt)
    if "template" not in schema.get("properties", {}) or not listing:
        return reply
    blocks = {int(number): text for number, text in BLOCK_LINE.findall(prompt)}
    opening = " ".join(list(blocks.values())[:3]).lower()
    fields = listed_fields(listing.group(1))
    kind = next((kind for kind in fields if kind in opening), None)
    reply["template"] = kind
    if kind is not None:
        reply["facts"] = [fact for fact in facts_of(blocks) if fact["name"] in fields[kind]]
    return reply


def listed_fields(listing: str) -> dict[str, set[str]]:
    """Each template a listing names, by its kind, in the listing's order, with the names of its
    fields."""
    fields: dict[str, set[str]] = {}
    kind = None
    for line in listing.splitlines():
        if found := LISTED_FIELD.match(line):
            if kind is not None:
                fields[kind].add(found.group(1))
        elif found := LISTED_KIND.match(line):
            kind = found.group(1)
            fields[kind] = set()
    return fields


def transcription(rng: random.Random) -> list[dict[str, Any]]:
    """A scanned page's blocks as a model would transcribe them: a heading, a reference and a
    few paragraphs, about as many words as a printed page holds."""
    number = rng.randrange(10_000, 100_000)
    blocks = [
        {"kind": "heading", "level": 1, "text": f"Delivery record {number}"},
        {"kind": "list_item", "level": None, "text": f"Reference: DR-{number}"},
    ]
    for _ in range(rng.randint(4, 6)):
        sentences = [
            " ".join(rng.choice(SCAN_WORDS) for _ in range(rng.randint(8, 16))).capitalize() + "."
            for _ in range(rng.randint(4, 7))
        ]
        blocks.append({"kind": "paragraph", "level": None, "text": " ".join(sentences)})
    return blocks


def first_sentence(text: str) -> str:
    found = SENTENCE.match(text.strip())
    return " ".join((found.group(1) if found else text).split()[:30])


def section_summaries(prompt: str) -> list[dict[str, Any]]:
    """A summary of each section marked in the request: its first block's first sentence."""
    parts = MARKER.split(prompt)
    summaries = []
    for number, body in zip(parts[1::2], parts[2::2], strict=True):
        lines = BLOCK_LINE.findall(body)
        if lines:
            summaries.append({"section": int(number), "summary": first_sentence(lines[0][1])})
    return summaries


def facts_of(blocks: dict[int, str]) -> list[dict[str, Any]]:
    """A fact for each block that states a field ("Total amount: EUR 1,250.00"), at most the
    40 the app asks for."""
    facts: list[dict[str, Any]] = []
    for number, text in blocks.items():
        found = FIELD.match(text)
        if not found:
            continue
        label, value = found.groups()
        fact = {"type": "text", "number": None, "date": None, "currency": None}
        if money := MONEY.match(value):
            amount = float(money.group(2).replace(",", ""))
            fact.update(type="money", number=amount, currency=money.group(1))
        elif ISO_DATE.fullmatch(value):
            fact.update(type="date", date=value)
        elif NUMBER.match(value):
            fact.update(type="number", number=float(value.replace(",", "")))
        name = label.lower().replace(" ", "_")
        facts.append({**fact, "name": name, "value": value, "block": number, "quote": text})
        if len(facts) == 40:
            break
    return facts


def embedding(text: str, dim: int) -> list[float]:
    vector = [0.0] * dim
    for word in WORD.findall(text.lower()):
        vector[int(hashlib.sha256(word.encode()).hexdigest(), 16) % dim] += 1.0
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def text_of(content: Any) -> str:
    """A message's content as text: a string, or the text parts of a list of parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(part.get("text", "") for part in content if isinstance(part, dict))
    return ""


def agent_turn(messages: list[dict[str, Any]], tools: list[str]) -> dict[str, Any]:
    """What the model does next: a search for the question, a draft offered or a file written
    from its results when the question asks for one and the tool is offered, or the answer from
    them."""
    results = [text_of(m.get("content")) for m in messages if m.get("role") == "tool"]
    question = next(
        text_of(m.get("content")) for m in reversed(messages) if m.get("role") == "user"
    )
    if not results:
        return {"tool": "search", "arguments": {"query": question}}
    if "create_document" in tools and question.startswith("Write a Word document"):
        if KEPT in results[-1]:
            return {"text": "It is in your files."}
        if found := CITED.search(results[-1]):
            ref, passage = found.groups()
            sentence = SENTENCE.match(passage.strip())
            said = (sentence.group(1) if sentence else passage).strip()
            arguments = {"title": "Notes on the invoice", "markdown": f"# Notes\n\n{said} {ref}"}
            return {"tool": "create_document", "arguments": arguments}
    if "create_spreadsheet" in tools and question.startswith("Make an Excel workbook"):
        if KEPT in results[-1]:
            return {"text": "It is in your files."}
        if found := CITED.search(results[-1]):
            ref, passage = found.groups()
            sentence = SENTENCE.match(passage.strip())
            said = (sentence.group(1) if sentence else passage).strip()
            rows = [["Passage", "Words", "Length"], [ref, f"'{said}", "=LEN(B2)"]]
            sheets = [{"name": "Lines", "rows": rows}]
            arguments = {"title": "Lines of the invoice", "sheets": sheets}
            return {"tool": "create_spreadsheet", "arguments": arguments}
    if "create_draft" in tools and question.startswith("Draft "):
        if OFFERED in results[-1]:
            return {"text": "It is offered below as a draft for the project's drafts."}
        if hit := HIT.search(results[-1]):
            asked = question.lower()
            kind = next((kind for word, kind in DRAFT_WORDS if word in asked), "reply_email")
            arguments = {"kind": kind, "instructions": question, "document_ids": [int(hit[1])]}
            return {"tool": "create_draft", "arguments": arguments}
    found = CITED.search(results[-1])
    if not found:
        return {"text": "The documents do not say."}
    ref, passage = found.groups()
    sentence = SENTENCE.match(passage.strip())
    said = (sentence.group(1) if sentence else passage).strip()
    return {"text": f"{said} {ref}"}


class Handler(BaseHTTPRequestHandler):
    dim = 768
    fill = False
    # Seconds before an agent turn's first part, and the words a second its text streams at
    # (0: at once), as a hosted model takes; seconds an indexing request takes.
    first_token = 0.0
    words_per_second = 0.0
    index_seconds = 0.0
    protocol_version = "HTTP/1.1"
    # How many agent turns to come are held, how many are waiting so now, and what lets the held
    # ones go.
    holds = 0
    waiting = 0
    released = threading.Event()
    hold_lock = threading.Lock()
    # The word whose next embeddings request is refused, once.
    refusing: str | None = None
    # The webhooks' events received, each with its headers, as sent.
    hooks: list[dict[str, str]] = []

    def log_message(self, format: str, *args: Any) -> None:
        """Quiet: Playwright shows a server's output only when asked."""

    def do_GET(self) -> None:
        if self.path.startswith("/google/"):
            self._google_get()
        elif self.path.rstrip("/") in ("/health", "/v1/models"):
            self._json({"object": "list", "data": [{"id": "standin", "object": "model"}]})
        elif self.path.rstrip("/") == "/held":
            with Handler.hold_lock:
                self._json({"waiting": Handler.waiting})
        elif self.path.rstrip("/") == "/hooks":
            with Handler.hold_lock:
                self._json({"received": Handler.hooks})
        else:
            self._json({"error": {"message": f"No route {self.path}."}}, 404)

    def do_POST(self) -> None:
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.path.startswith("/google/"):
            self._google_post(parse_qs(raw.decode()))
            return
        if self.path.rstrip("/") == "/hooks":
            with Handler.hold_lock:
                Handler.hooks.append(
                    {
                        "event": self.headers.get("Emudoc-Event", ""),
                        "signature": self.headers.get("Emudoc-Signature", ""),
                        "body": raw.decode(),
                    }
                )
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = json.loads(raw or b"{}")
        if self.path.rstrip("/") == "/hold":
            with Handler.hold_lock:
                Handler.holds += 1
                Handler.released.clear()
            self._json({"holds": Handler.holds})
        elif self.path.rstrip("/") == "/release":
            with Handler.hold_lock:
                Handler.holds = 0
                Handler.released.set()
            self._json({"holds": 0})
        elif self.path.rstrip("/") == "/refuse-embeddings":
            with Handler.hold_lock:
                Handler.refusing = body["containing"]
            self._json({"refusing": Handler.refusing})
        elif self.path.endswith("/embeddings"):
            inputs = body["input"] if isinstance(body["input"], list) else [body["input"]]
            with Handler.hold_lock:
                refused = Handler.refusing is not None and any(
                    Handler.refusing in text for text in inputs
                )
                if refused:
                    Handler.refusing = None
            if refused:
                self._json({"error": {"message": "Refused, as asked.", "type": "test"}}, 400)
                return
            words = sum(len(WORD.findall(text)) for text in inputs)
            self._json(
                {
                    "object": "list",
                    "model": body.get("model"),
                    "data": [
                        {"object": "embedding", "index": i, "embedding": embedding(t, self.dim)}
                        for i, t in enumerate(inputs)
                    ],
                    "usage": {"prompt_tokens": words, "total_tokens": words},
                }
            )
        elif self.path.endswith("/chat/completions"):
            self._chat(body)
        else:
            self._json({"error": {"message": f"No route {self.path}."}}, 404)

    def _google_get(self) -> None:
        """Google's consent page, which consents at once, and Drive's API over DRIVE."""
        url = urlsplit(self.path)
        query = {name: values[0] for name, values in parse_qs(url.query).items()}
        if url.path == "/google/auth":
            given = urlencode({"code": "standin-code", "state": query["state"]})
            back = f"{query['redirect_uri']}?{given}"
            self.send_response(302)
            self.send_header("Location", back)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        api = url.path.removeprefix("/google/drive/v3")
        if api == "/about":
            self._json({"user": {"emailAddress": "member@drive.example"}})
        elif api == "/drives":
            self._json({"drives": []})
        elif api == "/changes/startPageToken":
            self._json({"startPageToken": "1"})
        elif api == "/changes":
            self._json({"changes": [], "newStartPageToken": "1"})
        elif api == "/files":
            parent = DRIVE_PARENT.search(query.get("q", ""))
            folders_only = f"mimeType = '{DRIVE_FOLDER}'" in query.get("q", "")
            found = [
                drive_item(ref)
                for ref, item in DRIVE.items()
                if parent
                and item["parent"] == parent.group(1)
                and (not folders_only or item["mimeType"] == DRIVE_FOLDER)
            ]
            self._json({"files": sorted(found, key=lambda item: item["name"])})
        elif (ref := api.removeprefix("/files/")) in DRIVE:
            if query.get("alt") == "media":
                data = DRIVE[ref]["content"]
                self.send_response(200)
                self.send_header("Content-Type", DRIVE[ref]["mimeType"])
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self._json({**drive_item(ref), "trashed": False})
        else:
            self._json({"error": {"code": 404, "errors": [{"reason": "notFound"}]}}, 404)

    def _google_post(self, form: dict[str, list[str]]) -> None:
        """Google's token endpoint, and its revocation, which takes back nothing."""
        if self.path == "/google/token" and form.get("grant_type") == ["authorization_code"]:
            self._json(
                {
                    "access_token": "standin-access",
                    "refresh_token": "standin-refresh",
                    "scope": DRIVE_SCOPE,
                    "expires_in": 3599,
                }
            )
        elif self.path == "/google/token":
            self._json({"access_token": "standin-access", "expires_in": 3599})
        else:
            self._json({})

    def _chat(self, body: dict[str, Any]) -> None:
        format_ = body.get("response_format") or {}
        if format_.get("type") == "json_schema":
            schema = format_["json_schema"]["schema"]
            defs = schema.get("$defs", {})
            messages = body.get("messages", [])
            made = named(
                filled(schema, defs, messages) if self.fill else instance(schema, defs),
                schema,
                messages,
            )
            reply: dict[str, Any] = {"text": json.dumps(made)}
            time.sleep(self.index_seconds)
        elif body.get("tools"):
            with Handler.hold_lock:
                held = Handler.holds > 0
                if held:
                    Handler.holds -= 1
                    Handler.waiting += 1
            if held:
                Handler.released.wait(HELD_FOR)
                with Handler.hold_lock:
                    Handler.waiting -= 1
            offered = [tool.get("function", {}).get("name") for tool in body["tools"]]
            reply = agent_turn(body.get("messages", []), offered)
            time.sleep(self.first_token)
        else:
            reply = {"text": "OK."}
        if body.get("stream"):
            self._stream(body, reply)
        else:
            self._json(completion(body, reply))

    def _json(self, payload: Any, status: int = 200) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _stream(self, body: dict[str, Any], reply: dict[str, Any]) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        base = {
            "id": "chatcmpl-standin",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": body.get("model", "standin"),
        }
        calls = (
            [
                {
                    "index": 0,
                    "id": f"call_{int(time.time() * 1000)}",
                    "type": "function",
                    "function": {
                        "name": reply["tool"],
                        "arguments": json.dumps(reply["arguments"]),
                    },
                }
            ]
            if "tool" in reply
            else []
        )
        deltas: list[dict[str, Any]]
        if calls:
            deltas, finish = [{"role": "assistant", "tool_calls": calls}], "tool_calls"
        else:
            # Word by word, as a model streams, so the page shows an answer growing.
            words = re.findall(r"\S+\s*", reply["text"])
            deltas = [{"role": "assistant", "content": ""}] + [{"content": w} for w in words]
            finish = "stop"
        chunks = [{**base, "choices": [{"index": 0, "delta": d}]} for d in deltas]
        chunks.append({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]})
        chunks.append({**base, "choices": [], "usage": usage()})
        for chunk in chunks:
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.flush()
            if (
                self.words_per_second
                and chunk["choices"]
                and chunk["choices"][0]["delta"].get("content")
            ):
                time.sleep(1 / self.words_per_second)
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        self.close_connection = True


def usage() -> dict[str, int]:
    return {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


def completion(body: dict[str, Any], reply: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "chatcmpl-standin",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.get("model", "standin"),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": reply.get("text", "")},
                "finish_reason": "stop",
            }
        ],
        "usage": usage(),
    }


def drive_item(ref: str) -> dict[str, Any]:
    """An item of DRIVE as Drive's API lists it."""
    item = DRIVE[ref]
    shown = {"id": ref, "name": item["name"], "mimeType": item["mimeType"]}
    if "content" in item:
        shown["md5Checksum"] = hashlib.md5(item["content"]).hexdigest()
        shown["size"] = str(len(item["content"]))
    shown["modifiedTime"] = "2026-10-24T09:00:00Z"
    return shown


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18001)
    parser.add_argument("--dim", type=int, default=768, help="EMBEDDING_DIM of the app")
    parser.add_argument(
        "--fill", action="store_true", help="answer indexing requests with what they hold"
    )
    parser.add_argument(
        "--first-token", type=float, default=0.0, help="seconds before an agent turn begins"
    )
    parser.add_argument(
        "--words-per-second", type=float, default=0.0, help="an answer's pace (0: at once)"
    )
    parser.add_argument(
        "--index-seconds", type=float, default=0.0, help="seconds an indexing request takes"
    )
    args = parser.parse_args()
    Handler.dim = args.dim
    Handler.fill = args.fill
    Handler.first_token = args.first_token
    Handler.words_per_second = args.words_per_second
    Handler.index_seconds = args.index_seconds
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
