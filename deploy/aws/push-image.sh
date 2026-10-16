#!/usr/bin/env bash
# Builds the image of the commit checked out and pushes it to the account's ECR repository,
# tagged with the commit (deploying.md, "On AWS"), unless the repository holds that tag
# already: its tags are immutable, so a tag is one build for good, and staging and production
# run the same bytes.
#
#   deploy/aws/push-image.sh        prints the tag, for deploy/aws/release.sh
#
# It needs Docker, git, and the AWS CLI with credentials allowed to push to the repository
# (ecr:GetAuthorizationToken, ecr:DescribeRepositories, ecr:DescribeImages and the layer
# uploads), its region from AWS_REGION or the CLI's own configuration.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [[ -n $(git status --porcelain) ]]; then
  echo "The checkout has changes, and an image is built from a commit as it stands: commit" \
    "or stash them first." >&2
  exit 1
fi
tag=$(git rev-parse --short HEAD)
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

aws ecr get-login-password | docker login --username AWS --password-stdin "${repository%%/*}"
# The instances are x86_64, whatever builds the image.
docker build --platform linux/amd64 --tag "$repository:$tag" .
docker push --quiet "$repository:$tag" >&2
echo "Pushed $repository:$tag." >&2
echo "$tag"
