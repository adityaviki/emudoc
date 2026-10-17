#!/usr/bin/env bash
# The compose file's embeddings service (deploy/compose.yml) against what the app takes from it
# (CI, "Embeddings"): started from the weights it downloads, as a deployment starts it, it
# answers the texts of embeddings.json beside this script with the vectors and the token count
# written there, and refuses an input past the model's context with the error the app cuts such
# an input on (TOO_LONG in backend/app/embeddings.py). The vectors are those the database holds:
# a server whose vectors moved would rank every stored passage against a query of another
# model's, without a word in any log. Dependabot proposes a new build of the server every week;
# this is what says whether one may be taken.
#
#   .github/scripts/check-embeddings.sh            check
#   .github/scripts/check-embeddings.sh --write    write embeddings.json from the service, for
#                                                  other weights chosen on purpose
#
# It needs the network for the image and the weights (Hugging Face), and runs in a Compose
# project of its own, removed with its volume at the end.

set -euo pipefail

if (($# > 1)) || [[ ${1:---write} != --write ]]; then
  echo "usage: $0 [--write]" >&2
  exit 2
fi
root=$(cd "$(dirname "$0")/../.." && pwd)
export EMUDOC_IMAGE=emudoc:unused EMUDOC_ENV=/dev/null
# A network of Docker's choosing in place of the deployment's fixed subnet, so that the check
# also runs on a machine where the deployment does.
override=$(mktemp)
printf 'networks:\n  default:\n    ipam: !reset {}\n' >"$override"
compose=(docker compose -f "$root/deploy/compose.yml" -f "$override" -p emudoc-embeddings-check)
trap '"${compose[@]}" down --volumes >/dev/null 2>&1; rm -f "$override"' EXIT

"${compose[@]}" up --detach --wait --wait-timeout 300 embeddings

python3 - "$root/.github/scripts/embeddings.json" "${1:-}" "${compose[@]}" <<'EOF'
import json
import math
import subprocess
import sys

reference, mode, compose = sys.argv[1], sys.argv[2], sys.argv[3:]
# Ollama's nomic-embed-text on a CPU and on a GPU agree to 0.99999, and llama.cpp's build and
# Ollama's on a CPU to 0.999999; a changed tokenizer, pooling or normalisation is far below.
MIN_COSINE = 0.9999
TOO_LONG = "exceed_context_size_error"


def embed(texts):
    """The service's status and answer, asked from inside its container: it publishes no port."""
    request = ["curl", "-sS", "-H", "Content-Type: application/json", "--data-binary", "@-"]
    request += ["-w", "\n%{http_code}", "http://127.0.0.1:8080/v1/embeddings"]
    run = subprocess.run(
        [*compose, "exec", "-T", "embeddings", *request],
        input=json.dumps({"model": "nomic-embed-text", "input": texts}),
        capture_output=True,
        text=True,
        check=True,
    )
    body, _, status = run.stdout.rpartition("\n")
    return int(status), json.loads(body)


def vectors(answer):
    return [item["embedding"] for item in sorted(answer["data"], key=lambda item: item["index"])]


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


with open(reference) as file:
    expected = json.load(file)
status, answer = embed(expected["texts"])
if status != 200:
    sys.exit(f"The embeddings service answered {status}: {answer}")

if mode == "--write":
    expected["tokens"] = answer["usage"]["prompt_tokens"]
    expected["vectors"] = []
    # A line a vector, rather than a line a number.
    rows = ",\n  ".join(json.dumps([round(x, 6) for x in vector]) for vector in vectors(answer))
    text = json.dumps(expected, indent=1, ensure_ascii=False)
    with open(reference, "w") as file:
        file.write(text.replace('"vectors": []', '"vectors": [\n  ' + rows + "\n ]") + "\n")
    print(f"Wrote {len(expected['texts'])} vectors to {reference}.")
    sys.exit(0)

failures = []
for text, got, want in zip(expected["texts"], vectors(answer), expected["vectors"], strict=True):
    similarity = cosine(got, want)
    print(f"{similarity:.7f}  {text[:70]!r}")
    if similarity < MIN_COSINE:
        failures.append(f"The vector of {text[:40]!r} is {similarity:.6f} of the one stored.")
tokens = answer.get("usage", {}).get("prompt_tokens")
if tokens != expected["tokens"]:
    failures.append(f"The texts took {tokens} tokens, not {expected['tokens']}: the tokens moved.")

status, answer = embed([expected["texts"][0] + " " + "请" * 3000])
error = answer.get("error") if isinstance(answer, dict) else None
sizes = [error.get(field) for field in ("n_prompt_tokens", "n_ctx")] if error else []
refused = status == 400 and error and error.get("type") == TOO_LONG
if not refused or not all(isinstance(n, int) for n in sizes):
    failures.append(
        f"An input past the context was answered {status}, not 400 with {TOO_LONG} and the "
        f"sizes beside it, which the app cuts such an input on: {answer}"
    )
else:
    print(f"An input of {sizes[0]} tokens is refused as past the model's {sizes[1]}.")

if failures:
    sys.exit("\n".join(failures))
print("The embeddings service answers as the app expects.")
EOF
