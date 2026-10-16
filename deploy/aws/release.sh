#!/usr/bin/env bash
# Runs a release on an environment's instance (deploying.md, "On AWS"): Systems Manager runs
# emudoc-release TAG there, which pulls the image and runs the release it carries
# (deploy/aws/host/release); this waits for it, prints what it said, records the tag so that
# a new instance starts with it, and asks the app's health check over HTTPS.
#
#   deploy/aws/release.sh staging 3f2c1ab
#   deploy/aws/release.sh production 3f2c1ab     once staging ran it
#
# It needs the AWS CLI with credentials allowed ec2:DescribeInstances, ssm:SendCommand on the
# instance and the AWS-RunShellScript document, ssm:GetCommandInvocation, and
# ssm:GetParameter and ssm:PutParameter under /emudoc/ENVIRONMENT, its region from AWS_REGION
# or the CLI's own configuration; and curl.

set -euo pipefail

# How often the command's state is asked for.
poll=${RELEASE_POLL_SECONDS:-5}

if (($# != 2)); then
  echo "usage: $0 ENVIRONMENT TAG" >&2
  exit 2
fi
environment=$1 tag=$2
# A tag as Docker spells one, since it becomes part of a command on the instance.
if [[ ! $tag =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]]; then
  echo "$tag is not an image tag." >&2
  exit 2
fi

instances=$(aws ec2 describe-instances \
  --filters "Name=tag:Name,Values=emudoc-$environment" "Name=instance-state-name,Values=running" \
  --query 'Reservations[].Instances[].InstanceId' --output text)
read -ra instances <<<"$instances"
if ((${#instances[@]} != 1)); then
  echo "Found ${#instances[@]} running instances named emudoc-$environment, not one: is" \
    "the environment applied, and the instance started?" >&2
  exit 1
fi
instance=${instances[0]}

echo "Releasing $tag on $environment ($instance)."
command=$(aws ssm send-command --instance-ids "$instance" \
  --document-name AWS-RunShellScript --comment "Emudoc $tag" \
  --parameters "{\"commands\": [\"/usr/local/sbin/emudoc-release $tag\"], \"executionTimeout\": [\"3600\"]}" \
  --query Command.CommandId --output text)

while true; do
  sleep "$poll"
  # Not found for a moment after it was sent.
  if ! status=$(aws ssm get-command-invocation --command-id "$command" \
    --instance-id "$instance" --query Status --output text 2>/dev/null); then
    continue
  fi
  case $status in
    Pending | InProgress | Delayed) ;;
    *) break ;;
  esac
done

# What it printed: Systems Manager keeps the last 24,000 characters of each.
aws ssm get-command-invocation --command-id "$command" --instance-id "$instance" \
  --query StandardOutputContent --output text
aws ssm get-command-invocation --command-id "$command" --instance-id "$instance" \
  --query StandardErrorContent --output text >&2
if [[ $status != Success ]]; then
  echo "The release ended $status, and was not recorded; what it printed above says why." \
    "A migration that failed leaves the release before it serving; \`sudo emudoc-compose ps\`" \
    "on the instance shows what runs." >&2
  exit 1
fi

aws ssm put-parameter --name "/emudoc/$environment/release" --type String --value "$tag" \
  --overwrite >/dev/null
app_url=$(aws ssm get-parameter --name "/emudoc/$environment/settings/APP_URL" \
  --query Parameter.Value --output text)
# A first release waits for Caddy's certificate, which takes a few seconds once the domain
# leads to the instance.
if ! curl -fsS --max-time 10 --retry 12 --retry-delay 5 --retry-all-errors \
  -o /dev/null "$app_url/api/health"; then
  echo "$app_url/api/health does not answer: does the domain lead to the instance?" >&2
  exit 1
fi
echo "$tag is released on $environment, and $app_url is ready."
