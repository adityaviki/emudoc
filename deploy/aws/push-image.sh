#!/usr/bin/env bash
# Pushes the image of the commit checked out to the account's ECR repository, tagged with the
# commit's full SHA (deploying.md, "Releases from CI"), unless the repository holds that tag
# already: its tags are immutable, so a tag is one build for good, and staging and production
# run the same bytes. CI's Image job runs it on main with the image it built, smoke-tested and
# scanned; the repository takes images from CI's role alone, unless the account root names
# another in `pushers`.
#
#   deploy/aws/push-image.sh IMAGE    pushes IMAGE, built from the commit checked out
#   deploy/aws/push-image.sh          builds it first
#
# Either way it prints the tag, for deploy/aws/release.sh. It needs Docker, git, and the AWS CLI
# with credentials allowed to push to the repository (ecr:GetAuthorizationToken,
# ecr:DescribeRepositories, ecr:DescribeImages and the layer uploads), its region from
# AWS_REGION or the CLI's own configuration.

set -euo pipefail
cd "$(dirname "$0")/../.."

if (($# > 1)); then
  echo "usage: $0 [IMAGE]" >&2
  exit 2
fi
if [[ -n $(git status --porcelain) ]]; then
  echo "The checkout has changes, and an image is built from a commit as it stands: commit" \
    "or stash them first." >&2
  exit 1
fi
tag=$(git rev-parse HEAD)
image=${1:-}
# The version the image reports (the Dockerfile's VERSION) is the tag release.sh waits for.
if [[ -n $image ]]; then
  revision=$(docker image inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image")
  if [[ $revision != "$tag" ]]; then
    echo "$image was built as $revision, not as the commit checked out, $tag." >&2
    exit 1
  fi
fi
name=${EMUDOC_REPOSITORY_NAME:-emudoc}
repository=$(aws ecr describe-repositories --repository-names "$name" \
  --query 'repositories[0].repositoryUri' --output text)

# Whether the tag is there; any other failure, such as credentials that ran out, fails.
if found=$(aws ecr describe-images --repository-name "$name" --image-ids "imageTag=$tag" \
  --query 'imageDetails[0].imageTags' --output text 2>&1); then
  echo "$repository:$tag is pushed already." >&2
  echo "$tag"
  exit 0
elif [[ $found != *ImageNotFoundException* ]]; then
  echo "$found" >&2
  exit 1
fi

# Standard output carries the tag alone.
aws ecr get-login-password |
  docker login --username AWS --password-stdin "${repository%%/*}" >&2
if [[ -n $image ]]; then
  docker tag "$image" "$repository:$tag"
else
  # The instances are x86_64, whatever builds the image.
  docker build --platform linux/amd64 --build-arg "VERSION=$tag" --tag "$repository:$tag" . >&2
fi
docker push --quiet "$repository:$tag" >&2
echo "Pushed $repository:$tag." >&2
echo "$tag"
