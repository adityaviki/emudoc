#!/usr/bin/env bash
# Another checkout to work in beside the main one, so that several sessions work at once, each
# on its own branch: a git worktree of origin/main under .claude/worktrees/<name>, on the branch
# <name>, with a database of its own on the compose server (emudoc_wt_<name>), the main
# checkout's .env naming that database, its packages installed and its schema migrated. Its
# files go to its own backend/data, and the suite in it names its own databases
# (tests/conftest.py). `remove` takes away what `new` made, the branch only once main holds its
# commits.
#
#   emudoc/worktree.sh start <name>    make it unless it is there, and start Claude Code in it
#   emudoc/worktree.sh new <name>      make it; prints its path and nothing else on stdout
#   emudoc/worktree.sh remove <name>   refused while it holds changes not committed
set -euo pipefail
# Only the path goes to stdout, so that `cd "$(emudoc/worktree.sh new <name>)"` works.
exec 3>&1 1>&2
# The main checkout, from whichever checkout's copy of this script is run.
cd "$(dirname "$(git -C "$(dirname "$0")" rev-parse --path-format=absolute --git-common-dir)")"

usage() {
  echo "usage: $0 start|new|remove <name>: lower-case letters, digits and dashes, at most 40"
  exit 2
}
[ $# -eq 2 ] && [[ $2 =~ ^[a-z0-9][a-z0-9-]{0,39}$ ]] || usage
name=$2
dir=.claude/worktrees/$name
key=${name//-/_}
database=emudoc_wt_$key
sql() { docker compose exec -T db psql -U emudoc -d postgres -v ON_ERROR_STOP=1 -qAt -c "$1"; }

new() {
  git fetch -q origin
  git worktree add -q -b "$name" "$dir" origin/main
  docker compose up -d --wait db
  sql "CREATE DATABASE $database"
  local settings=.env
  [ -f "$settings" ] || settings=.env.example
  # The database the worktree's own, the listener's too where it is set; the files in its own
  # backend/data rather than a place another checkout's database names.
  sed -E \
    -e "s#^(DATABASE(_LISTEN)?_URL=[\"']?.*/)[^/?\"']+#\1$database#" \
    -e 's#^(STORAGE_URL=)#\# \1#' \
    "$settings" >"$dir/.env"
  # Claude Code's rules for each part of the app live outside the repository (../.claude/rules,
  # each rule's paths written from the main checkout's parent and from a checkout's root): a
  # session started in the worktree finds them in its own .claude.
  if [ -d ../.claude/rules ]; then
    mkdir -p "$dir/.claude"
    ln -s "$(realpath ../.claude/rules)" "$dir/.claude/rules"
  fi
  pnpm --dir "$dir/frontend" install --frozen-lockfile --prefer-offline --silent
  uv --directory "$dir/backend" run --quiet alembic upgrade head
}

case $1 in
  start)
    [ -d "$dir" ] || new
    cd "$dir"
    exec claude >&3 3>&-
    ;;
  new)
    new
    realpath "$dir" >&3
    ;;
  remove)
    git worktree remove "$dir"
    git fetch -q origin
    # Merged by rebase, its commits are on main under other hashes: `git cherry` finds them by
    # what they change.
    if git cherry origin/main "$name" | grep -q '^+'; then
      echo "Kept the branch $name: it has commits main has not."
    else
      git branch -q -D "$name"
    fi
    for each in $(sql "SELECT datname FROM pg_database WHERE datname = '$database' OR datname ~ '^emudoc_test_wt_${key}(_gw[0-9]+)?\$'"); do
      sql "DROP DATABASE \"$each\" WITH (FORCE)"
    done
    ;;
  *) usage ;;
esac
