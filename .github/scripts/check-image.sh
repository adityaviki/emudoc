#!/usr/bin/env bash
# The image's smoke test (CI, "Image"): the release step, the API and the worker start from the
# image as a deployment runs them, on a read-only filesystem with a tmpfs at /tmp and the files
# on a volume, and the API answers its probes and serves the page and the files it names. The
# suite builds no image, so this is what finds a file the image lacks (.dockerignore is an
# allowlist), a path that moved or something written outside /tmp. Development's settings,
# since production's want a mail relay, a scanner, a model key and the rest: what is checked is
# the image, not a configuration (deploying.md, "The image", for the whole deployment).
#
#   .github/scripts/check-image.sh emudoc:<sha>
#
# It needs the database of docker-compose.yml up (docker compose up -d --wait db), in which it
# makes a scratch database of its own and drops it at the end, as it does its containers.

set -euo pipefail

if (($# != 1)); then
  echo "usage: $0 IMAGE" >&2
  exit 2
fi
image=$1
root=$(cd "$(dirname "$0")/../.." && pwd)
compose=(docker compose -f "$root/docker-compose.yml")
name=emudoc-image-check-$$
database=emudoc_image_check

db=$("${compose[@]}" ps -q db)
if [[ -z $db ]]; then
  echo "The database of docker-compose.yml is not running: docker compose up -d --wait db." >&2
  exit 2
fi
# The containers join the database's network and reach it by its service name, so nothing but
# the API's port is published, and that on a port Docker picks.
network=$(docker inspect -f '{{range $net, $_ := .NetworkSettings.Networks}}{{$net}}{{end}}' "$db")
sql() {
  "${compose[@]}" exec -T -e PGOPTIONS=--client-min-messages=warning db \
    psql -q -U emudoc -d emudoc -c "$1"
}

passed=false
cleanup() {
  if ! $passed; then
    for part in api worker; do
      echo "--- the $part's log" >&2
      docker logs "$name-$part" 2>&1 | tail -n 40 >&2 || true
    done
  fi
  docker rm -f "$name-api" "$name-worker" >/dev/null 2>&1 || true
  docker volume rm "$name-files" >/dev/null 2>&1 || true
  sql "DROP DATABASE IF EXISTS $database WITH (FORCE)" || true
}
trap cleanup EXIT

sql "DROP DATABASE IF EXISTS $database WITH (FORCE)"
sql "CREATE DATABASE $database"

app=(
  --network "$network" --read-only --tmpfs /tmp
  --mount "type=volume,src=$name-files,dst=/var/lib/emudoc"
  -e ENVIRONMENT=development -e RUN_WORKER=false
  -e "DATABASE_URL=postgresql+psycopg://emudoc:emudoc@db:5432/$database"
  -e STORAGE_URL=file:///var/lib/emudoc
)
docker run --rm "${app[@]}" "$image" alembic upgrade head
docker run -d --name "$name-api" -p 127.0.0.1::8000 "${app[@]}" "$image" >/dev/null
docker run -d --name "$name-worker" "${app[@]}" "$image" \
  python -m app.worker --alive-file /tmp/worker.alive >/dev/null
address=http://$(docker port "$name-api" 8000/tcp | head -n 1)

running() {
  [[ $(docker inspect -f '{{.State.Running}}' "$name-$1") == true ]] || {
    echo "The $1 stopped." >&2
    exit 1
  }
}
for ((tries = 60; ; tries--)); do
  running api
  curl -fsS "$address/api/health" >/dev/null 2>&1 && break
  ((tries > 1)) || {
    echo "The API was not ready within a minute." >&2
    exit 1
  }
  sleep 1
done
curl -fsS "$address/api/livez" >/dev/null
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
for ((tries = 30; ; tries--)); do
  running worker
  docker exec "$name-worker" test -f /tmp/worker.alive && break
  ((tries > 1)) || {
    echo "The worker did not say it was alive within 30 seconds." >&2
    exit 1
  }
  sleep 1
done
running api

passed=true
echo "The image $image migrates, serves the page, its ${#files[@]} files and the probes, and runs the worker."
