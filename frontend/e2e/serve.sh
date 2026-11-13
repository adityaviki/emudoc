#!/usr/bin/env bash
# The app as the end-to-end test drives it (playwright.config.ts starts it): a database of its
# own on the compose server, made afresh and migrated, files in a directory of their own, and
# uvicorn in development with its worker threads, serving the last `pnpm build`, the model, the
# embeddings endpoint and Google Drive being the stand-in (e2e/standin.py, started before this). Every
# setting the run depends on is set here, so that a developer's .env changes none of them.
# Everything the server says goes to LOG as well as to standard error, which Playwright shows:
# the run has no mail relay, so the app logs each message it sends, and the test reads the mail it
# checks from there (e2e/mail.ts), and copies an invitation's link from the page that made it.
#
#   e2e/serve.sh PORT STANDIN_PORT LOG
#
# It needs the database of docker-compose.yml up (docker compose up -d --wait db).

set -euo pipefail

if (($# != 3)); then
  echo "usage: $0 PORT STANDIN_PORT LOG" >&2
  exit 2
fi
port=$1
standin=http://127.0.0.1:$2/v1
connectors=http://127.0.0.1:$2
log=$3
root=$(cd "$(dirname "$0")/../.." && pwd)
database=emudoc_e2e
files=${TMPDIR:-/tmp}/emudoc-e2e-files

if [[ ! -f $root/frontend/dist/index.html ]]; then
  echo "The page is not built: pnpm --dir frontend build." >&2
  exit 2
fi
: >"$log"
exec > >(tee -a "$log" >&2) 2>&1
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
export SCANNER_URL=off
export SIGNUP_OPEN=false
export SMTP_URL=
export RESEND_API_KEY=
export RESEND_WEBHOOK_SECRET=
export MAIL_EVENTS_QUEUE=
export INBOUND_DOMAIN=in.emudoc.test
export INBOUND_MAIL_QUEUE=
export PWNED_PASSWORDS_URL=
export GOOGLE_DRIVE_CLIENT_ID=standin
export GOOGLE_DRIVE_CLIENT_SECRET=standin
export CONNECTOR_BASE_URL=$connectors
export SUPPORT_EMAIL=
export STATUS_PAGE_URL=https://status.example.com
export SOURCE_URL=
export SAMPLE_PROJECT=true
export MODEL_PRICES=
export DAILY_SPEND_CAP=0
export AUTOMATION_WAIT=1
export SENTRY_DSN=
export METRICS_ADDRESS=

uv --directory "$root/backend" run --locked alembic upgrade head
exec uv --directory "$root/backend" run --locked uvicorn app.main:app --host 127.0.0.1 --port "$port"
