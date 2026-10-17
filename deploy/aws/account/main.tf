# Once per AWS account, before the environments (deploying.md, "On AWS"): the bucket their
# state is kept in, the key OpenTofu encrypts that state with, the image repository the
# environments pull from, and the way GitHub's workflows on main sign in to push to it, which
# nothing else may (deploying.md, "Releases from CI"). Its own state holds no secret and stays
# in this directory (terraform.tfstate, kept out of git), or moves into the bucket with `tofu
# init -migrate-state` once the bucket exists.
#
#   tofu -chdir=deploy/aws/account init
#   tofu -chdir=deploy/aws/account apply

terraform {
  required_version = ">= 1.12"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.69"
    }
  }
}

variable "region" {
  description = "The region the state, the repository and the environments are in."
  type        = string
  default     = "eu-central-1"
}

variable "pushers" {
  description = "Roles besides CI's that may push an image, by ARN: an operator's, while GitHub cannot run the workflows (deploying.md, \"Releases from CI\"). Empty is CI alone."
  type        = list(string)
  default     = []
}

provider "aws" {
  region = var.region

  default_tags {
    tags = { app = "emudoc" }
  }
}

data "aws_caller_identity" "current" {}

module "shared" {
  source = "../shared"
}

# The environments' state: versioned, so an apply gone wrong can be undone, private, and over
# TLS only. What it holds (the database's password, SES's SMTP key) is encrypted again by
# OpenTofu itself before it leaves the operator's machine, with the key below.
resource "aws_s3_bucket" "state" {
  bucket = "emudoc-state-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket = aws_s3_bucket.state.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.state.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    id     = "earlier-states"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }

  depends_on = [aws_s3_bucket_versioning.state]
}

resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "TLSOnly"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })

  depends_on = [aws_s3_bucket_public_access_block.state]
}

# The key the roots' `encryption` block names by its alias.
resource "aws_kms_key" "state" {
  description             = "Emudoc: OpenTofu's state"
  enable_key_rotation     = true
  deletion_window_in_days = 30
}

resource "aws_kms_alias" "state" {
  name          = "alias/emudoc-state"
  target_key_id = aws_kms_key.state.key_id
}

# One build per tag for good (immutable), so that staging and production run the same bytes;
# scanned by ECR as it is pushed.
resource "aws_ecr_repository" "app" {
  name                 = "emudoc"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "The newest 30 images; the instances keep the ones they run."
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 30
      }
      action = { type = "expire" }
    }]
  })
}

# GitHub's OpenID Connect provider: a workflow's job presents a token GitHub signed that names
# the repository and the branch, and takes on a role that trusts those for an hour, so that no
# AWS key is kept in GitHub. AWS checks GitHub's certificate against its own authorities, so no
# thumbprint is pinned. The environments' release roles trust it too (modules/environment).
resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

locals {
  # A job of a workflow on main, a push or a run by hand. A pull request's token says
  # pull_request, another branch's names it, and neither is taken.
  github_main = "repo:${module.shared.github_repository}:ref:refs/heads/main"

  push_trust = {
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRoleWithWebIdentity"
      Principal = { Federated = aws_iam_openid_connect_provider.github.arn }
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = local.github_main
        }
      }
    }]
  }

  # What deploy/aws/push-image.sh and `docker push` ask, on this repository alone.
  push_policy = {
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "SignInToTheRegistry"
        Effect   = "Allow"
        Action   = "ecr:GetAuthorizationToken"
        Resource = "*"
      },
      {
        Sid    = "Push"
        Effect = "Allow"
        Action = [
          "ecr:DescribeRepositories",
          "ecr:DescribeImages",
          "ecr:BatchGetImage",
          "ecr:BatchCheckLayerAvailability",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload",
          "ecr:PutImage",
        ]
        Resource = aws_ecr_repository.app.arn
      },
    ]
  }

  # An image reaches the repository from CI's role alone, the account's administrators
  # included, so that what an environment runs was built from a commit on main, checked and
  # scanned by CI. Changing this policy is the way past it, and `pushers` says whom for.
  repository_policy = {
    Version = "2012-10-17"
    Statement = [{
      Sid       = "PushedByCIAlone"
      Effect    = "Deny"
      Principal = "*"
      Action = [
        "ecr:InitiateLayerUpload",
        "ecr:UploadLayerPart",
        "ecr:CompleteLayerUpload",
        "ecr:PutImage",
      ]
      Resource  = aws_ecr_repository.app.arn
      Condition = { ArnNotEquals = { "aws:PrincipalArn" = concat([aws_iam_role.push.arn], var.pushers) } }
    }]
  }
}

resource "aws_iam_role" "push" {
  name                 = "emudoc-github-push"
  assume_role_policy   = jsonencode(local.push_trust)
  max_session_duration = 3600
}

resource "aws_iam_role_policy" "push" {
  name   = "push"
  role   = aws_iam_role.push.id
  policy = jsonencode(local.push_policy)
}

resource "aws_ecr_repository_policy" "app" {
  repository = aws_ecr_repository.app.name
  policy     = jsonencode(local.repository_policy)
}

output "state_bucket" {
  description = "For each environment's `tofu init -backend-config=bucket=…`."
  value       = aws_s3_bucket.state.bucket
}

output "repository" {
  description = "Where deploy/aws/push-image.sh pushes."
  value       = aws_ecr_repository.app.repository_url
}

output "push_role" {
  description = "The role CI's Image job pushes with: the repository variable AWS_PUSH_ROLE."
  value       = aws_iam_role.push.arn
}
