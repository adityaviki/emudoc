#!/usr/bin/env bash
# Run Emudoc for development: the database, the backend restarting on Python changes, and the
# frontend dev server on http://localhost:5173, which updates the open page as you edit and
# proxies /api to the backend. Ctrl+C stops both servers.
#
#   ./dev.sh
set -euo pipefail
cd "$(dirname "$0")"

say() { echo "dev.sh: $*" >&2; }

# One at a time: the backend's port is taken while another dev.sh runs.
if (exec 3<>/dev/tcp/127.0.0.1/8000) 2>/dev/null; then
  say "something already serves 127.0.0.1:8000, such as another dev.sh."
  exit 1
fi

docker compose up -d --wait
# Every start, so that packages a pull changed are installed: next to nothing when none did.
pnpm --dir frontend install --frozen-lockfile --prefer-offline --silent
# The backend refuses to serve a database that is behind the migrations, and it must not
# upgrade one itself (it restarts on every edit below), so the schema is brought up to date here.
(cd backend && uv run alembic upgrade head)

servers=()
# shellcheck disable=SC2329 # the EXIT trap below runs it
stop() {
  local pid
  for pid in "${servers[@]}"; do kill -TERM -- "-$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
}
trap 'exit 0' INT TERM HUP
trap stop EXIT

# Each server in a session of its own, so that stopping it takes uv's uvicorn with its worker, or
# pnpm's vite, and leaves this script; never reading the terminal, which outside its foreground
# a process may not.
setsid sh -c 'cd backend && exec uv run uvicorn app.main:app --reload --reload-dir app' </dev/null &
servers+=($!)
setsid sh -c 'cd frontend && exec pnpm --silent dev' </dev/null &
servers+=($!)

status=0
wait -n || status=$?
say "a server stopped (exit $status), so the other does too."
exit "$status"
