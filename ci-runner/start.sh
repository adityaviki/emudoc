#!/usr/bin/env bash
# A runner's start (compose.yml): registered with the repository the first time, from the
# registration token in RUNNER_TOKEN, which lasts an hour and is asked for with
#
#   gh api -X POST repos/emudocai/emudoc/actions/runners/registration-token --jq .token
#
# and run from then on with the registration kept in its volume, so a restart needs no token.
#
#   start.sh NAME
set -euo pipefail
name=${1:?"start.sh takes the name of the runner."}
cd /opt/actions-runner

if [[ ! -f .runner ]]; then
  if [[ -z ${RUNNER_TOKEN:-} ]]; then
    echo "Runner $name is not registered yet: start it once with RUNNER_TOKEN set" \
      "(ci-runner/compose.yml says how)." >&2
    exit 1
  fi
  ./config.sh --unattended --replace --name "$name" --labels "$RUNNER_LABELS" \
    --url "https://github.com/$RUNNER_REPOSITORY" --token "$RUNNER_TOKEN" \
    --work /home/runner/_work
fi

# The work directory is a volume of its own, which Docker makes as root.
sudo chown runner:runner /home/runner/_work
exec ./run.sh
