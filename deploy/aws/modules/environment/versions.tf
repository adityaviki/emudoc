# One Emudoc environment on AWS (deploying.md, "On AWS"): a network of its own, Postgres on
# RDS, the documents' bucket, and one EC2 instance that runs deploy/compose.yml from the image
# with Caddy in front. Staging and production are this module twice, with their own sizes.

terraform {
  required_version = ">= 1.12"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.69"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.9"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
data "aws_partition" "current" {}

locals {
  name      = "emudoc-${var.environment}"
  account   = data.aws_caller_identity.current.account_id
  region    = data.aws_region.current.region
  partition = data.aws_partition.current.partition
  # Every parameter the instance reads lies under this path: the settings the module writes,
  # the secrets the operator puts beside them, and the release to run.
  parameters = "/emudoc/${var.environment}"
}
