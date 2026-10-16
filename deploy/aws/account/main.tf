# Once per AWS account, before the environments (deploying.md, "On AWS"): the bucket their
# state is kept in, the key OpenTofu encrypts that state with, and the image repository the
# environments pull from. Its own state holds no secret and stays in this directory
# (terraform.tfstate, kept out of git), or moves into the bucket with `tofu init
# -migrate-state` once the bucket exists.
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

provider "aws" {
  region = var.region

  default_tags {
    tags = { app = "emudoc" }
  }
}

data "aws_caller_identity" "current" {}

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

output "state_bucket" {
  description = "For each environment's `tofu init -backend-config=bucket=…`."
  value       = aws_s3_bucket.state.bucket
}

output "repository" {
  description = "Where deploy/aws/push-image.sh pushes."
  value       = aws_ecr_repository.app.repository_url
}
