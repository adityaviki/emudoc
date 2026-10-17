#!/usr/bin/env bash
# The tools named in more than one place, at one version: uv, pnpm and Node in the Dockerfile
# and in each workflow that sets them up, Python in the Dockerfile and backend/.python-version.
# Nothing moves them together (Dependabot moves none of them), so a change to one place that
# misses another fails here.
#
#   .github/scripts/check-versions.sh
set -euo pipefail
cd "$(dirname "$0")/../.."

found=$(
  sed -nE \
    -e 's/^ARG PYTHON_IMAGE=python:([0-9.]+)-.*/python \1 Dockerfile/p' \
    -e 's/^FROM node:([0-9]+)-.*/node \1 Dockerfile/p' \
    -e 's/.* pnpm@([0-9.]+).*/pnpm \1 Dockerfile/p' \
    -e 's/^COPY --from=ghcr\.io\/astral-sh\/uv:([0-9.]+) .*/uv \1 Dockerfile/p' \
    Dockerfile
  echo "python $(<backend/.python-version) backend/.python-version"
  # The version an action is given is the first version: or node-version: of its step.
  awk '
    /uses: astral-sh\/setup-uv@/ { tool = "uv"; next }
    /uses: pnpm\/action-setup@/ { tool = "pnpm"; next }
    /uses: actions\/setup-node@/ { tool = "node"; next }
    /^ *- / { tool = "" }
    tool != "" && /^ *(node-)?version: / {
      gsub(/"/, "", $2)
      print tool, $2, FILENAME
      tool = ""
    }
  ' .github/workflows/*.yml
)

status=0
for tool in python uv pnpm node; do
  places=$(grep "^$tool " <<<"$found" || true)
  versions=$(cut -d' ' -f2 <<<"$places" | sort -u | wc -l)
  # Each is set for the image in the Dockerfile; missing there, or named nowhere else, a
  # pattern above no longer matches its file.
  if ! grep -q ' Dockerfile$' <<<"$places" || (($(wc -l <<<"$places") < 2 || versions != 1)); then
    echo "$tool is not named at one version in the Dockerfile and elsewhere:"
    awk '{ print "  " $0 }' <<<"$places"
    status=1
  fi
done
if ((status == 0)); then
  echo "$found"
fi
exit "$status"
