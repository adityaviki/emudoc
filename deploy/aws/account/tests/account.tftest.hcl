# The account root against a mocked AWS (tofu test, in this root's directory): who may push an
# image, and with what, and what the account's trail records. Nothing reaches AWS.

mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  mock_data "aws_partition" {
    defaults = { partition = "aws" }
  }
  mock_resource "aws_ecr_repository" {
    defaults = {
      arn            = "arn:aws:ecr:eu-central-1:123456789012:repository/emudoc"
      repository_url = "123456789012.dkr.ecr.eu-central-1.amazonaws.com/emudoc"
    }
  }
  mock_resource "aws_iam_openid_connect_provider" {
    defaults = { arn = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com" }
  }
  # CloudTrail refuses a key that is not named by an ARN.
  mock_resource "aws_kms_key" {
    defaults = { arn = "arn:aws:kms:eu-central-1:123456789012:key/trail" }
  }
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::123456789012:role/emudoc-github-push" }
  }
}

run "ci_alone" {
  assert {
    condition = jsondecode(aws_iam_role.push.assume_role_policy).Statement[0] == {
      Effect    = "Allow"
      Action    = "sts:AssumeRoleWithWebIdentity"
      Principal = { Federated = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com" }
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = "repo:emudocai/emudoc:ref:refs/heads/main"
        }
      }
    }
    error_message = "Only a job of a workflow on the repository's main takes on the push role."
  }
  assert {
    condition = (
      aws_iam_openid_connect_provider.github.url == "https://token.actions.githubusercontent.com"
      && aws_iam_openid_connect_provider.github.client_id_list == toset(["sts.amazonaws.com"])
    )
    error_message = "AWS takes GitHub's tokens meant for STS."
  }
  assert {
    condition = one([
      for statement in local.push_policy.Statement : statement.Resource if statement.Sid == "Push"
    ]) == "arn:aws:ecr:eu-central-1:123456789012:repository/emudoc"
    error_message = "CI pushes to this repository alone."
  }
  assert {
    condition = jsondecode(aws_ecr_repository_policy.app.policy).Statement == [{
      Sid       = "PushedByCIAlone"
      Effect    = "Deny"
      Principal = "*"
      Action    = ["ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage"]
      Resource  = "arn:aws:ecr:eu-central-1:123456789012:repository/emudoc"
      Condition = { ArnNotEquals = { "aws:PrincipalArn" = ["arn:aws:iam::123456789012:role/emudoc-github-push"] } }
    }]
    error_message = "The repository refuses an image from anyone but CI's role."
  }
  assert {
    condition     = aws_ecr_repository.app.image_tag_mutability == "IMMUTABLE"
    error_message = "A tag is one build for good."
  }
  assert {
    condition     = output.push_role == "arn:aws:iam::123456789012:role/emudoc-github-push"
    error_message = "The role is an output, for the repository variable."
  }
}

run "an_operator_while_github_is_down" {
  command = plan
  variables {
    pushers = ["arn:aws:iam::123456789012:role/operator"]
  }
  assert {
    condition = local.repository_policy.Statement[0].Condition.ArnNotEquals["aws:PrincipalArn"] == [
      "arn:aws:iam::123456789012:role/emudoc-github-push", "arn:aws:iam::123456789012:role/operator",
    ]
    error_message = "pushers lets another role push beside CI."
  }
}

run "the_trail" {
  override_resource {
    target = aws_s3_bucket.state
    values = { arn = "arn:aws:s3:::emudoc-state-123456789012" }
  }
  override_resource {
    target = aws_s3_bucket.trail
    values = { arn = "arn:aws:s3:::emudoc-trail-123456789012", id = "emudoc-trail-123456789012" }
  }

  assert {
    condition = (
      aws_cloudtrail.account.is_multi_region_trail
      && aws_cloudtrail.account.include_global_service_events
      && aws_cloudtrail.account.enable_log_file_validation
      && aws_cloudtrail.account.kms_key_id == "arn:aws:kms:eu-central-1:123456789012:key/trail"
      && aws_cloudtrail.account.s3_bucket_name == "emudoc-trail-123456789012"
    )
    error_message = "The trail records every region, signs its files and encrypts them with its own key, in its own bucket."
  }
  assert {
    condition = (
      length(aws_cloudtrail.account.advanced_event_selector) == 2
      && one(aws_cloudtrail.account.advanced_event_selector[0].field_selector).field == "eventCategory"
      && one(aws_cloudtrail.account.advanced_event_selector[0].field_selector).equals == tolist(["Management"])
    )
    error_message = "The trail records every call to the APIs."
  }
  assert {
    condition = {
      for field in aws_cloudtrail.account.advanced_event_selector[1].field_selector :
      field.field => field.equals != null ? field.equals : field.starts_with
      } == {
      eventCategory    = tolist(["Data"])
      "resources.type" = tolist(["AWS::S3::Object"])
      "resources.ARN"  = tolist(["arn:aws:s3:::emudoc-state-123456789012/"])
    }
    error_message = "The trail records every request for an object of the state bucket, and of no other."
  }
  assert {
    condition = one([
      for statement in jsondecode(aws_kms_key.trail.policy).Statement : statement
      if statement.Sid == "CloudTrailEncrypts"
      ]) == {
      Sid       = "CloudTrailEncrypts"
      Effect    = "Allow"
      Principal = { Service = "cloudtrail.amazonaws.com" }
      Action    = "kms:GenerateDataKey*"
      Resource  = "*"
      Condition = {
        StringEquals = { "aws:SourceArn" = "arn:aws:cloudtrail:eu-central-1:123456789012:trail/emudoc" }
        StringLike   = { "kms:EncryptionContext:aws:cloudtrail:arn" = "arn:aws:cloudtrail:*:123456789012:trail/*" }
      }
    }
    error_message = "CloudTrail encrypts with the key for this trail alone."
  }
  assert {
    condition = one([
      for statement in jsondecode(aws_s3_bucket_policy.trail.policy).Statement : statement
      if statement.Sid == "CloudTrailWrites"
      ]) == {
      Sid       = "CloudTrailWrites"
      Effect    = "Allow"
      Principal = { Service = "cloudtrail.amazonaws.com" }
      Action    = "s3:PutObject"
      Resource  = "arn:aws:s3:::emudoc-trail-123456789012/AWSLogs/123456789012/*"
      Condition = {
        StringEquals = {
          "aws:SourceArn" = "arn:aws:cloudtrail:eu-central-1:123456789012:trail/emudoc"
          "s3:x-amz-acl"  = "bucket-owner-full-control"
        }
      }
    }
    error_message = "Only this trail writes to its bucket, under the account's own prefix."
  }
  assert {
    condition = (
      jsondecode(aws_s3_bucket_policy.trail.policy).Statement[0].Condition.Bool["aws:SecureTransport"] == "false"
      && aws_s3_bucket_public_access_block.trail.block_public_policy
      && aws_s3_bucket_public_access_block.trail.restrict_public_buckets
    )
    error_message = "The trail's bucket is never public and refuses a request without TLS."
  }
  assert {
    condition     = aws_s3_bucket_lifecycle_configuration.trail.rule[0].expiration[0].days == 365
    error_message = "The trail's files are kept for trail_days."
  }
}

run "a_trail_kept_no_days" {
  command = plan
  variables {
    trail_days = 0
  }
  expect_failures = [var.trail_days]
}
