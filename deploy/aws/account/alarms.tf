# Alarms on the account's own record (deploying.md, "The account's records"): the trail goes to
# CloudWatch Logs as well as to its bucket, and a metric filter for each kind of event that
# should not happen unannounced, after the monitoring section of CIS's AWS Foundations
# Benchmark, counts it into an alarm that mails alert_email: the root user at work, a console
# sign-in without a second factor, failed sign-ins and refused calls in a burst, and a change
# to IAM, the trail, a key, a bucket's policy, a security group or the network. The operators'
# own `tofu apply` sets off the changes, which is the point: the message says what kind of change
# was made, and one that nobody applied is the one to look into. The address they go to is
# shared/'s account_alert_email.

variable "trail_log_days" {
  description = "How many days CloudWatch Logs keeps its copy of the trail, which the alarms read as it arrives; the trail's bucket keeps its files trail_days."
  type        = number
  default     = 30

  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653], var.trail_log_days)
    error_message = "trail_log_days is one of the periods CloudWatch Logs keeps a log for: 1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365 days, or more."
  }
}

locals {
  trail_log_group = "emudoc-trail"
  # Written out, since the trail's key names it before it is made.
  trail_log_group_arn = "arn:${local.partition}:logs:${var.region}:${local.account}:log-group:${local.trail_log_group}"

  # Each kind of change by the events that make it, from the service that records them.
  trail_changes = {
    iam = {
      source = "iam.amazonaws.com"
      what   = "IAM changed a policy, a role's trust, a user or an access key"
      events = [
        "DeleteGroupPolicy", "DeleteRolePolicy", "DeleteUserPolicy", "PutGroupPolicy", "PutRolePolicy",
        "PutUserPolicy", "CreatePolicy", "DeletePolicy", "CreatePolicyVersion", "DeletePolicyVersion",
        "SetDefaultPolicyVersion", "AttachRolePolicy", "DetachRolePolicy", "AttachUserPolicy",
        "DetachUserPolicy", "AttachGroupPolicy", "DetachGroupPolicy", "UpdateAssumeRolePolicy",
        "CreateUser", "CreateAccessKey", "CreateLoginProfile", "UpdateLoginProfile",
      ]
    }
    trail = {
      source = "cloudtrail.amazonaws.com"
      what   = "A trail was made, changed, stopped or deleted"
      events = ["CreateTrail", "UpdateTrail", "DeleteTrail", "StartLogging", "StopLogging", "PutEventSelectors"]
    }
    keys = {
      source = "kms.amazonaws.com"
      what   = "A key was turned off, set to be deleted, or given another policy"
      events = ["DisableKey", "ScheduleKeyDeletion", "PutKeyPolicy"]
    }
    buckets = {
      source = "s3.amazonaws.com"
      what   = "A bucket's policy, public access, versioning, encryption, lifecycle or replication changed"
      events = [
        "PutBucketAcl", "PutBucketPolicy", "DeleteBucketPolicy", "PutBucketPublicAccessBlock",
        "DeleteBucketPublicAccessBlock", "PutBucketCors", "DeleteBucketCors", "PutBucketLifecycle",
        "DeleteBucketLifecycle", "PutBucketReplication", "DeleteBucketReplication", "PutBucketVersioning",
        "PutBucketEncryption", "DeleteBucketEncryption",
      ]
    }
    security-groups = {
      source = "ec2.amazonaws.com"
      what   = "A security group was made, changed or deleted"
      events = [
        "AuthorizeSecurityGroupIngress", "AuthorizeSecurityGroupEgress", "RevokeSecurityGroupIngress",
        "RevokeSecurityGroupEgress", "CreateSecurityGroup", "DeleteSecurityGroup", "ModifySecurityGroupRules",
      ]
    }
    network = {
      source = "ec2.amazonaws.com"
      what   = "A network, its routes, gateways, network ACLs or peering changed"
      events = [
        "CreateNetworkAcl", "CreateNetworkAclEntry", "DeleteNetworkAcl", "DeleteNetworkAclEntry",
        "ReplaceNetworkAclEntry", "ReplaceNetworkAclAssociation", "CreateInternetGateway",
        "DeleteInternetGateway", "AttachInternetGateway", "DetachInternetGateway", "CreateRoute",
        "DeleteRoute", "ReplaceRoute", "CreateRouteTable", "DeleteRouteTable",
        "ReplaceRouteTableAssociation", "DisassociateRouteTable", "CreateVpc", "DeleteVpc",
        "ModifyVpcAttribute", "CreateVpcPeeringConnection", "AcceptVpcPeeringConnection",
        "DeleteVpcPeeringConnection",
      ]
    }
  }

  # Each alarm: its filter over the trail's JSON (CloudWatch Logs' pattern syntax, at most 1,024
  # characters), what it says, and how many such events in five minutes set it off.
  trail_alarms = merge(
    {
      root = {
        pattern   = "{ $.userIdentity.type = \"Root\" && $.userIdentity.invokedBy NOT EXISTS && $.eventType != \"AwsServiceEvent\" }"
        what      = "The account's root user made a call"
        threshold = 1
      }
      console-without-mfa = {
        pattern   = "{ $.eventName = \"ConsoleLogin\" && $.additionalEventData.MFAUsed != \"Yes\" && $.userIdentity.type = \"IAMUser\" && $.responseElements.ConsoleLogin = \"Success\" }"
        what      = "An IAM user signed in to the console without a second factor"
        threshold = 1
      }
      console-failures = {
        pattern   = "{ $.eventName = \"ConsoleLogin\" && $.errorMessage = \"Failed authentication\" }"
        what      = "Sign-ins to the console failed 3 times in 5 minutes"
        threshold = 3
      }
      refused = {
        pattern   = "{ $.errorCode = \"*UnauthorizedOperation\" || $.errorCode = \"AccessDenied*\" }"
        what      = "AWS refused 10 calls in 5 minutes for want of permission"
        threshold = 10
      }
    },
    {
      for name, change in local.trail_changes : name => {
        pattern   = "{ $.eventSource = \"${change.source}\" && (${join(" || ", [for event in change.events : "$.eventName = \"${event}\""])}) }"
        what      = change.what
        threshold = 1
      }
    },
  )

  # The account's administrators decide through IAM, as for every other key; CloudWatch may
  # encrypt its alarms' messages with it.
  alarms_key_policy = {
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "TheAccountDecides"
        Effect    = "Allow"
        Principal = { AWS = "arn:${local.partition}:iam::${local.account}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "CloudWatchAlarms"
        Effect    = "Allow"
        Principal = { Service = "cloudwatch.amazonaws.com" }
        Action    = ["kms:GenerateDataKey*", "kms:Decrypt"]
        Resource  = "*"
      },
    ]
  }

  # CloudTrail writes the account's streams into the group and does nothing else.
  trail_logs_policy = {
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
      Resource = "${local.trail_log_group_arn}:log-stream:${local.account}_CloudTrail_${var.region}*"
    }]
  }
}

# Encrypted with the trail's key, which lets CloudWatch Logs use it for this group alone.
resource "aws_cloudwatch_log_group" "trail" {
  name              = local.trail_log_group
  retention_in_days = var.trail_log_days
  kms_key_id        = aws_kms_key.trail.arn
}

resource "aws_iam_role" "trail_logs" {
  name = "emudoc-trail-logs"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "cloudtrail.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "trail_logs" {
  name   = "logs"
  role   = aws_iam_role.trail_logs.id
  policy = jsonencode(local.trail_logs_policy)
}

resource "aws_cloudwatch_log_metric_filter" "trail" {
  for_each = local.trail_alarms

  name           = "emudoc-${each.key}"
  log_group_name = aws_cloudwatch_log_group.trail.name
  pattern        = each.value.pattern

  metric_transformation {
    name      = each.key
    namespace = "Emudoc/Trail"
    value     = "1"
  }
}

resource "aws_kms_key" "alarms" {
  description             = "Emudoc: the account's alarms"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy                  = jsonencode(local.alarms_key_policy)
}

resource "aws_kms_alias" "alarms" {
  name          = "alias/emudoc-account-alarms"
  target_key_id = aws_kms_key.alarms.key_id
}

resource "aws_sns_topic" "alarms" {
  name              = "emudoc-account-alarms"
  kms_master_key_id = aws_kms_key.alarms.arn
}

resource "aws_sns_topic_subscription" "alarms" {
  count = module.shared.account_alert_email == "" ? 0 : 1

  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = module.shared.account_alert_email
}

# An event, not a state: nothing reports it over, and a period without one is no news.
resource "aws_cloudwatch_metric_alarm" "trail" {
  for_each = local.trail_alarms

  alarm_name        = "emudoc-account-${each.key}"
  alarm_description = "${each.value.what}. Who, from where and what: CloudTrail's event history in the console, or CloudWatch Logs Insights on the log group ${local.trail_log_group} with the filter of the metric filter emudoc-${each.key}. One the operators made themselves (a tofu apply) needs nothing (deploying.md, \"The account's records\")."

  namespace           = "Emudoc/Trail"
  metric_name         = each.key
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = each.value.threshold
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alarms.arn]

  depends_on = [aws_cloudwatch_log_metric_filter.trail]
}

output "alarms" {
  description = "The SNS topic the account's alarms publish to."
  value       = aws_sns_topic.alarms.arn
}
