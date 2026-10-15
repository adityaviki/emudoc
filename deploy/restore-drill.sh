#!/usr/bin/env bash
# The restore drill (deploying.md, "The drill"): restore the database from its pgBackRest
# backups into a scratch directory, start a throwaway Postgres on it that archives nothing and
# listens on no port, bring it to the code's schema, and check it against the live storage with
# `after-restore --dry-run`, which changes neither. It says how long each step took, the parts
# of the recovery time, and exits with a status other than 0 when a step fails or the restore
# is not whole, for the timer that runs it to report.
#
#   deploy/restore-drill.sh                            to the end of the archive
#   deploy/restore-drill.sh "2026-10-14 09:30:00+00"   to a moment within BACKUP_DAYS
#
# Run it as the user Postgres runs as, on a machine with pgBackRest's configuration (the
# repository and its passphrase), the server binaries of the database's major version with
# pgvector, and the app's settings in the environment, the storage's included: the dry run only
# reads the storage, so read-only keys do. DATABASE_URL is set here, to the copy. The scratch
# directory holds every document's text for as long as the drill runs, unencrypted, so it
# belongs on a disk kept like the database's.

set -euo pipefail

stanza=${DRILL_STANZA:-emudoc}
scratch=${DRILL_DIR:-/var/tmp/emudoc-drill}
pg_bin=${DRILL_PG_BIN:-/usr/lib/postgresql/17/bin}
role=${DRILL_ROLE:-emudoc}
database=${DRILL_DATABASE:-emudoc}
backend=${DRILL_BACKEND:-$(cd "$(dirname "$0")/../backend" && pwd)}
# How the app's commands run: in the checkout, with uv.
read -ra app <<<"${DRILL_APP:-uv --directory $backend run --frozen --no-dev}"
# How long recovery may replay the archive before the drill gives up, in seconds.
recovery_limit=${DRILL_RECOVERY_LIMIT:-21600}

if (($# > 1)); then
  echo "usage: $0 [TIME]   TIME with its zone, as in '2026-10-14 09:30:00+00'" >&2
  exit 2
fi
if [[ -e $scratch ]]; then
  echo "$scratch exists: a drill is running, or one was kept with DRILL_KEEP; remove it first." >&2
  exit 2
fi

span() {
  local seconds=$1
  if ((seconds >= 3600)); then
    echo "$((seconds / 3600)) h $((seconds % 3600 / 60)) min"
  elif ((seconds >= 60)); then
    echo "$((seconds / 60)) min $((seconds % 60)) s"
  else
    echo "$seconds s"
  fi
}

cleanup() {
  if [[ -f $scratch/data/postmaster.pid ]]; then
    "$pg_bin/pg_ctl" -D "$scratch/data" -m immediate stop >/dev/null || true
  fi
  if [[ -z ${DRILL_KEEP:-} ]]; then
    rm -rf "$scratch"
  fi
}

mkdir -m 700 "$scratch"
trap cleanup EXIT
mkdir -m 700 "$scratch/data" "$scratch/socket"

if (($# == 1)); then
  moment=$1
  target=(--type=time "--target=$moment" --target-action=promote)
  echo "Restoring stanza $stanza to $moment into $scratch."
else
  target=()
  echo "Restoring stanza $stanza to the end of its archive into $scratch."
fi
started=$SECONDS
# Archiving off: a copy that pushed its own WAL into the repository would start a timeline
# there that a later restore to the end of the archive follows.
pgbackrest --stanza="$stanza" --pg1-path="$scratch/data" --archive-mode=off "${target[@]}" \
  restore
restored=$SECONDS

# Production's configuration may not suit this machine and must not reach production: no port,
# no archiving, no TLS files or preloaded libraries this machine may lack, and connections
# through a socket only this user can reach. A Debian cluster keeps its configuration outside
# the data directory, so the copy may have none.
[[ -f $scratch/data/postgresql.conf ]] || : >"$scratch/data/postgresql.conf"
echo "local all all trust" >"$scratch/hba.conf"
: >"$scratch/ident.conf"
give_up() {
  echo "$1" >&2
  tail -n 20 "$scratch/server.log" >&2
  exit 1
}
echo "Replaying the archive (at most $(span "$recovery_limit"); the server's log is $scratch/server.log)."
# Without hot standby, pg_ctl's wait ends once recovery has begun, and the server takes no
# connection until recovery has ended and it was promoted; one that reaches no end exits.
"$pg_bin/pg_ctl" -D "$scratch/data" -l "$scratch/server.log" -w -o "\
  -c listen_addresses='' -c port=5432 -c unix_socket_directories='$scratch/socket' \
  -c hba_file='$scratch/hba.conf' -c ident_file='$scratch/ident.conf' -c ssl=off \
  -c archive_mode=off -c shared_preload_libraries='' -c hot_standby=off \
  -c shared_buffers=256MB -c logging_collector=off" start >/dev/null ||
  give_up "The copy's server did not start:"
until "$pg_bin/pg_isready" -q -h "$scratch/socket" -p 5432; do
  "$pg_bin/pg_ctl" -D "$scratch/data" status >/dev/null ||
    give_up "The copy's server stopped before its recovery ended:"
  ((SECONDS - restored < recovery_limit)) ||
    give_up "The recovery did not end within $(span "$recovery_limit") (DRILL_RECOVERY_LIMIT):"
  sleep 2
done
recovered=$SECONDS
# A restore to the end of the archive reaches the moment its recovery ended.
moment=${moment:-$(date -u '+%Y-%m-%d %H:%M:%S+00')}

export DATABASE_URL="postgresql+psycopg://$role@/$database?host=$scratch/socket"
"${app[@]}" alembic upgrade head
migrated=$SECONDS
status=0
"${app[@]}" python -m app.maintenance after-restore --dry-run "$moment" || status=$?
checked=$SECONDS

echo
echo "The drill took $(span $((checked - started))): the restore $(span $((restored - started))),\
 the replay $(span $((recovered - restored))), the schema $(span $((migrated - recovered))),\
 the check $(span $((checked - migrated)))."
if ((status == 0)); then
  echo "The drill passed."
else
  echo "The drill failed: the check above says why."
fi
exit "$status"
