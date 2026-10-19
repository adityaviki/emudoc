# Where the environment reports what goes wrong (deploying.md, "Alerts"), three ways that cover
# each other's blind spots:
#
# - on the instance, the host's units (deploy/aws/host): a restore drill, a credentials refresh
#   or a start that failed, and a container that stopped or whose health check fails
#   (deploy/aws/host/watch), published with the instance's role;
# - on the instance, Prometheus with the alerting rules of deploy/prometheus, and Alertmanager,
#   which publishes what fires with a role of its own (deploy/aws/compose.yml, iam.tf), and
#   every two minutes its watchdog to the heartbeat topic;
# - outside it, CloudWatch: the heartbeat's silence, which is the instance, its Docker,
#   Prometheus, Alertmanager or their credentials gone, and the instance's own status checks.
#
# Everything reaches the alerts topic. What cannot wait, a member waiting now or nothing on the
# instance able to say so, reaches the pages topic as well, for a pager the operator subscribes
# by hand (deploying.md, "On call"): Prometheus's critical rules, a container in trouble, and the
# three alarms. A unit that failed (the drill, a renewal of the credentials, the boot) is for the
# working day: the renewal has an hour's margin, and what a failed renewal or boot leads to is
# the silence's alarm, which pages.
#
# The messages name units, services, rules and the instance, never a log line, which may name a
# document. The app mails its own reports to the same address (ALERT_EMAIL, settings.tf).

locals {
  # The topics are encrypted with a key of the environment's own, since SNS's AWS-managed key
  # lets no AWS service use it, and CloudWatch's alarms publish here: the account's
  # administrators decide through IAM as for every other key, the publishers may use it through
  # SNS alone, and CloudWatch may encrypt its alarms' messages with it.
  alerts_key_policy = {
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
        Sid       = "PublishersThroughSns"
        Effect    = "Allow"
        Principal = { AWS = [aws_iam_role.instance.arn, aws_iam_role.alertmanager.arn] }
        Action    = ["kms:GenerateDataKey*", "kms:Decrypt"]
        Resource  = "*"
        Condition = { StringEquals = { "kms:ViaService" = "sns.${local.region}.amazonaws.com" } }
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

  # Alertmanager sends its watchdog every two minutes (deploy/aws/alertmanager.yml), so three
  # five-minute periods without one are a silence and not a late message.
  heartbeat_period  = 300
  heartbeat_periods = 3
}

resource "aws_kms_key" "alerts" {
  description             = "Emudoc ${var.environment}: the alerts topics"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy                  = jsonencode(local.alerts_key_policy)
}

resource "aws_kms_alias" "alerts" {
  name          = "alias/${local.name}-alerts"
  target_key_id = aws_kms_key.alerts.key_id
}

resource "aws_sns_topic" "alerts" {
  name              = "${local.name}-alerts"
  kms_master_key_id = aws_kms_key.alerts.arn
}

resource "aws_sns_topic_subscription" "alerts" {
  count = var.alert_email == "" ? 0 : 1

  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# Nobody is subscribed here by the module: a paging service's address carries its key, which the
# repository, the plan and the state would hold, so the operator subscribes it, or a phone
# number, with the AWS CLI (deploying.md, "On call").
resource "aws_sns_topic" "pages" {
  name              = "${local.name}-pages"
  kms_master_key_id = aws_kms_key.alerts.arn
}

# Nobody subscribes: what CloudWatch counts of it is the proof that an alert would arrive.
resource "aws_sns_topic" "heartbeat" {
  name              = "${local.name}-heartbeat"
  kms_master_key_id = aws_kms_key.alerts.arn
}

# Missing data is breaching: a topic nobody publishes to has no data at all, and before the first
# release this is in alarm, as no alert could arrive yet.
resource "aws_cloudwatch_metric_alarm" "silent" {
  alarm_name        = "${local.name}-alerts-silent"
  alarm_description = "No heartbeat from Alertmanager on the instance for ${local.heartbeat_period * local.heartbeat_periods / 60} minutes, so no alert of Prometheus's would arrive either: the instance, its Docker, Prometheus or Alertmanager stopped, or Alertmanager's credentials were not renewed. aws ec2 describe-instance-status shows the instance; on it (aws ssm start-session), sudo emudoc-compose ps prometheus alertmanager and systemctl status emudoc-credentials say which (deploying.md, \"Alerts\")."

  namespace           = "AWS/SNS"
  metric_name         = "NumberOfMessagesPublished"
  dimensions          = { TopicName = aws_sns_topic.heartbeat.name }
  statistic           = "Sum"
  period              = local.heartbeat_period
  evaluation_periods  = local.heartbeat_periods
  datapoints_to_alarm = local.heartbeat_periods
  comparison_operator = "LessThanThreshold"
  threshold           = 1
  treat_missing_data  = "breaching"

  alarm_actions = [aws_sns_topic.alerts.arn, aws_sns_topic.pages.arn]
  ok_actions    = [aws_sns_topic.alerts.arn, aws_sns_topic.pages.arn]
}

# The hardware or AWS's network under the instance: EC2 moves the instance to other hardware,
# with its address, disk and identity, and Docker starts the containers again.
resource "aws_cloudwatch_metric_alarm" "system" {
  alarm_name        = "${local.name}-instance-system"
  alarm_description = "The instance failed AWS's system status check for 2 minutes, so EC2 recovers it onto other hardware. Should it fail again, replace it: tofu apply -replace=module.emudoc.aws_instance.app (deploying.md, \"On AWS\")."

  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed_System"
  dimensions          = { InstanceId = aws_instance.app.id }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1

  alarm_actions = ["arn:${local.partition}:automate:${local.region}:ec2:recover", aws_sns_topic.alerts.arn, aws_sns_topic.pages.arn]
  ok_actions    = [aws_sns_topic.alerts.arn, aws_sns_topic.pages.arn]
}

# The instance's own system: out of memory, a kernel that hangs, a disk it cannot mount. A
# reboot runs the recorded release again (emudoc-boot.service).
resource "aws_cloudwatch_metric_alarm" "instance" {
  alarm_name        = "${local.name}-instance-status"
  alarm_description = "The instance failed its own status check for 3 minutes (out of memory, a kernel that hangs, a full or broken disk), so EC2 reboots it. Its console output says why: aws ec2 get-console-output --latest --instance-id ${aws_instance.app.id}; after the reboot, journalctl --boot=-1 on it."

  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed_Instance"
  dimensions          = { InstanceId = aws_instance.app.id }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 3
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1

  alarm_actions = ["arn:${local.partition}:automate:${local.region}:ec2:reboot", aws_sns_topic.alerts.arn, aws_sns_topic.pages.arn]
  ok_actions    = [aws_sns_topic.alerts.arn, aws_sns_topic.pages.arn]
}
