# The account's own record (deploying.md, "The account's records"): every call to AWS's APIs
# in every region, by whom and from where, kept for trail_days in a bucket of its own, each
# file encrypted with a key of its own and signed, so that a file changed or removed is found
# (`aws cloudtrail validate-logs`). Beside those management events it records every request for
# an object of the state bucket, whose states hold the database's password: few requests, each
# worth knowing about. The documents buckets' requests are each environment's access logs
# instead (modules/environment/logs.tf), since a data event costs per request and every page a
# member opens is one. What the trail holds is the operators', CI's and the hosts' doing: no
# member's request reaches an API it records. CloudWatch Logs takes a copy for the alarms on its
# events (alarms.tf).

variable "trail_days" {
  description = "How many days the trail's files are kept."
  type        = number
  default     = 365

  validation {
    condition     = var.trail_days >= 1 && floor(var.trail_days) == var.trail_days
    error_message = "trail_days is a whole number of days."
  }
}

data "aws_partition" "current" {}

locals {
  account    = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
  trail_name = "emudoc"
  # Written out rather than read from the trail, since its key and its bucket must name it
  # before it can be made.
  trail_arn = "arn:${local.partition}:cloudtrail:${var.region}:${local.account}:trail/${local.trail_name}"

  # The account's administrators decide who reads the trail through IAM, as for every other
  # key; CloudTrail may only encrypt with it, and only for this trail, and CloudWatch Logs only
  # for the trail's log group.
  trail_key_policy = {
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
        Sid       = "CloudTrailEncrypts"
        Effect    = "Allow"
        Principal = { Service = "cloudtrail.amazonaws.com" }
        Action    = "kms:GenerateDataKey*"
        Resource  = "*"
        Condition = {
          StringEquals = { "aws:SourceArn" = local.trail_arn }
          StringLike   = { "kms:EncryptionContext:aws:cloudtrail:arn" = "arn:${local.partition}:cloudtrail:*:${local.account}:trail/*" }
        }
      },
      {
        Sid       = "CloudTrailDescribes"
        Effect    = "Allow"
        Principal = { Service = "cloudtrail.amazonaws.com" }
        Action    = "kms:DescribeKey"
        Resource  = "*"
        Condition = { StringEquals = { "aws:SourceArn" = local.trail_arn } }
      },
      {
        Sid       = "TheLogGroupEncrypts"
        Effect    = "Allow"
        Principal = { Service = "logs.${var.region}.amazonaws.com" }
        Action    = ["kms:Encrypt*", "kms:Decrypt*", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:Describe*"]
        Resource  = "*"
        Condition = { ArnEquals = { "kms:EncryptionContext:aws:logs:arn" = local.trail_log_group_arn } }
      },
    ]
  }

  trail_bucket_policy = {
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "TLSOnly"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = [aws_s3_bucket.trail.arn, "${aws_s3_bucket.trail.arn}/*"]
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      },
      {
        Sid       = "CloudTrailChecks"
        Effect    = "Allow"
        Principal = { Service = "cloudtrail.amazonaws.com" }
        Action    = "s3:GetBucketAcl"
        Resource  = aws_s3_bucket.trail.arn
        Condition = { StringEquals = { "aws:SourceArn" = local.trail_arn } }
      },
      {
        Sid       = "CloudTrailWrites"
        Effect    = "Allow"
        Principal = { Service = "cloudtrail.amazonaws.com" }
        Action    = "s3:PutObject"
        Resource  = "${aws_s3_bucket.trail.arn}/AWSLogs/${local.account}/*"
        Condition = {
          StringEquals = {
            "aws:SourceArn" = local.trail_arn
            "s3:x-amz-acl"  = "bucket-owner-full-control"
          }
        }
      },
    ]
  }
}

resource "aws_kms_key" "trail" {
  description             = "Emudoc: the account's trail"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy                  = jsonencode(local.trail_key_policy)
}

resource "aws_kms_alias" "trail" {
  name          = "alias/emudoc-trail"
  target_key_id = aws_kms_key.trail.key_id
}

resource "aws_s3_bucket" "trail" {
  bucket = "emudoc-trail-${local.account}"
}

resource "aws_s3_bucket_public_access_block" "trail" {
  bucket = aws_s3_bucket.trail.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "trail" {
  bucket = aws_s3_bucket.trail.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# CloudTrail encrypts its files with the trail's key itself; the bucket's default does the same
# for anything else put there.
resource "aws_s3_bucket_server_side_encryption_configuration" "trail" {
  bucket = aws_s3_bucket.trail.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.trail.arn
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "trail" {
  bucket = aws_s3_bucket.trail.id

  rule {
    id     = "expiry"
    status = "Enabled"

    filter {}

    expiration {
      days = var.trail_days
    }
  }
}

resource "aws_s3_bucket_policy" "trail" {
  bucket = aws_s3_bucket.trail.id
  policy = jsonencode(local.trail_bucket_policy)

  depends_on = [aws_s3_bucket_public_access_block.trail]
}

resource "aws_cloudtrail" "account" {
  name                          = local.trail_name
  s3_bucket_name                = aws_s3_bucket.trail.id
  kms_key_id                    = aws_kms_key.trail.arn
  is_multi_region_trail         = true
  include_global_service_events = true
  enable_log_file_validation    = true
  cloud_watch_logs_group_arn    = "${aws_cloudwatch_log_group.trail.arn}:*"
  cloud_watch_logs_role_arn     = aws_iam_role.trail_logs.arn

  advanced_event_selector {
    name = "Every call to the APIs"

    field_selector {
      field  = "eventCategory"
      equals = ["Management"]
    }
  }

  advanced_event_selector {
    name = "Every request for the state"

    field_selector {
      field  = "eventCategory"
      equals = ["Data"]
    }

    field_selector {
      field  = "resources.type"
      equals = ["AWS::S3::Object"]
    }

    field_selector {
      field       = "resources.ARN"
      starts_with = ["${aws_s3_bucket.state.arn}/"]
    }
  }

  # CloudTrail checks at once that it may write to the bucket and the log group.
  depends_on = [aws_s3_bucket_policy.trail, aws_iam_role_policy.trail_logs]
}

output "trail_bucket" {
  description = "Where the trail's files are, under AWSLogs/<account>/CloudTrail/ (and CloudTrail-Digest/)."
  value       = aws_s3_bucket.trail.bucket
}
