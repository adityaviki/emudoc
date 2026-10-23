#!/usr/bin/env bash
# Before and after each job (ACTIONS_RUNNER_HOOK_JOB_STARTED, _COMPLETED in compose.yml): the
# state a job of GitHub's starts from, on a machine that outlives it. GitHub's runner is a new
# virtual machine each time; this one keeps its Docker daemon, so what a job started there (the
# compose database, the image's smoke test, the embeddings server) is removed, with its
# networks, its volumes and the app's images, which are a gigabyte a commit. The images it
# pulled and the build cache of the last three days stay, which is what makes a job faster here.
set -euo pipefail

mapfile -t containers < <(docker ps -aq)
if ((${#containers[@]})); then
  docker rm -f "${containers[@]}" >/dev/null
fi
docker network prune -f >/dev/null
# Every volume but the embedding model's weights, which check-embeddings.sh downloads once and
# checks by their hash at every run.
docker volume ls -q | { grep -vx 'emudoc-embeddings-check_embedding-model' || true; } |
  xargs -r docker volume rm >/dev/null
docker image ls --format '{{.Repository}}:{{.Tag}}' | { grep '^emudoc:' || true; } |
  xargs -r docker image rm -f >/dev/null
docker image prune -f >/dev/null
docker builder prune -f --filter until=72h >/dev/null

# A file a container wrote into the checkout as root, which the next checkout could not clean.
sudo chown -R runner:runner /home/runner/_work
