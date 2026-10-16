# Three roles. The instance's role is the host's alone: the instance metadata service answers
# no container (one hop, instance.tf), so ClamAV, which opens hostile files, and Ollama hold no
# credentials at all. The app's containers get the app role's instead, which reaches the bucket
# and nothing else, through a credentials file the host renews (deploy/aws/host/credentials).
# The drill's dry run gets the reader's, which can only list the bucket. The policies are
# written out rather than built by aws_iam_policy_document, so that the tests read them.

data "aws_ecr_repository" "app" {
  name = var.repository
}

resource "aws_iam_role" "instance" {
  name = "${local.name}-instance"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
}

resource "aws_iam_instance_profile" "instance" {
  name = "${local.name}-instance"
  role = aws_iam_role.instance.name
}

# A shell on the instance through Session Manager, and the commands release.sh sends.
resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

locals {
  parameters_arn = "arn:${local.partition}:ssm:${local.region}:${local.account}:parameter${local.parameters}"
  drill_copies   = "arn:${local.partition}:rds:${local.region}:${local.account}:db:emudoc-drill-*"

  instance_policy = {
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "PullTheImage"
        Effect   = "Allow"
        Action   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"]
        Resource = data.aws_ecr_repository.app.arn
      },
      {
        Sid      = "SignInToTheRegistry"
        Effect   = "Allow"
        Action   = "ecr:GetAuthorizationToken"
        Resource = "*"
      },
      # The settings, the secrets and the release; SecureStrings are decrypted with SSM's own
      # key, whose policy lets the account's principals use it through SSM.
      {
        Sid      = "ReadTheSettings"
        Effect   = "Allow"
        Action   = ["ssm:GetParametersByPath", "ssm:GetParameter", "ssm:GetParameters"]
        Resource = [local.parameters_arn, "${local.parameters_arn}/*"]
      },
      {
        Sid      = "HandOutTheContainersCredentials"
        Effect   = "Allow"
        Action   = "sts:AssumeRole"
        Resource = [aws_iam_role.app.arn, aws_iam_role.reader.arn]
      },
      {
        Sid      = "Alert"
        Effect   = "Allow"
        Action   = "sns:Publish"
        Resource = aws_sns_topic.alerts.arn
      },
      # The restore drill (deploy/rds-drill.sh): a copy of this environment's database alone,
      # which it may tag and delete, and nothing else of RDS's.
      {
        Sid      = "DrillDescribe"
        Effect   = "Allow"
        Action   = "rds:DescribeDBInstances"
        Resource = "*"
      },
      {
        Sid    = "DrillRestore"
        Effect = "Allow"
        Action = "rds:RestoreDBInstanceToPointInTime"
        Resource = [
          aws_db_instance.main.arn,
          local.drill_copies,
          aws_db_subnet_group.main.arn,
          aws_db_parameter_group.main.arn,
          "arn:${local.partition}:rds:${local.region}:${local.account}:og:*",
        ]
      },
      {
        Sid      = "DrillTag"
        Effect   = "Allow"
        Action   = "rds:AddTagsToResource"
        Resource = local.drill_copies
      },
      {
        Sid       = "DrillDiscard"
        Effect    = "Allow"
        Action    = "rds:DeleteDBInstance"
        Resource  = local.drill_copies
        Condition = { StringEquals = { "rds:db-tag/purpose" = "restore-drill" } }
      },
    ]
  }

  # Assumed by the host for the containers, never by anything else.
  host_trust = {
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { AWS = aws_iam_role.instance.arn }
    }]
  }

  # What app/storage.py asks of the bucket, and no more: the objects, their versions (a
  # restore removes the delete markers newer than its moment), and the bucket's versioning,
  # lifecycle, encryption and region, which the app reads at start.
  app_policy = {
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:ListBucket",
          "s3:ListBucketVersions",
          "s3:GetBucketLocation",
          "s3:GetBucketVersioning",
          "s3:GetLifecycleConfiguration",
          "s3:GetEncryptionConfiguration",
        ]
        Resource = aws_s3_bucket.documents.arn
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:DeleteObjectVersion"]
        Resource = "${aws_s3_bucket.documents.arn}/*"
      },
    ]
  }

  # The four permissions deploying.md's "The drill" names for the dry run, which reads the live
  # bucket while the copy of the database is checked against it.
  reader_policy = {
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "s3:ListBucket",
        "s3:ListBucketVersions",
        "s3:GetBucketVersioning",
        "s3:GetLifecycleConfiguration",
      ]
      Resource = aws_s3_bucket.documents.arn
    }]
  }
}

resource "aws_iam_role_policy" "instance" {
  name   = "emudoc"
  role   = aws_iam_role.instance.id
  policy = jsonencode(local.instance_policy)
}

resource "aws_iam_role" "app" {
  name                 = "${local.name}-app"
  assume_role_policy   = jsonencode(local.host_trust)
  max_session_duration = 3600
}

resource "aws_iam_role_policy" "app" {
  name   = "documents"
  role   = aws_iam_role.app.id
  policy = jsonencode(local.app_policy)
}

resource "aws_iam_role" "reader" {
  name                 = "${local.name}-storage-reader"
  assume_role_policy   = jsonencode(local.host_trust)
  max_session_duration = 3600
}

resource "aws_iam_role_policy" "reader" {
  name   = "documents-read"
  role   = aws_iam_role.reader.id
  policy = jsonencode(local.reader_policy)
}
