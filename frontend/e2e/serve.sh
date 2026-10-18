#!/usr/bin/env bash
# The app as the end-to-end test drives it (playwright.config.ts starts it): a database of its
# own on the compose server, made afresh and migrated, files in a directory of their own, and
# uvicorn in development with its worker threads, serving the last `pnpm build`, the model and
# the embeddings endpoint being the stand-in (e2e/standin.py, started before this). Every
# setting the run depends on is set here, so that a developer's .env changes none of them.
#
#   e2e/serve.sh PORT STANDIN_PORT
#
# It needs the database of docker-compose.yml up (docker compose up -d --wait db).

set -euo pipefail

if (($# != 2)); then
  echo "usage: $0 PORT STANDIN_PORT" >&2
  exit 2
fi
port=$1
standin=http://127.0.0.1:$2/v1
root=$(cd "$(dirname "$0")/../.." && pwd)
database=emudoc_e2e
files=${TMPDIR:-/tmp}/emudoc-e2e-files

if [[ ! -f $root/frontend/dist/index.html ]]; then
  echo "The page is not built: pnpm --dir frontend build." >&2
  exit 2
fi
sql() {
  docker compose -f "$root/docker-compose.yml" exec -T -e PGOPTIONS=--client-min-messages=warning \
    db psql -q -U emudoc -d emudoc -c "$1"
}
sql "DROP DATABASE IF EXISTS $database WITH (FORCE)"
sql "CREATE DATABASE $database"
rm -rf "$files"
mkdir -p "$files"

export ENVIRONMENT=development
export RUN_WORKER=true
export LOCAL_ACCOUNT=false
export DATABASE_URL=postgresql+psycopg://emudoc:emudoc@127.0.0.1:5437/$database
export STORAGE_URL=file://$files
export APP_URL=http://127.0.0.1:$port
export AGENT_MODEL=ollama:standin
export INDEX_MODEL=ollama:standin
export AGENT_FALLBACK_MODEL=
export INDEX_FALLBACK_MODEL=
export OLLAMA_BASE_URL=$standin
export EMBEDDING_BASE_URL=$standin
export EMBEDDING_MODEL=standin
export EMBEDDING_DIM=768
export DISABLED_PROVIDERS=
export DATA_REGION=
export SCANNER_URL=off
export SMTP_URL=
export MODEL_PRICES=
export DAILY_SPEND_CAP=0
export SENTRY_DSN=

uv --directory "$root/backend" run --locked alembic upgrade head
exec uv --directory "$root/backend" run --locked uvicorn app.main:app --host 127.0.0.1 --port "$port"
