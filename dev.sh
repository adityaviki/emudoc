#!/usr/bin/env bash
# Run Emudoc for development: the database, the backend restarting on Python changes, and the
# frontend dev server, which updates the open page as you edit. Ctrl+C stops both servers.
set -euo pipefail
cd "$(dirname "$0")"

# What was merged since: sessions work in worktrees (worktree.sh) and merge on GitHub, which
# moves origin's main and nothing here. The main checkout (its .git a directory, a worktree's a
# file) on main is fast-forwarded to it; any other branch is only told how far behind it is.
if ! git fetch -q origin; then
  echo "dev.sh: origin could not be reached, so this starts with what is here." >&2
elif [ -d .git ] && [ "$(git symbolic-ref --short -q HEAD)" = main ]; then
  before=$(git rev-parse --short HEAD)
  if git merge -q --ff-only origin/main; then
    after=$(git rev-parse --short HEAD)
    [ "$before" = "$after" ] || echo "dev.sh: main moved from $before up to origin/main, $after." >&2
  else
    echo "dev.sh: main stays where it was: it cannot fast-forward to origin/main" \
      "(commits of its own, or an edit in a file the merge would change)." >&2
  fi
else
  count=$(git rev-list --count HEAD..origin/main)
  [ "$count" -eq 0 ] || echo "dev.sh: $(git rev-parse --abbrev-ref HEAD) is behind origin/main" \
    "by $count, which this does not run (git rebase origin/main)." >&2
fi

docker compose up -d --wait
# Every start, so that packages a pull changed are installed: next to nothing when none did.
pnpm --dir frontend install --frozen-lockfile --prefer-offline --silent
# The backend refuses to serve a database that is behind the migrations, and it must not
# upgrade one itself (it restarts on every edit below), so the schema is brought up to date here.
(cd backend && uv run alembic upgrade head)

# Stop everything when either server exits or the script is interrupted.
cleanup() {
  trap - EXIT INT TERM
  kill 0 2>/dev/null
}
trap cleanup EXIT INT TERM

(cd backend && exec uv run uvicorn app.main:app --reload --reload-dir app) &
(cd frontend && exec pnpm --silent dev) &
wait -n
