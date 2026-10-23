"""A stand-in for the model and the embeddings endpoint, for the end-to-end test (e2e/).

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
- an embedding is the text's words hashed into EMBEDDING_DIM dimensions, so a passage and a
  question that share words are near each other.

`POST /hold` makes the next agent turn wait until `POST /release` (or HELD_FOR seconds), as a
model that thinks for long, so that the browser test stops an answer while it is thought about
and tries it again: the try's turns are not held, and the held one answers once released, into
an answer that is no longer its run's. `POST /refuse-embeddings` with `{"containing": word}`
refuses the next embeddings request any of whose texts holds the word with a 400, which the app
does not try again, so that the browser test finds a document partly indexed and indexes the
rest of it.

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

# The longest a held agent turn waits for its release.
HELD_FOR = 60.0

WORD = re.compile(r"\w+")
CITED = re.compile(r"^(\[P\d+\]) .*\n(.+)", re.MULTILINE)
SENTENCE = re.compile(r"(.+?[.!?])(\s|$)")

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


def agent_turn(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """What the model does next: a search for the question, or the answer from its results."""
    results = [text_of(m.get("content")) for m in messages if m.get("role") == "tool"]
    if not results:
        question = next(
            text_of(m.get("content")) for m in reversed(messages) if m.get("role") == "user"
        )
        return {"tool": "search", "arguments": {"query": question}}
    found = CITED.search(results[-1])
    if not found:
        return {"text": "The documents do not say."}
    ref, passage = found.groups()
    sentence = SENTENCE.match(passage.strip())
    return {"text": f"{(sentence.group(1) if sentence else passage).strip()} {ref}"}


class Handler(BaseHTTPRequestHandler):
    dim = 768
    fill = False
    # Seconds before an agent turn's first part, and the words a second its text streams at
    # (0: at once), as a hosted model takes; seconds an indexing request takes.
    first_token = 0.0
    words_per_second = 0.0
    index_seconds = 0.0
    protocol_version = "HTTP/1.1"
    # How many agent turns to come are held, and what lets the held ones go.
    holds = 0
    released = threading.Event()
    hold_lock = threading.Lock()
    # The word whose next embeddings request is refused, once.
    refusing: str | None = None

    def log_message(self, format: str, *args: Any) -> None:
        """Quiet: Playwright shows a server's output only when asked."""

    def do_GET(self) -> None:
        if self.path.rstrip("/") in ("/health", "/v1/models"):
            self._json({"object": "list", "data": [{"id": "standin", "object": "model"}]})
        else:
            self._json({"error": {"message": f"No route {self.path}."}}, 404)

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
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
            if held:
                Handler.released.wait(HELD_FOR)
            reply = agent_turn(body.get("messages", []))
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
        if "tool" in reply:
            delta: dict[str, Any] = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": f"call_{int(time.time() * 1000)}",
                        "type": "function",
                        "function": {
                            "name": reply["tool"],
                            "arguments": json.dumps(reply["arguments"]),
                        },
                    }
                ],
            }
            deltas, finish = [delta], "tool_calls"
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
