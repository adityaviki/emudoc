#!/usr/bin/env bash
# The owner's development servers in the main checkout, following origin/main: dev.sh, with main
# fast-forwarded to origin/main before it starts and every FOLLOW_SECONDS (30) while it runs.
# Sessions work in worktrees (worktree.sh) and merge on GitHub, which moves origin's main and
# nothing here: the servers reload on the files that changed, and what a reload does not take in
# (dev.sh, this script, the compose file, the packages, a migration) stops dev.sh and starts this
# again, main brought up to date before dev.sh starts anything. Any other branch, or a worktree,
# is only told how far behind origin/main it is, and runs dev.sh alone.
#
# This script and worktree.sh are the private archive's, for the owner's sessions: the published
# tree leaves them out (.gitattributes, export-ignore).
#
#   ./follow.sh            run it in this terminal
#   ./follow.sh service    run it from now on at login, as the systemd user service emudoc-dev
set -euo pipefail
cd "$(dirname "$0")"

FOLLOW_SECONDS=${FOLLOW_SECONDS:-30}
# What the servers' reload does not take in: main moving over one of these starts this again.
RESTART='^(dev\.sh|follow\.sh|docker-compose\.yml|backend/(pyproject\.toml|uv\.lock|alembic\.ini|migrations/)|frontend/(package\.json|pnpm-lock\.yaml))'
SERVICE=emudoc-dev

say() { echo "follow.sh: $*" >&2; }

if [ "${1:-}" = service ]; then
  # The main checkout's alone (its .git a directory, a worktree's a file): it is the one that follows.
  [ -d .git ] || { say "the service runs the main checkout's follow.sh: run this there."; exit 2; }
  unit=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/$SERVICE.service
  mkdir -p "$(dirname "$unit")"
  # The PATH of now: at login the service can start before the desktop hands systemd its own.
  cat >"$unit" <<EOF
[Unit]
Description=Emudoc's development servers, following origin/main ($PWD/follow.sh)
# Docker can come up after the session does: the service tries again until it is there.
StartLimitIntervalSec=0

[Service]
ExecStart=$PWD/follow.sh
Environment=PATH=$PATH
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now "$SERVICE"
  say "the service $SERVICE runs it from now on, at login too (systemctl --user restart" \
    "$SERVICE, if it ran another script before). Its output: journalctl --user -u $SERVICE -f;" \
    "systemctl --user stop|restart $SERVICE; gone with systemctl --user disable --now $SERVICE."
  exit
fi

# One at a time: the backend's port is taken while another runs, the service's among them.
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
# Fast-forwards to $target, saying what came; says once a commit what is in the way, across a
# start again too.
warned=${FOLLOW_WARNED:-}
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

following || exec ./dev.sh

# dev.sh beside the watch below, in this process group, so that Ctrl+C reaches it too.
./dev.sh &
dev=$!
stop_dev() {
  kill -TERM "$dev" 2>/dev/null || true
  wait "$dev" 2>/dev/null || true
}
trap 'stop_dev; exit 0' INT TERM HUP

# Following origin/main. A change the servers' reload takes in is merged at once; one it does not
# stops dev.sh, fast-forwards and starts this again, or, when the fast-forward already failed for
# this commit ($tried), once it fast-forwards here.
while true; do
  sleep "$FOLLOW_SECONDS" &
  sleeper=$!
  status=0 finished=''
  wait -n -p finished "$dev" "$sleeper" || status=$?
  if [ "$finished" = "$dev" ]; then
    kill "$sleeper" 2>/dev/null || true
    say "dev.sh stopped (exit $status), so this does too."
    exit "$status"
  fi
  if ! following || ! fetch; then continue; fi
  target=$(git rev-parse origin/main)
  behind || continue
  # grep reads it all (no -q), so that git never writes to a closed pipe, which pipefail would see.
  if git diff --name-only HEAD "$target" | grep -E "$RESTART" >/dev/null; then
    if [ "$target" = "$tried" ]; then
      catch_up || continue
      stop_dev
    else
      stop_dev
      catch_up || true
    fi
    say "origin/main changes what the servers' reload does not take in: starting again."
    trap - INT TERM HUP
    FOLLOW_WARNED=$warned exec ./follow.sh
  fi
  catch_up || true
done
