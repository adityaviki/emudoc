# The environment's records of connections (deploying.md, "The account's records"): the VPC's
# flow logs, every connection to or from its subnets with the addresses, ports and size, and
# the documents bucket's server access logs, every request with the requester's address and
# browser, which for the page images and downloads a member's browser fetches by a signed
# address are the member's, and whose query names a download's file. Both are personal data, so
# they go after access_log_days.
# Access logs rather than CloudTrail's data events, which cost per request. Neither of the
# containers' roles reaches this bucket.

resource "aws_s3_bucket" "logs" {
  bucket = "${local.name}-logs-${local.account}"

  tags = { Name = "${local.name}-logs" }
}

resource "aws_s3_bucket_public_access_block" "logs" {
  bucket = aws_s3_bucket.logs.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "logs" {
  bucket = aws_s3_bucket.logs.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# S3 delivers access logs only to a bucket encrypted with its own keys.
resource "aws_s3_bucket_server_side_encryption_configuration" "logs" {
  bucket = aws_s3_bucket.logs.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Unversioned, so that an expired log is gone: two days before access_log_days, as the
# documents bucket's rule, since AWS rounds the days up to the next midnight and may take a
# while longer, and an access log is delivered hours after its requests.
resource "aws_s3_bucket_lifecycle_configuration" "logs" {
  bucket = aws_s3_bucket.logs.id

  rule {
    id     = "expiry"
    status = "Enabled"

    filter {}

    expiration {
      days = var.access_log_days - 2
    }
  }
}

locals {
  # The flow logs are delivered by CloudWatch Logs' delivery service on this account's behalf.
  log_delivery = "arn:${local.partition}:logs:${local.region}:${local.account}:*"

  logs_policy = {
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "TLSOnly"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = [aws_s3_bucket.logs.arn, "${aws_s3_bucket.logs.arn}/*"]
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      },
      {
        Sid       = "AccessLogsOfTheDocuments"
        Effect    = "Allow"
        Principal = { Service = "logging.s3.amazonaws.com" }
        Action    = "s3:PutObject"
        Resource  = "${aws_s3_bucket.logs.arn}/access/*"
        Condition = {
          # The documents' bucket, and the bucket of the mail SES received, when there is one.
          ArnLike      = { "aws:SourceArn" = concat([aws_s3_bucket.documents.arn], aws_s3_bucket.inbound[*].arn) }
          StringEquals = { "aws:SourceAccount" = local.account }
        }
      },
      {
        Sid       = "FlowLogsCheck"
        Effect    = "Allow"
        Principal = { Service = "delivery.logs.amazonaws.com" }
        Action    = "s3:GetBucketAcl"
        Resource  = aws_s3_bucket.logs.arn
        Condition = {
          StringEquals = { "aws:SourceAccount" = local.account }
          ArnLike      = { "aws:SourceArn" = local.log_delivery }
        }
      },
      {
        Sid       = "FlowLogs"
        Effect    = "Allow"
        Principal = { Service = "delivery.logs.amazonaws.com" }
        Action    = "s3:PutObject"
        Resource  = "${aws_s3_bucket.logs.arn}/flow/AWSLogs/${local.account}/*"
        Condition = {
          StringEquals = { "aws:SourceAccount" = local.account, "s3:x-amz-acl" = "bucket-owner-full-control" }
          ArnLike      = { "aws:SourceArn" = local.log_delivery }
        }
      },
    ]
  }
}

resource "aws_s3_bucket_policy" "logs" {
  bucket = aws_s3_bucket.logs.id
  policy = jsonencode(local.logs_policy)

  depends_on = [aws_s3_bucket_public_access_block.logs]
}

# Grouped by the day of the request (access/<account>/<region>/<bucket>/YYYY/MM/DD/), for a query
# over a day or a week.
resource "aws_s3_bucket_logging" "documents" {
  bucket        = aws_s3_bucket.documents.id
  target_bucket = aws_s3_bucket.logs.id
  target_prefix = "access/"

  target_object_key_format {
    partitioned_prefix {
      partition_date_source = "EventTime"
    }
  }

  depends_on = [aws_s3_bucket_policy.logs]
}

# Accepted and rejected alike: who reached the instance, and what knocked on what it refuses.
# The delivery service writes its own policy over a bucket's when it finds none that lets it
# in, so the bucket's comes first.
resource "aws_flow_log" "main" {
  vpc_id               = aws_vpc.main.id
  traffic_type         = "ALL"
  log_destination_type = "s3"
  log_destination      = "${aws_s3_bucket.logs.arn}/flow/"

  tags = { Name = local.name }

  depends_on = [aws_s3_bucket_policy.logs]
}
