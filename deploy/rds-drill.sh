#!/usr/bin/env bash
# Amazon RDS's part of the managed restore drill (deploy/managed-restore-drill.sh):
#
#   DRILL_RESTORE="/opt/emudoc/deploy/rds-drill.sh restore"
#   DRILL_DISCARD="/opt/emudoc/deploy/rds-drill.sh discard"
#
#   rds-drill.sh restore NAME [TIME]   restore RDS_SOURCE to a new instance NAME at TIME (with
#                                      its zone), or at the latest restorable time, wait for
#                                      it, and print DATABASE_URL= and DRILL_MOMENT=
#   rds-drill.sh discard NAME          delete the instance NAME and its automated backups
#
# It needs the AWS CLI with credentials (the region from AWS_REGION or the CLI's own
# configuration) allowed rds:DescribeDBInstances, rds:RestoreDBInstanceToPointInTime,
# rds:AddTagsToResource and rds:DeleteDBInstance, and:
#
#   RDS_SOURCE            production's instance identifier
#   RDS_URL               the copy's DATABASE_URL with HOST for its address, with production's
#                         role and password (a restore keeps them) and RDS's authority:
#                         postgresql+psycopg://emudoc:…@HOST:5432/emudoc?sslmode=verify-full&sslrootcert=/etc/emudoc/rds-global-bundle.pem
#   RDS_SUBNET_GROUP      the DB subnet group to put the copy in, one the drill machine reaches
#   RDS_SECURITY_GROUPS   the security group ids that let the drill machine in, space-separated
#   RDS_INSTANCE_CLASS    the copy's class; production's when unset
#   RDS_PARAMETER_GROUP   the copy's parameter group; the engine's default when unset
#   RDS_WAIT_LIMIT        how long the restore may take before the drill gives up, in seconds;
#                         6 h when unset
#
# The copy has no public address, no standby and no deletion protection, and is tagged
# purpose=restore-drill, so that one a drill failed to delete can be found. It is encrypted
# with production's key when production is encrypted.

set -euo pipefail

# How often the instance's state is asked for while it is restored or deleted.
poll=${RDS_POLL_SECONDS:-30}

usage() {
  echo "usage: $0 restore NAME [TIME] | discard NAME" >&2
  exit 2
}

need() {
  if [[ -z ${!1:-} ]]; then
    echo "$1 is not set: $2" >&2
    exit 2
  fi
}

# The instance's state, or nothing when there is no such instance; any other failure, such as
# credentials that ran out, fails, so that an instance is never taken for gone.
state_of() {
  local out
  if out=$(aws rds describe-db-instances --db-instance-identifier "$1" \
    --query 'DBInstances[0].DBInstanceStatus' --output text 2>&1); then
    echo "$out"
  elif [[ $out != *DBInstanceNotFound* ]]; then
    echo "$out" >&2
    return 1
  fi
}

restore() {
  local name=$1 asked=${2:-} moment state
  need RDS_SOURCE "name production's instance"
  need RDS_URL "give the copy's DATABASE_URL, with HOST for its address"
  need RDS_SUBNET_GROUP "name the subnet group to put the copy in"
  need RDS_SECURITY_GROUPS "list the security groups that let the drill machine in"
  local -a groups when
  read -ra groups <<<"$RDS_SECURITY_GROUPS"
  # The comma is the CLI's shorthand for one tag's key and value, not an array's separator.
  # shellcheck disable=SC2054
  local -a options=(
    --source-db-instance-identifier "$RDS_SOURCE"
    --target-db-instance-identifier "$name"
    --db-subnet-group-name "$RDS_SUBNET_GROUP"
    --vpc-security-group-ids "${groups[@]}"
    --no-publicly-accessible --no-multi-az --no-deletion-protection
    --tags Key=purpose,Value=restore-drill
  )
  if [[ -n ${RDS_INSTANCE_CLASS:-} ]]; then
    options+=(--db-instance-class "$RDS_INSTANCE_CLASS")
  fi
  if [[ -n ${RDS_PARAMETER_GROUP:-} ]]; then
    options+=(--db-parameter-group-name "$RDS_PARAMETER_GROUP")
  fi
  if [[ -n $asked ]]; then
    when=(--restore-time "$(date -u -d "$asked" +%Y-%m-%dT%H:%M:%SZ)")
    moment=$asked
  else
    # The moment the latest restore reaches, read before asking for it: the restore lands at
    # it or a few seconds after, so the check's moment is never past the copy's.
    moment=$(aws rds describe-db-instances --db-instance-identifier "$RDS_SOURCE" \
      --query 'DBInstances[0].LatestRestorableTime' --output text)
    moment=$(date -u -d "$moment" '+%Y-%m-%d %H:%M:%S+00')
    when=(--use-latest-restorable-time)
  fi
  aws rds restore-db-instance-to-point-in-time "${options[@]}" "${when[@]}" >/dev/null
  # Asked for in a loop rather than through the CLI's waiter, which gives up after 30 minutes,
  # and at once, without a pause, for an instance that failed.
  local deadline=$((SECONDS + ${RDS_WAIT_LIMIT:-21600}))
  while true; do
    state=$(state_of "$name") || return 1
    [[ $state == available ]] && break
    case $state in
      '' | creating | backing-up | modifying | rebooting | storage-optimization | upgrading | \
        configuring-* | resetting-master-credentials | renaming) ;;
      *)
        echo "The instance $name is $state, not available: the restore failed." >&2
        return 1
        ;;
    esac
    if ((SECONDS >= deadline)); then
      echo "The instance $name was still ${state:-not there} after ${RDS_WAIT_LIMIT:-21600} s" \
        "(RDS_WAIT_LIMIT)." >&2
      return 1
    fi
    sleep "$poll"
  done
  local host
  host=$(aws rds describe-db-instances --db-instance-identifier "$name" \
    --query 'DBInstances[0].Endpoint.Address' --output text)
  echo "DATABASE_URL=${RDS_URL//HOST/$host}"
  echo "DRILL_MOMENT=$moment"
}

discard() {
  local name=$1 state deadline=$((SECONDS + 3600))
  # An instance being created or changed cannot be deleted yet; one that failed can.
  while true; do
    state=$(state_of "$name") || return 1
    case $state in
      '' | deleting) return 0 ;;
      creating | backing-up | modifying | rebooting | storage-optimization | upgrading | \
        configuring-* | resetting-master-credentials | renaming)
        if ((SECONDS >= deadline)); then
          echo "The instance $name was still $state after an hour." >&2
          return 1
        fi
        sleep "$poll"
        ;;
      *) break ;;
    esac
  done
  aws rds delete-db-instance --db-instance-identifier "$name" \
    --skip-final-snapshot --delete-automated-backups >/dev/null
  echo "Deleting the instance $name."
}

(($# >= 2)) || usage
case $1 in
  restore)
    (($# <= 3)) || usage
    restore "${@:2}"
    ;;
  discard)
    (($# == 2)) || usage
    discard "$2"
    ;;
  *) usage ;;
esac
