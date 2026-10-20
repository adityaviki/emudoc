# Mail through Amazon SES, when var.mail names a domain: the domain verified by DKIM (its
# records in the zone, or listed by the dns_records output), a MAIL FROM domain of its own under
# it, so that SPF passes for the From domain too, and DMARC's record asking receivers to hold
# both to it (deploying.md, "Mail"); SMTP credentials that may send from it and do nothing else;
# and what SES says of the mail it could not deliver, which reaches the app through EventBridge
# and an SQS queue the worker reads (backend/app/bounces.py). A new SES account sends only to
# verified addresses until AWS grants production access, which is asked for by hand
# (deploying.md, "On AWS").

resource "aws_sesv2_email_identity" "mail" {
  count = var.mail == null ? 0 : 1

  email_identity = var.mail.domain
  # Every message from the domain is sent under it, the SMTP relay's included, which names none.
  configuration_set_name = aws_sesv2_configuration_set.mail[0].configuration_set_name

  # Easy DKIM: SES keeps the key and rotates it; the zone holds three CNAMEs to it.
  dkim_signing_attributes {
    next_signing_key_length = "RSA_2048_BIT"
  }
}

# The envelope's domain, where receivers send bounces (to SES, by its MX) and look up SPF. While
# its MX is missing SES falls back to its own domain, and DMARC rests on DKIM alone.
resource "aws_sesv2_email_identity_mail_from_attributes" "mail" {
  count = var.mail == null ? 0 : 1

  email_identity         = aws_sesv2_email_identity.mail[0].email_identity
  mail_from_domain       = local.mail_from_domain
  behavior_on_mx_failure = "USE_DEFAULT_VALUE"
}

resource "aws_sesv2_configuration_set" "mail" {
  count = var.mail == null ? 0 : 1

  configuration_set_name = local.name

  # The links in the mail sign people in: a receiving server that cannot take them encrypted is
  # not sent them, and the bounce says so.
  delivery_options {
    tls_policy = "REQUIRE"
  }
  reputation_options {
    reputation_metrics_enabled = true
  }
  # SES drops what goes to an address on the account's list, for both reasons, besides telling
  # the app, which spares it its notices.
  suppression_options {
    suppressed_reasons = ["BOUNCE", "COMPLAINT"]
  }
}

resource "aws_sesv2_configuration_set_event_destination" "mail" {
  count = var.mail == null ? 0 : 1

  configuration_set_name = aws_sesv2_configuration_set.mail[0].configuration_set_name
  event_destination_name = "bounces"

  event_destination {
    enabled              = true
    matching_event_types = ["BOUNCE", "COMPLAINT"]
    # SES publishes to the account's default bus alone.
    event_bridge_destination {
      event_bus_arn = "arn:${local.partition}:events:${local.region}:${local.account}:event-bus/default"
    }
  }
}

# The bus is the account's, which the other environment's SES publishes to as well: the rule
# takes the events of this environment's configuration set.
resource "aws_cloudwatch_event_rule" "mail" {
  count = var.mail == null ? 0 : 1

  name          = "${local.name}-mail-events"
  description   = "The bounces and complaints of ${local.name}'s mail, for its worker"
  event_pattern = jsonencode(local.mail_event_pattern)
}

resource "aws_cloudwatch_event_target" "mail" {
  count = var.mail == null ? 0 : 1

  rule = aws_cloudwatch_event_rule.mail[0].name
  arn  = aws_sqs_queue.mail_events[0].arn
}

# What the worker reads every minute; kept as long as SQS keeps anything, for a worker that is
# down a while.
resource "aws_sqs_queue" "mail_events" {
  count = var.mail == null ? 0 : 1

  name                      = "${local.name}-mail-events"
  message_retention_seconds = 14 * 24 * 3600
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue_policy" "mail_events" {
  count = var.mail == null ? 0 : 1

  queue_url = aws_sqs_queue.mail_events[0].id
  policy    = jsonencode(local.mail_events_policy)
}

locals {
  mail_from_domain = var.mail == null ? "" : "bounce.${var.mail.domain}"

  mail_event_pattern = var.mail == null ? null : {
    source        = ["aws.ses"]
    "detail-type" = ["Email Bounced", "Email Complaint Received"]
    detail        = { mail = { tags = { "ses:configuration-set" = [local.name] } } }
  }

  # EventBridge sends into the queue for this rule alone.
  mail_events_policy = var.mail == null ? null : {
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.mail_events[0].arn
      Condition = { ArnEquals = { "aws:SourceArn" = aws_cloudwatch_event_rule.mail[0].arn } }
    }]
  }

  # The MAIL FROM domain's MX and SPF, and the From domain's DMARC unless the zone has its own.
  mail_records = var.mail == null ? [] : concat(
    [
      {
        name  = local.mail_from_domain
        type  = "MX"
        value = "10 feedback-smtp.${local.region}.amazonses.com"
      },
      { name = local.mail_from_domain, type = "TXT", value = "v=spf1 include:amazonses.com ~all" },
    ],
    var.mail.dmarc_policy == "" ? [] : [{
      name = "_dmarc.${var.mail.domain}"
      type = "TXT"
      value = join("; ", compact([
        "v=DMARC1",
        "p=${var.mail.dmarc_policy}",
        var.mail.dmarc_reports == "" ? "" : "rua=mailto:${var.mail.dmarc_reports}",
      ]))
    }],
  )
}

locals {
  dkim_records = var.mail == null ? [] : [
    for token in aws_sesv2_email_identity.mail[0].dkim_signing_attributes[0].tokens : {
      name  = "${token}._domainkey.${var.mail.domain}"
      type  = "CNAME"
      value = "${token}.dkim.amazonses.com"
    }
  ]
}

resource "aws_iam_user" "smtp" {
  count = var.mail == null ? 0 : 1

  name = "${local.name}-smtp"
}

resource "aws_iam_user_policy" "smtp" {
  count = var.mail == null ? 0 : 1

  name = "send"
  user = aws_iam_user.smtp[0].name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "ses:SendRawEmail"
      Resource = aws_sesv2_email_identity.mail[0].arn
    }]
  })
}

# SES's SMTP password is derived from the key's secret; both are in the state, which the roots
# encrypt. A new key is made by replacing this resource (tofu apply -replace=…) and releasing.
resource "aws_iam_access_key" "smtp" {
  count = var.mail == null ? 0 : 1

  user = aws_iam_user.smtp[0].name
}

locals {
  smtp_url = var.mail == null ? "" : format(
    "smtp://%s:%s@email-smtp.%s.amazonaws.com:587",
    urlencode(aws_iam_access_key.smtp[0].id),
    urlencode(aws_iam_access_key.smtp[0].ses_smtp_password_v4),
    local.region,
  )
}
