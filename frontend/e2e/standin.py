"""A stand-in for the model and the embeddings endpoint, for the end-to-end test (e2e/).

CI holds no key, and a real model answers differently from one run to the next, so the app is
pointed at this instead: an OpenAI-compatible server, the API that Ollama speaks, reached as
`ollama:standin` through OLLAMA_BASE_URL for both kinds of work and as EMBEDDING_BASE_URL for
the passages. It answers as a model would that does the least the app asks:

- an indexing request (a JSON schema in `response_format`) gets the smallest reply the schema
  takes: no sections, no facts, empty strings, which the pipeline reads as nothing learnt;
- an agent turn whose conversation has no tool result yet calls `search` with the question,
  streamed as the app streams every turn;
- an agent turn after a search answers with the first sentence of the best passage, cited by
  its [P...] reference;
- an embedding is the text's words hashed into EMBEDDING_DIM dimensions, so a passage and a
  question that share words are near each other.

Standard library only, so any Python 3 runs it:

    python3 e2e/standin.py --port 18001 [--dim 768]
"""

import argparse
import hashlib
import json
import math
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

WORD = re.compile(r"\w+")
CITED = re.compile(r"^(\[P\d+\]) .*\n(.+)", re.MULTILINE)
SENTENCE = re.compile(r"(.+?[.!?])(\s|$)")


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
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        """Quiet: Playwright shows a server's output only when asked."""

    def do_GET(self) -> None:
        if self.path.rstrip("/") in ("/health", "/v1/models"):
            self._json({"object": "list", "data": [{"id": "standin", "object": "model"}]})
        else:
            self._json({"error": {"message": f"No route {self.path}."}}, 404)

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.path.endswith("/embeddings"):
            inputs = body["input"] if isinstance(body["input"], list) else [body["input"]]
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
            reply: dict[str, Any] = {"text": json.dumps(instance(schema, schema.get("$defs", {})))}
        elif body.get("tools"):
            reply = agent_turn(body.get("messages", []))
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
    args = parser.parse_args()
    Handler.dim = args.dim
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
