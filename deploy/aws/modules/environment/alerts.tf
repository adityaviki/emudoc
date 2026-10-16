# Where the instance reports what it did not manage on its own: a restore drill that failed, a
# credentials refresh that failed (the containers lose the bucket within the hour), a start
# that could not run the release. The message names the unit and the instance, never a log
# line, which may name a document.

resource "aws_sns_topic" "alerts" {
  name              = "${local.name}-alerts"
  kms_master_key_id = "alias/aws/sns"
}

resource "aws_sns_topic_subscription" "alerts" {
  count = var.alert_email == "" ? 0 : 1

  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}
