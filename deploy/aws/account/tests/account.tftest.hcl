# The account root against a mocked AWS (tofu test, in this root's directory): who may push an
# image, and with what, what the account's trail records, and the alarms on it. Nothing reaches
# AWS.

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
  # CloudTrail refuses a log group that is not named by an ARN.
  mock_resource "aws_cloudwatch_log_group" {
    defaults = { arn = "arn:aws:logs:eu-central-1:123456789012:log-group:emudoc-trail" }
  }
  mock_resource "aws_sns_topic" {
    defaults = { arn = "arn:aws:sns:eu-central-1:123456789012:emudoc-account-alarms" }
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
  override_resource {
    target = aws_iam_role.trail_logs
    values = { arn = "arn:aws:iam::123456789012:role/emudoc-trail-logs" }
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

  # The alarms: the trail in CloudWatch Logs too, under its key, and an alarm for each kind of
  # event, which mails the account's topic.
  assert {
    condition = (
      aws_cloudtrail.account.cloud_watch_logs_group_arn == "arn:aws:logs:eu-central-1:123456789012:log-group:emudoc-trail:*"
      && aws_cloudtrail.account.cloud_watch_logs_role_arn == "arn:aws:iam::123456789012:role/emudoc-trail-logs"
      && aws_cloudwatch_log_group.trail.retention_in_days == 30
      && aws_cloudwatch_log_group.trail.kms_key_id == aws_kms_key.trail.arn
    )
    error_message = "The trail goes to its log group, kept trail_log_days and encrypted with the trail's key."
  }
  assert {
    condition = one([
      for statement in jsondecode(aws_kms_key.trail.policy).Statement : statement.Condition
      if statement.Sid == "TheLogGroupEncrypts"
    ]) == { ArnEquals = { "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:eu-central-1:123456789012:log-group:emudoc-trail" } }
    error_message = "CloudWatch Logs uses the trail's key for the trail's log group alone."
  }
  assert {
    condition = (
      jsondecode(aws_iam_role.trail_logs.assume_role_policy).Statement[0].Principal == { Service = "cloudtrail.amazonaws.com" }
      && local.trail_logs_policy.Statement == [{
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:eu-central-1:123456789012:log-group:emudoc-trail:log-stream:123456789012_CloudTrail_eu-central-1*"
      }]
    )
    error_message = "CloudTrail writes the account's streams into the trail's log group and nothing else."
  }
  assert {
    condition = toset(keys(aws_cloudwatch_metric_alarm.trail)) == toset([
      "root", "console-without-mfa", "console-failures", "refused",
      "iam", "trail", "keys", "buckets", "security-groups", "network",
    ]) && keys(aws_cloudwatch_metric_alarm.trail) == keys(aws_cloudwatch_log_metric_filter.trail)
    error_message = "CIS's events each have a metric filter and an alarm."
  }
  assert {
    condition = alltrue([
      for name, filter in aws_cloudwatch_log_metric_filter.trail :
      length(filter.pattern) <= 1024 && filter.log_group_name == "emudoc-trail"
      && one(filter.metric_transformation).name == name && one(filter.metric_transformation).namespace == "Emudoc/Trail"
    ])
    error_message = "Each filter fits CloudWatch Logs' 1,024 characters and counts into a metric of its own."
  }
  assert {
    condition = (
      aws_cloudwatch_log_metric_filter.trail["root"].pattern == "{ $.userIdentity.type = \"Root\" && $.userIdentity.invokedBy NOT EXISTS && $.eventType != \"AwsServiceEvent\" }"
      && startswith(aws_cloudwatch_log_metric_filter.trail["keys"].pattern, "{ $.eventSource = \"kms.amazonaws.com\" && ($.eventName = \"DisableKey\" || ")
      && strcontains(aws_cloudwatch_log_metric_filter.trail["iam"].pattern, "$.eventName = \"UpdateAssumeRolePolicy\"")
    )
    error_message = "The filters are CIS's patterns over the trail's JSON."
  }
  assert {
    condition = alltrue([
      for name, alarm in aws_cloudwatch_metric_alarm.trail :
      alarm.namespace == "Emudoc/Trail" && alarm.metric_name == name && alarm.statistic == "Sum"
      && alarm.period == 300 && alarm.comparison_operator == "GreaterThanOrEqualToThreshold"
      && alarm.treat_missing_data == "notBreaching"
      && alarm.alarm_actions == toset(["arn:aws:sns:eu-central-1:123456789012:emudoc-account-alarms"])
    ]) && aws_cloudwatch_metric_alarm.trail["root"].threshold == 1 && aws_cloudwatch_metric_alarm.trail["refused"].threshold == 10
    error_message = "One event in five minutes sets an alarm off, ten refused calls, and each mails the account's topic."
  }
  assert {
    condition = (
      aws_sns_topic.alarms.kms_master_key_id == aws_kms_key.alarms.arn
      && aws_kms_key.alarms.enable_key_rotation
      && one([
        for statement in jsondecode(aws_kms_key.alarms.policy).Statement : statement.Principal
        if statement.Sid == "CloudWatchAlarms"
      ]) == { Service = "cloudwatch.amazonaws.com" }
      && length(aws_sns_topic_subscription.alarms) == 0
    )
    error_message = "The account's topic is encrypted with a key CloudWatch may publish with, and subscribes no address unasked."
  }
}

run "an_address_for_the_alarms" {
  command = plan
  override_module {
    target = module.shared
    outputs = {
      github_repository   = "emudocai/emudoc"
      account_alert_email = "operator@example.com"
    }
  }
  assert {
    condition     = aws_sns_topic_subscription.alarms[0].endpoint == "operator@example.com"
    error_message = "shared/'s account_alert_email subscribes the address to the account's alarms."
  }
}

run "trail_logs_kept_a_period_cloudwatch_does_not_offer" {
  command = plan
  variables {
    trail_log_days = 10
  }
  expect_failures = [var.trail_log_days]
}

run "a_trail_kept_no_days" {
  command = plan
  variables {
    trail_days = 0
  }
  expect_failures = [var.trail_days]
}
