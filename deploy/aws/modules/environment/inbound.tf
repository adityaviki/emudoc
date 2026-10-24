# Mail in through Amazon SES, when var.inbound names a domain (deploying.md, "Mail in"): the
# projects' addresses are <token>@<domain>, whose MX is SES's receiving endpoint in the region.
# SES checks a domain's identity before it takes its mail, so the domain is verified by DKIM as
# the mail domain is. The account's one active receipt rule set (account/inbound.tf, the name
# shared/ gives it) holds a rule of this environment's for the domain, which writes each message
# as it was sent into a bucket of its own and tells an SNS topic, whose notification carries
# SES's SPF, DKIM and DMARC verdicts and the object's key, into an SQS queue the worker reads
# (backend/app/receiving.py): no address of the app's is opened to AWS. The worker takes a
# message's object once it took the message in; what is left a fortnight, as long as the queue
# keeps its notice, is removed.

locals {
  inbound_rule      = var.inbound == null ? "" : "arn:${local.partition}:ses:${local.region}:${local.account}:receipt-rule-set/${var.inbound.rule_set}:receipt-rule/${local.name}"
  inbound_prefix    = "inbound/"
  inbound_kept_days = 14

  inbound_records = var.inbound == null ? [] : concat(
    [{ name = var.inbound.domain, type = "MX", value = "10 inbound-smtp.${local.region}.amazonaws.com" }],
    [
      for token in aws_sesv2_email_identity.inbound[0].dkim_signing_attributes[0].tokens : {
        name  = "${token}._domainkey.${var.inbound.domain}"
        type  = "CNAME"
        value = "${token}.dkim.amazonses.com"
      }
    ],
  )

  # SES writes the messages its rule received, and nothing reaches the bucket but over TLS.
  inbound_bucket_policy = var.inbound == null ? null : {
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "SesWritesItsRules"
        Effect    = "Allow"
        Principal = { Service = "ses.amazonaws.com" }
        Action    = "s3:PutObject"
        Resource  = "${aws_s3_bucket.inbound[0].arn}/${local.inbound_prefix}*"
        Condition = {
          StringEquals = { "aws:SourceAccount" = local.account }
          ArnLike      = { "aws:SourceArn" = local.inbound_rule }
        }
      },
      {
        Sid       = "TLSOnly"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = [aws_s3_bucket.inbound[0].arn, "${aws_s3_bucket.inbound[0].arn}/*"]
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      },
    ]
  }

  # SES publishes for its rule alone, encrypting with the topic's key.
  inbound_topic_policy = var.inbound == null ? null : {
    Version = "2012-10-17"
    Statement = [{
      Sid       = "SesPublishesItsRules"
      Effect    = "Allow"
      Principal = { Service = "ses.amazonaws.com" }
      Action    = "sns:Publish"
      Resource  = aws_sns_topic.inbound[0].arn
      Condition = {
        StringEquals = { "aws:SourceAccount" = local.account }
        ArnLike      = { "aws:SourceArn" = local.inbound_rule }
      }
    }]
  }

  inbound_key_policy = var.inbound == null ? null : {
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
        Sid       = "SesPublishes"
        Effect    = "Allow"
        Principal = { Service = "ses.amazonaws.com" }
        Action    = ["kms:GenerateDataKey*", "kms:Decrypt"]
        Resource  = "*"
        Condition = { StringEquals = { "aws:SourceAccount" = local.account } }
      },
    ]
  }

  # The topic sends into the queue, and nothing else does.
  inbound_queue_policy = var.inbound == null ? null : {
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "sns.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.inbound[0].arn
      Condition = { ArnEquals = { "aws:SourceArn" = aws_sns_topic.inbound[0].arn } }
    }]
  }
}

resource "aws_sesv2_email_identity" "inbound" {
  count = var.inbound == null ? 0 : 1

  email_identity = var.inbound.domain

  dkim_signing_attributes {
    next_signing_key_length = "RSA_2048_BIT"
  }
}

resource "aws_s3_bucket" "inbound" {
  count = var.inbound == null ? 0 : 1

  bucket = "${local.name}-inbound-${local.account}"

  tags = { Name = "${local.name}-inbound" }
}

resource "aws_s3_bucket_public_access_block" "inbound" {
  count = var.inbound == null ? 0 : 1

  bucket = aws_s3_bucket.inbound[0].id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "inbound" {
  count = var.inbound == null ? 0 : 1

  bucket = aws_s3_bucket.inbound[0].id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "inbound" {
  count = var.inbound == null ? 0 : 1

  bucket = aws_s3_bucket.inbound[0].id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "inbound" {
  count = var.inbound == null ? 0 : 1

  bucket = aws_s3_bucket.inbound[0].id

  rule {
    id     = "untaken"
    status = "Enabled"

    filter {}

    expiration {
      days = local.inbound_kept_days
    }
  }
}

# Beside the documents bucket's, by the bucket's name in the partitioned prefix (logs.tf).
resource "aws_s3_bucket_logging" "inbound" {
  count = var.inbound == null ? 0 : 1

  bucket        = aws_s3_bucket.inbound[0].id
  target_bucket = aws_s3_bucket.logs.id
  target_prefix = "access/"

  target_object_key_format {
    partitioned_prefix {
      partition_date_source = "EventTime"
    }
  }

  depends_on = [aws_s3_bucket_policy.logs]
}

resource "aws_s3_bucket_policy" "inbound" {
  count = var.inbound == null ? 0 : 1

  bucket = aws_s3_bucket.inbound[0].id
  policy = jsonencode(local.inbound_bucket_policy)

  depends_on = [aws_s3_bucket_public_access_block.inbound]
}

resource "aws_kms_key" "inbound" {
  count = var.inbound == null ? 0 : 1

  description             = "Emudoc ${var.environment}: the notices of mail received"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy                  = jsonencode(local.inbound_key_policy)
}

resource "aws_kms_alias" "inbound" {
  count = var.inbound == null ? 0 : 1

  name          = "alias/${local.name}-inbound"
  target_key_id = aws_kms_key.inbound[0].key_id
}

resource "aws_sns_topic" "inbound" {
  count = var.inbound == null ? 0 : 1

  name              = "${local.name}-inbound"
  kms_master_key_id = aws_kms_key.inbound[0].arn
}

resource "aws_sns_topic_policy" "inbound" {
  count = var.inbound == null ? 0 : 1

  arn    = aws_sns_topic.inbound[0].arn
  policy = jsonencode(local.inbound_topic_policy)
}

# What the worker reads; kept as long as SQS keeps anything, for a worker that is down a while.
resource "aws_sqs_queue" "inbound" {
  count = var.inbound == null ? 0 : 1

  name                      = "${local.name}-inbound"
  message_retention_seconds = local.inbound_kept_days * 24 * 3600
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue_policy" "inbound" {
  count = var.inbound == null ? 0 : 1

  queue_url = aws_sqs_queue.inbound[0].id
  policy    = jsonencode(local.inbound_queue_policy)
}

# The notification itself as the queue's message, without SNS's envelope.
resource "aws_sns_topic_subscription" "inbound" {
  count = var.inbound == null ? 0 : 1

  topic_arn            = aws_sns_topic.inbound[0].arn
  protocol             = "sqs"
  endpoint             = aws_sqs_queue.inbound[0].arn
  raw_message_delivery = true

  depends_on = [aws_sqs_queue_policy.inbound]
}

# SES checks that it may write to the bucket and publish to the topic as the rule is made.
resource "aws_ses_receipt_rule" "inbound" {
  count = var.inbound == null ? 0 : 1

  name          = local.name
  rule_set_name = var.inbound.rule_set
  recipients    = [var.inbound.domain]
  enabled       = true
  scan_enabled  = false
  tls_policy    = "Optional"

  s3_action {
    position          = 1
    bucket_name       = aws_s3_bucket.inbound[0].bucket
    object_key_prefix = local.inbound_prefix
    topic_arn         = aws_sns_topic.inbound[0].arn
  }

  depends_on = [aws_s3_bucket_policy.inbound, aws_sns_topic_policy.inbound]
}
