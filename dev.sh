#!/usr/bin/env bash
# Run Emudoc for development: the database, the backend restarting on Python changes, and the
# frontend dev server, which updates the open page as you edit. Ctrl+C stops both servers.
set -euo pipefail
cd "$(dirname "$0")"

docker compose up -d --wait
[ -d frontend/node_modules ] || (cd frontend && pnpm install)

# Stop everything when either server exits or the script is interrupted.
cleanup() {
  trap - EXIT INT TERM
  kill 0 2>/dev/null
}
trap cleanup EXIT INT TERM

(cd backend && exec uv run uvicorn app.main:app --reload --reload-dir app) &
(cd frontend && exec pnpm --silent dev) &
wait -n
