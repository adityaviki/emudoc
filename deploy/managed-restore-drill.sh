#!/usr/bin/env bash
# The restore drill for a managed database (deploying.md, "The drill"): have the provider
# restore production's database to a new instance, bring the copy to the code's schema, check
# it against the live storage with `after-restore --dry-run`, which changes neither, and delete
# the instance whatever happened. It says how long each step took, the parts of the recovery
# time, and exits with a status other than 0 when a step fails, the restore is not whole, or
# the instance could not be deleted, for the timer that runs it to report.
#
#   deploy/managed-restore-drill.sh                            to the latest moment it can
#   deploy/managed-restore-drill.sh "2026-10-14 09:30:00+00"   to a moment within BACKUP_DAYS
#
# Every provider restores its own way, so its part is two commands of the deployment's, each
# one line that the instance's name and the moment are put after (deploy/rds-drill.sh is RDS's):
#
#   DRILL_RESTORE NAME [TIME]   restores production's database to a new instance called NAME,
#                               at TIME or the latest moment the provider can, waits until the
#                               instance takes connections, and prints two lines of its own:
#                               DATABASE_URL=<the copy's URL> and DRILL_MOMENT=<the moment it
#                               restored to, with its zone>. Whatever else it prints is shown.
#   DRILL_DISCARD NAME          deletes the instance and its backups, and succeeds when there
#                               is none; run at the end however the drill went.
#
# Run it on a machine that reaches the provider's API and the copy, with the app's settings in
# the environment, the storage's included: the dry run only reads the storage, so read-only
# keys do. DATABASE_URL is set here, to the copy. The copy holds every document's text until it
# is deleted, so the provider keeps it like production's: encrypted, on no public address.

set -euo pipefail

# The checkout is looked for only without DRILL_APP: the image needs none beside the script.
if [[ -n ${DRILL_APP:-} ]]; then
  read -ra app <<<"$DRILL_APP"
else
  backend=${DRILL_BACKEND:-$(cd "$(dirname "$0")/../backend" && pwd)}
  app=(uv --directory "$backend" run --frozen --no-dev)
fi
read -ra restore <<<"${DRILL_RESTORE:?DRILL_RESTORE names the command that restores the database to a new instance}"
read -ra discard <<<"${DRILL_DISCARD:?DRILL_DISCARD names the command that deletes the instance}"
# The provider's names allow letters, digits and hyphens; this one says what it is and when.
name=${DRILL_INSTANCE:-emudoc-drill-$(date -u +%Y%m%d-%H%M%S)}

if (($# > 1)); then
  echo "usage: $0 [TIME]   TIME with its zone, as in '2026-10-14 09:30:00+00'" >&2
  exit 2
fi
asked=${1:-}

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

# What the restore prints holds the copy's password, so it goes to a file only this user reads.
scratch=$(mktemp -d)
status=0
cleanup() {
  # A step that failed under `set -e` ends the script with its own status, which stands.
  local code=$?
  ((status)) || status=$code
  rm -rf "$scratch"
  if [[ -n ${DRILL_KEEP:-} ]]; then
    echo "The instance $name is kept (DRILL_KEEP); delete it with: ${discard[*]} $name" >&2
  elif ! "${discard[@]}" "$name"; then
    echo "The instance $name could not be deleted, and it holds a copy of every document:" \
      "delete it by hand." >&2
    ((status)) || status=1
  fi
  exit "$status"
}
trap cleanup EXIT

echo "Restoring the database to ${asked:-the latest moment the provider can} as $name."
started=$SECONDS
"${restore[@]}" "$name" ${asked:+"$asked"} >"$scratch/restored" || status=$?
restored=$SECONDS
url='' moment=''
while IFS= read -r line; do
  case $line in
    DATABASE_URL=*) url=${line#DATABASE_URL=} ;;
    DRILL_MOMENT=*) moment=${line#DRILL_MOMENT=} ;;
    *) printf '%s\n' "$line" ;;
  esac
done <"$scratch/restored"
if ((status)); then
  echo "The drill failed: DRILL_RESTORE ended with status $status." >&2
  exit
elif [[ -z $url || -z $moment ]]; then
  echo "DRILL_RESTORE printed no DATABASE_URL= or no DRILL_MOMENT= line." >&2
  status=1
  exit
fi
echo "The copy is at $moment."

export DATABASE_URL=$url
"${app[@]}" alembic upgrade head || {
  status=$?
  echo "The drill failed: the migrations did not run on the copy." >&2
  exit
}
migrated=$SECONDS
"${app[@]}" python -m app.maintenance after-restore --dry-run "$moment" || status=$?
checked=$SECONDS

echo
echo "The drill took $(span $((checked - started))): the restore $(span $((restored - started))),\
 the schema $(span $((migrated - restored))), the check $(span $((checked - migrated))); deleting\
 the instance follows."
if ((status == 0)); then
  echo "The drill passed."
else
  echo "The drill failed: the check above says why."
fi
