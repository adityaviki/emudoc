#!/usr/bin/env bash
# The image's smoke test (CI, "Image"; publish.yml before a release goes out): deploy/compose.yml
# run as a team runs it, from the image, with a .env made from deploy/.env.example as the README's
# quick start makes one, so in production with its checks. The database starts, the release step
# migrates it, the API and the worker start once the embeddings server answers, the API answers
# its probes with the version the image was built as and serves the page and the files it names,
# both serve their metrics, and the converter turns a Word file into a PDF with the image's
# LibreOffice on the network only the worker shares and works out the formulas of a workbook the
# agent writes, and a backup restores into an empty database with the files it holds. The suite builds no image, so this is what
# finds a file the image lacks (.dockerignore is an allowlist), a path that moved, something
# written outside /tmp, or a compose file and an example that no longer start together. The
# models are a name and an address nothing answers at, since production builds them at start
# without asking them; ClamAV and Caddy are left out (Caddy's configuration is checked by
# Caddy).
#
#   .github/scripts/check-image.sh emudoc:<sha>
#
# It runs in a Compose project of its own, on a network and a port Docker picks, so beside a
# deployment or development on the same machine, and removes it at the end with its volumes but
# the embedding model's weights, which it downloads the first time into the volume
# check-embeddings.sh keeps them in, and the CI runners keep from one job to the next.
# An image of another platform than the machine's (arm64 on amd64) runs under emulation, given
# binfmt's handlers.

set -euo pipefail

if (($# != 1)); then
  echo "usage: $0 IMAGE" >&2
  exit 2
fi
image=$1
root=$(cd "$(dirname "$0")/../.." && pwd)
work=$(mktemp -d)
project=emudoc-image-check-$$
weights=emudoc-embeddings-check_embedding-model

# The quick start's .env: the example with its four values set, and the profile and address of
# the converter.
password=$(openssl rand -hex 24)
key=$(openssl rand -base64 32)
sed -e "s|^APP_URL=.*|APP_URL=http://localhost|" \
  -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$password|" \
  -e "s|^ENCRYPTION_KEYS=.*|ENCRYPTION_KEYS=$key|" \
  -e "s|^AGENT_MODEL=.*|AGENT_MODEL=ollama:standin|" \
  -e "s|^INDEX_MODEL=.*|INDEX_MODEL=ollama:standin|" \
  -e "s|^# COMPOSE_PROFILES=.*|COMPOSE_PROFILES=office|" \
  -e "s|^# CONVERTER_URL=|CONVERTER_URL=|" \
  "$root/deploy/.env.example" >"$work/.env"
printf '%s\n' "OLLAMA_BASE_URL=http://ollama:11434/v1" "EMUDOC_IMAGE=$image" >>"$work/.env"
for line in "POSTGRES_PASSWORD=$password" COMPOSE_PROFILES=office CONVERTER_URL=http://converter:8080; do
  grep -qxF "$line" "$work/.env" || {
    echo "deploy/.env.example no longer has the line this check sets for $line." >&2
    exit 1
  }
done
# A network of Docker's choosing in place of the deployment's fixed subnet, the API on a port
# Docker picks, and the weights in a volume kept from one run to the next.
cat >"$work/override.yml" <<EOF
networks:
  default:
    ipam: !reset {}
services:
  api:
    ports: !override
      - 127.0.0.1::8000
volumes:
  embedding-model:
    name: $weights
    external: true
EOF
cp "$root/deploy/compose.yml" "$work/compose.yml"
compose=(docker compose --project-directory "$work" -f "$work/compose.yml" -f "$work/override.yml" -p "$project")
docker volume create "$weights" >/dev/null

passed=false
cleanup() {
  if ! $passed; then
    "${compose[@]}" ps --all >&2 || true
    for part in migrate api worker converter embeddings; do
      echo "--- the $part's log" >&2
      "${compose[@]}" logs --no-log-prefix "$part" 2>&1 | tail -n 40 >&2 || true
    done
  fi
  # Every profile's, or the volumes of Caddy, run alone below, would stay.
  "${compose[@]}" --profile '*' down --volumes --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT

# As deploying.md's "Upgrades" does: the release step alone first, then the rest.
"${compose[@]}" run --rm migrate
"${compose[@]}" up --detach --wait --wait-timeout 600
address=http://$("${compose[@]}" port api 8000)

curl -fsS "$address/api/livez" >/dev/null
curl -fsS "$address/api/health" >/dev/null
# The version the build was given (the Dockerfile's VERSION, also its version label), which a
# team's upgrade can wait for on /api/health.
version=$(docker image inspect -f '{{index .Config.Labels "org.opencontainers.image.version"}}' "$image")
if [[ $(curl -fsS "$address/api/health") != *"\"version\":\"$version\""* ]]; then
  echo "The API does not name the version the image was built as, $version." >&2
  exit 1
fi
# Started in production, with the embeddings server beside it answering.
logs=$("${compose[@]}" logs --no-log-prefix api)
for line in "Starting Emudoc $version" "Passages are embedded by"; do
  grep -qF "$line" <<<"$logs" || {
    echo "The API's log has no \"$line\"." >&2
    exit 1
  }
done
if ! page=$(curl -fsS "$address/projects") || ! grep -q '<div id="root">' <<<"$page"; then
  echo "The API does not serve the page." >&2
  exit 1
fi
# Its scripts, styles and icons: the build's assets and what public/ adds, such as theme.js.
mapfile -t files < <(grep -oE '(src|href)="/[^/"][^"]*"' <<<"$page" | cut -d'"' -f2)
((${#files[@]})) || {
  echo "The page names no file of its own." >&2
  exit 1
}
for file in "${files[@]}"; do
  curl -fsS -o /dev/null "$address$file"
done
# A Word file made in the converter's container and sent to it there: LibreOffice and its fonts
# are in the image, and run with nothing to write to but /tmp. Then the same from the worker,
# over the network they alone share, as the worker sends every upload of those types.
convert='
import io, sys, urllib.request
from docx import Document
document = Document()
document.add_heading("The image check", 0)
document.add_paragraph("Converted by the image'"'"'s LibreOffice.")
body = io.BytesIO()
document.save(body)
request = urllib.request.Request(sys.argv[1] + "/convert?type=docx", data=body.getvalue(), method="POST")
answer = urllib.request.urlopen(request, timeout=300).read()
assert answer.startswith(b"%PDF-"), answer[:200]
'
"${compose[@]}" exec -T converter python -c "$convert" http://127.0.0.1:8080 || {
  echo "The converter did not turn a Word file into a PDF." >&2
  exit 1
}
"${compose[@]}" exec -T worker python -c "$convert" http://converter:8080 || {
  echo "The worker did not reach the converter at CONVERTER_URL." >&2
  exit 1
}
# A workbook as the agent writes one, its formulas without values, saved again by the converter
# and the values it worked out written into it (app/sheet_files.py), as the worker does.
calculate='
import sys, urllib.request
from app.sheet_files import Sheet, created, reading, with_values
written = created([Sheet("Prices", [["Item", "Total"], ["Bolts", "=6*7"], ["Joined", "=TEXTJOIN(\"+\",TRUE,A1:A2)"]])])
request = urllib.request.Request(sys.argv[1] + "/convert?type=xlsx&to=xlsx", data=written, method="POST")
read = reading(with_values(written, urllib.request.urlopen(request, timeout=300).read()))
assert "B2 =6*7 → 42" in read and "B3 =TEXTJOIN(\"+\",TRUE,A1:A2) → \"Item+Bolts\"" in read, read
'
"${compose[@]}" exec -T worker python -c "$calculate" http://converter:8080 || {
  echo "The converter did not work out the formulas of a workbook for the worker." >&2
  exit 1
}
# The metrics, on the port of their own the image listens on (METRICS_ADDRESS), which nothing
# publishes: the API's requests, and the worker's figures of the database.
metrics() {
  "${compose[@]}" exec -T "$1" python -c "import urllib.request
print(urllib.request.urlopen('http://127.0.0.1:9464/metrics', timeout=5).read().decode())"
}
grep -q '^emudoc_http_requests_total{' <<<"$(metrics api)" || {
  echo "The API serves no metrics on 9464." >&2
  exit 1
}
grep -q '^emudoc_documents{' <<<"$(metrics worker)" || {
  echo "The worker serves none of the database's figures on 9464." >&2
  exit 1
}
# A backup and a restore, as a team takes them (deploying.md, "Backups"): the first account and
# its workspace made, with the sample project's files, then a backup by the backup service, then
# the database and the files lost, and both brought back into an empty database by the image's
# pg_restore, every original with them.
"${compose[@]}" run --rm --no-deps -T worker python -m app.maintenance create-admin \
  admin@example.com --name Admin --workspace Check >/dev/null
originals() {
  "${compose[@]}" run --rm --no-deps -T worker \
    sh -c 'find /var/lib/emudoc/orgs -type f -name "original.*" | wc -l'
}
held=$(originals)
((held > 0)) || {
  echo "The sample project put no original into the storage." >&2
  exit 1
}
taken=$("${compose[@]}" run --rm -T backup)
echo "$taken" >&2
folder=$(grep -oE '/backups/emudoc-[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{6}Z' <<<"$taken" | head -n 1)
[[ -n $folder ]] || {
  echo "The backup names no folder it wrote:" >&2
  echo "$taken" >&2
  exit 1
}
"${compose[@]}" stop api worker >/dev/null 2>&1
"${compose[@]}" exec -T postgres sh -c 'dropdb -U emudoc --force emudoc && createdb -U emudoc emudoc'
"${compose[@]}" run --rm --no-deps -T worker sh -c 'rm -r /var/lib/emudoc/orgs'
restored=$("${compose[@]}" run --rm -T backup python -m app.maintenance restore "$folder") || {
  echo "The restore of $folder failed." >&2
  exit 1
}
if ! grep -qx "Start the app." <<<"$restored" || grep -q "without an original" <<<"$restored"; then
  echo "The restore did not bring everything back:" >&2
  echo "$restored" >&2
  exit 1
fi
"${compose[@]}" up --detach --wait --wait-timeout 300
[[ $(originals) == "$held" ]] || {
  echo "The restore put back $(originals) of the $held originals." >&2
  exit 1
}
"${compose[@]}" run --rm --no-deps -T worker \
  python -m app.maintenance reset-password admin@example.com >/dev/null || {
  echo "The restored database lacks the account the backup held." >&2
  exit 1
}
# Started again, the API listens on another port Docker picks.
curl -fsS "http://$("${compose[@]}" port api 8000)/api/health" >/dev/null
# Caddy's configuration as compose.yml gives it, written as the service writes it, with an
# address for its name.
# shellcheck disable=SC2016 # the container's shell expands it, from the service's environment
validate=(
  "${compose[@]}" --profile tls run --rm --no-deps caddy
  'printf "%s" "$CADDYFILE" >/tmp/Caddyfile && caddy validate --config /tmp/Caddyfile --adapter caddyfile'
)
"${validate[@]}" >/dev/null 2>&1 || {
  echo "Caddy refuses the configuration in compose.yml:" >&2
  "${validate[@]}" >&2 || true
  exit 1
}

passed=true
echo "The image $image ($version) runs deploy/compose.yml in production: it migrates, serves the page, its ${#files[@]} files and the probes beside the embeddings server, runs the worker, both serve their metrics, its converter turns a Word file into a PDF and works out a workbook's formulas for the worker, and a backup restores into an empty database with its $held originals."
