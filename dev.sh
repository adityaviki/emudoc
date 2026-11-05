#!/usr/bin/env bash
# Run Emudoc for development: the database, the backend restarting on Python changes, and the
# frontend dev server, which updates the open page as you edit. Ctrl+C stops both servers.
#
# The main checkout on main follows origin/main while this runs. Sessions work in worktrees
# (worktree.sh) and merge on GitHub, which moves origin's main and nothing here, so every
# FOLLOW_SECONDS (30) it fetches and fast-forwards: the servers reload on the files that changed,
# and what a reload does not take in (this script, the compose file, the packages, a migration)
# starts the script again, which brings main up to date before it starts anything. Any other
# branch, or a worktree, is only told how far behind origin/main it is.
#
#   ./dev.sh            run it in this terminal
#   ./dev.sh service    run it from now on at login, as the systemd user service emudoc-dev
set -euo pipefail
cd "$(dirname "$0")"

FOLLOW_SECONDS=${FOLLOW_SECONDS:-30}
# What the servers' reload does not take in: main moving over one of these starts the script again.
RESTART='^(dev\.sh|docker-compose\.yml|backend/(pyproject\.toml|uv\.lock|alembic\.ini|migrations/)|frontend/(package\.json|pnpm-lock\.yaml))'
SERVICE=emudoc-dev

say() { echo "dev.sh: $*" >&2; }

if [ "${1:-}" = service ]; then
  # The main checkout's alone (its .git a directory, a worktree's a file): it is the one that follows.
  [ -d .git ] || { say "the service runs the main checkout's dev.sh: run this there."; exit 2; }
  unit=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/$SERVICE.service
  mkdir -p "$(dirname "$unit")"
  # The PATH of now: at login the service can start before the desktop hands systemd its own.
  cat >"$unit" <<EOF
[Unit]
Description=Emudoc's development servers, following origin/main ($PWD/dev.sh)
# Docker can come up after the session does: the service tries again until it is there.
StartLimitIntervalSec=0

[Service]
ExecStart=$PWD/dev.sh
Environment=PATH=$PATH
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now "$SERVICE"
  say "the service $SERVICE runs it from now on, at login too. Its output:" \
    "journalctl --user -u $SERVICE -f; systemctl --user stop|restart $SERVICE; gone with" \
    "systemctl --user disable --now $SERVICE."
  exit
fi

# One at a time: the backend's port is taken while another dev.sh runs, the service's among them.
if (exec 3<>/dev/tcp/127.0.0.1/8000) 2>/dev/null; then
  say "something already serves 127.0.0.1:8000: another dev.sh, or the service" \
    "(systemctl --user status $SERVICE; systemctl --user stop $SERVICE to run it here)."
  exit 1
fi

# The main checkout on main.
following() { [ -d .git ] && [ "$(git symbolic-ref --short -q HEAD)" = main ]; }
# Never a prompt, the script fetching on its own: a key that needs one fails, and origin is unreached.
fetch() { GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND="${GIT_SSH_COMMAND:-ssh} -o BatchMode=yes" git fetch -q origin 2>/dev/null; }
# Whether origin/main ($target) holds commits HEAD does not.
behind() { ! git merge-base --is-ancestor "$target" HEAD; }
# Fast-forwards to $target, saying what came; says once a commit what is in the way.
warned=
catch_up() {
  local before out
  before=$(git rev-parse HEAD)
  if out=$(git merge -q --ff-only "$target" 2>&1); then
    say "main moved up to origin/main:"
    git log --format='  %h %s' "$before..HEAD" >&2
    return 0
  fi
  if [ "$warned" != "$target" ]; then
    warned=$target
    say "main stays at $(git rev-parse --short HEAD), behind origin/main, until what is in the way" \
      "goes (a commit of its own, or an edit: commit it on a branch, or git stash push -m <name>):"
    echo "  ${out//$'\n'/$'\n'  }" >&2
  fi
  return 1
}

target='' tried=''
if fetch; then
  target=$(git rev-parse origin/main)
  if following; then
    # A fast-forward that fails here is not tried again by starting again ($tried, below).
    if behind && ! catch_up; then tried=$target; fi
  elif behind; then
    say "$(git rev-parse --abbrev-ref HEAD) is behind origin/main by" \
      "$(git rev-list --count "HEAD..$target"), which this does not run (git rebase origin/main)."
  fi
else
  say "origin could not be reached, so this starts with what is here."
fi

docker compose up -d --wait
# Every start, so that packages a pull changed are installed: next to nothing when none did.
pnpm --dir frontend install --frozen-lockfile --prefer-offline --silent
# The backend refuses to serve a database that is behind the migrations, and it must not
# upgrade one itself (it restarts on every edit below), so the schema is brought up to date here.
(cd backend && uv run alembic upgrade head)

servers=() watcher=''
stop() {
  local pid
  for pid in "${servers[@]}"; do kill -TERM -- "-$pid" 2>/dev/null || true; done
  [ -z "$watcher" ] || kill "$watcher" 2>/dev/null || true
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

# Following origin/main. A change the servers' reload takes in is merged here; one it does not
# ends the watch with 75, and the script starts again, fast-forwarding before anything runs, or,
# when that already failed for this commit ($tried), once it fast-forwards here.
if following; then
  (
    set +e
    trap - EXIT INT TERM HUP
    while sleep "$FOLLOW_SECONDS"; do
      if ! following || ! fetch; then continue; fi
      target=$(git rev-parse origin/main)
      behind || continue
      if git diff --name-only HEAD "$target" | grep -qE "$RESTART"; then
        [ "$target" != "$tried" ] && exit 75
        catch_up && exit 75
      else
        catch_up
      fi
    done
  ) &
  watcher=$!
fi

status=0
wait -n -p finished || status=$?
if [ -n "$watcher" ] && [ "$finished" = "$watcher" ] && [ "$status" -eq 75 ]; then
  say "origin/main changes what the servers' reload does not take in: starting again."
  stop
  trap - EXIT
  exec ./dev.sh
fi
say "a server stopped (exit $status), so the other does too."
exit "$status"
