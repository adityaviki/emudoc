#!/usr/bin/env bash
# The tools named in more than one place, at one version: uv, pnpm and Node in the Dockerfile
# and in each workflow that sets them up, Python in the Dockerfile and backend/.python-version,
# and the tools that come as images (trivy) in each workflow's env. Nothing moves them together
# (Dependabot moves only the digests of the Dockerfile's bases), so a change to one place that
# misses another fails here.
#
#   .github/scripts/check-versions.sh
set -euo pipefail
cd "$(dirname "$0")/../.."

found=$(
  sed -nE \
    -e 's/^FROM python:([0-9.]+)-.*/python \1 Dockerfile/p' \
    -e 's/^FROM (--platform=[^ ]+ )?node:([0-9]+)-.*/node \2 Dockerfile/p' \
    -e 's/.* pnpm@([0-9.]+).*/pnpm \1 Dockerfile/p' \
    -e 's/^COPY --from=ghcr\.io\/astral-sh\/uv:([0-9.]+)[@ ].*/uv \1 Dockerfile/p' \
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

# An image by name in a workflow's env, such as TRIVY, is the same image in every workflow.
images=$(awk '/^  [A-Z][A-Z_]*: [^ ]+@sha256:/ { sub(/:$/, "", $1); print $1, $2, FILENAME }' \
  .github/workflows/*.yml)
while read -r name; do
  places=$(grep "^$name " <<<"$images")
  if (($(cut -d' ' -f2 <<<"$places" | sort -u | wc -l) != 1)); then
    echo "$name is not one image in every workflow:"
    awk '{ print "  " $0 }' <<<"$places"
    status=1
  fi
done < <(cut -d' ' -f1 <<<"$images" | sort -u)

if ((status == 0)); then
  echo "$found"
  echo "$images"
fi
exit "$status"
