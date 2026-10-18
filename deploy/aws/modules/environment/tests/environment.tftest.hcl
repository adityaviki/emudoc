# The environment module against a mocked AWS (tofu test, in this module's directory): what
# each resource would be made with, and the inputs it refuses. Nothing reaches AWS. The values
# the provider would compute are the mocks' below.

mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  mock_data "aws_region" {
    defaults = { region = "eu-central-1" }
  }
  mock_data "aws_partition" {
    defaults = { partition = "aws" }
  }
  mock_data "aws_availability_zones" {
    defaults = { names = ["eu-central-1a", "eu-central-1b", "eu-central-1c"] }
  }
  mock_data "aws_ssm_parameter" {
    defaults = { value = "ami-0123456789abcdef0" }
  }
  mock_data "aws_ecr_repository" {
    defaults = {
      arn            = "arn:aws:ecr:eu-central-1:123456789012:repository/emudoc"
      repository_url = "123456789012.dkr.ecr.eu-central-1.amazonaws.com/emudoc"
    }
  }
  mock_resource "aws_db_instance" {
    defaults = {
      address = "emudoc-test.abcdefghijkl.eu-central-1.rds.amazonaws.com"
      port    = 5432
      arn     = "arn:aws:rds:eu-central-1:123456789012:db:emudoc-test"
    }
  }
  mock_resource "aws_s3_bucket" {
    defaults = { arn = "arn:aws:s3:::emudoc-test-documents-123456789012" }
  }
  mock_resource "aws_eip" {
    defaults = { public_ip = "198.51.100.7" }
  }
  mock_resource "aws_sesv2_email_identity" {
    defaults = {
      arn                     = "arn:aws:ses:eu-central-1:123456789012:identity/mail.example.com"
      dkim_signing_attributes = { tokens = ["one", "two", "three"] }
    }
  }
  mock_resource "aws_sns_topic" {
    defaults = { arn = "arn:aws:sns:eu-central-1:123456789012:emudoc-alerts" }
  }
  mock_resource "aws_iam_access_key" {
    defaults = {
      id                   = "AKIAEXAMPLEKEY"
      ses_smtp_password_v4 = "BAx/y+z="
    }
  }
}

variables {
  environment = "staging"
  domain      = "staging.emudoc.example.com"
  settings = {
    OPERATOR_NAME  = "Emudoc Ltd, 1 Example Street, Example Town"
    OPERATOR_EMAIL = "privacy@example.com"
    AGENT_MODEL    = "anthropic:claude-sonnet-5-5"
    INDEX_MODEL    = "anthropic:claude-haiku-5-5"
    # Empty is unset, as deploy/aws/shared/main.tf lists the settings it leaves out.
    AGENT_FALLBACK_MODEL = ""
  }
  github_repository = "emudocai/emudoc"
}

run "staging" {
  override_resource {
    target = aws_subnet.public
    values = { id = "subnet-public" }
  }
  override_resource {
    target = aws_subnet.private
    values = { id = "subnet-private" }
  }
  override_resource {
    target = aws_iam_role.instance
    values = { arn = "arn:aws:iam::123456789012:role/emudoc-staging-instance" }
  }
  override_resource {
    target = aws_iam_role.app
    values = { arn = "arn:aws:iam::123456789012:role/emudoc-staging-app" }
  }
  override_resource {
    target = aws_iam_role.reader
    values = { arn = "arn:aws:iam::123456789012:role/emudoc-staging-storage-reader" }
  }
  override_resource {
    target = aws_instance.app
    values = { arn = "arn:aws:ec2:eu-central-1:123456789012:instance/i-0123456789abcdef0" }
  }
  override_resource {
    target = aws_s3_bucket.logs
    values = { arn = "arn:aws:s3:::emudoc-test-logs-123456789012" }
  }

  # The database: private, encrypted, TLS only, its backups within BACKUP_DAYS.
  assert {
    condition     = aws_db_instance.main.publicly_accessible == false && aws_db_instance.main.storage_encrypted
    error_message = "The database must be private and encrypted."
  }
  assert {
    condition     = aws_db_instance.main.engine == "postgres" && aws_db_instance.main.engine_version == "17"
    error_message = "The database is Postgres 17, whose RDS builds bring pgvector 0.8."
  }
  assert {
    condition     = aws_db_instance.main.backup_retention_period == 35
    error_message = "The database's backups are kept for backup_days."
  }
  assert {
    condition = (
      aws_db_instance.main.deletion_protection && !aws_db_instance.main.skip_final_snapshot
      && aws_db_instance.main.final_snapshot_identifier == "emudoc-staging-final"
    )
    error_message = "A protected database leaves a final snapshot when it is deleted."
  }
  assert {
    condition = {
      for parameter in aws_db_parameter_group.main.parameter : parameter.name => parameter.value
      } == {
      "rds.force_ssl"                     = "1"
      statement_timeout                   = "30000"
      idle_in_transaction_session_timeout = "300000"
    }
    error_message = "The server requires TLS and holds deploying.md's timeouts."
  }
  assert {
    condition = (
      aws_vpc_security_group_ingress_rule.database.referenced_security_group_id == aws_security_group.instance.id
      && aws_vpc_security_group_ingress_rule.database.from_port == 5432
      && aws_vpc_security_group_ingress_rule.database.cidr_ipv4 == null
    )
    error_message = "Only the instance reaches the database."
  }
  assert {
    condition = (
      aws_db_subnet_group.main.subnet_ids == toset(["subnet-private"])
      && length(aws_subnet.private) == 2
      && aws_subnet.private[0].availability_zone != aws_subnet.private[1].availability_zone
    )
    error_message = "The database sits on the two private subnets."
  }

  # The instance: 80 and 443 in, no SSH, no metadata for the containers, an encrypted disk.
  assert {
    condition = toset([
      for rule in aws_vpc_security_group_ingress_rule.web : "${rule.ip_protocol}/${rule.from_port}-${rule.to_port}"
    ]) == toset(["tcp/80-80", "tcp/443-443", "udp/443-443"])
    error_message = "The instance takes 80 and 443 and nothing else."
  }
  assert {
    condition = (
      aws_instance.app.metadata_options[0].http_tokens == "required"
      && aws_instance.app.metadata_options[0].http_put_response_hop_limit == 1
    )
    error_message = "The metadata service answers the host alone, with tokens."
  }
  assert {
    condition     = aws_instance.app.root_block_device[0].encrypted
    error_message = "The disk is encrypted."
  }
  assert {
    condition     = aws_instance.app.user_data_replace_on_change
    error_message = "A change to the boot makes a new instance, since cloud-init runs once."
  }
  assert {
    condition = alltrue([
      for line in [
        "EMUDOC_REPOSITORY=123456789012.dkr.ecr.eu-central-1.amazonaws.com/emudoc",
        "EMUDOC_PARAMETERS=/emudoc/staging",
        "EMUDOC_APP_ROLE=arn:aws:iam::123456789012:role/emudoc-staging-app",
        "RDS_SOURCE=emudoc-staging",
        "EMUDOC_DRILL=yes",
      ] : strcontains(local.cloud_config, "      ${line}\n")
    ])
    error_message = "The host's settings reach /etc/emudoc/host.env."
  }
  assert {
    condition = (
      strcontains(local.cloud_config, "\"123456789012.dkr.ecr.eu-central-1.amazonaws.com\": \"ecr-login\"")
      && strcontains(local.cloud_config, "      exec \"$dir/aws/host/release\" \"$tag\"\n") == false
      && strcontains(local.cloud_config, "      \"$dir/aws/host/release\" \"$tag\"\n")
    )
    error_message = "The registry's helper and the release stub are written, indented, into the cloud-config."
  }

  # The bucket: private, encrypted, TLS only, its earlier versions within BACKUP_DAYS.
  assert {
    condition = (
      aws_s3_bucket_public_access_block.documents.block_public_acls
      && aws_s3_bucket_public_access_block.documents.block_public_policy
      && aws_s3_bucket_public_access_block.documents.ignore_public_acls
      && aws_s3_bucket_public_access_block.documents.restrict_public_buckets
    )
    error_message = "The bucket is never public."
  }
  assert {
    condition     = one(one(aws_s3_bucket_server_side_encryption_configuration.documents.rule).apply_server_side_encryption_by_default).sse_algorithm == "AES256"
    error_message = "The bucket encrypts what it keeps."
  }
  assert {
    condition     = aws_s3_bucket_versioning.documents.versioning_configuration[0].status == "Enabled"
    error_message = "The bucket keeps earlier versions, for a restore."
  }
  assert {
    condition = (
      aws_s3_bucket_lifecycle_configuration.documents.rule[0].noncurrent_version_expiration[0].noncurrent_days == 33
      && aws_s3_bucket_lifecycle_configuration.documents.rule[0].noncurrent_version_expiration[0].newer_noncurrent_versions == null
      && aws_s3_bucket_lifecycle_configuration.documents.rule[0].expiration[0].expired_object_delete_marker
      && length(aws_s3_bucket_lifecycle_configuration.documents.rule) == 1
    )
    error_message = "Earlier versions go two days before BACKUP_DAYS, by a rule over every key that check_versions counts."
  }
  assert {
    condition     = jsondecode(aws_s3_bucket_policy.documents.policy).Statement[0].Condition.Bool["aws:SecureTransport"] == "false"
    error_message = "The bucket refuses a request without TLS."
  }

  # The records: every request to the documents bucket and every connection of the network,
  # in a bucket of their own, gone before ACCESS_LOG_DAYS, which the privacy policy names.
  assert {
    condition = (
      aws_s3_bucket_logging.documents.bucket == aws_s3_bucket.documents.id
      && aws_s3_bucket_logging.documents.target_bucket == aws_s3_bucket.logs.id
      && aws_s3_bucket_logging.documents.target_prefix == "access/"
    )
    error_message = "Every request to the documents bucket is recorded in the logs bucket."
  }
  assert {
    condition = (
      aws_flow_log.main.vpc_id == aws_vpc.main.id
      && aws_flow_log.main.traffic_type == "ALL"
      && aws_flow_log.main.log_destination_type == "s3"
      && aws_flow_log.main.log_destination == "arn:aws:s3:::emudoc-test-logs-123456789012/flow/"
    )
    error_message = "Every connection of the network, accepted or refused, is recorded in the logs bucket."
  }
  assert {
    condition = (
      aws_s3_bucket_lifecycle_configuration.logs.rule[0].expiration[0].days == 28
      && length(aws_s3_bucket_lifecycle_configuration.logs.rule) == 1
      && aws_ssm_parameter.setting["ACCESS_LOG_DAYS"].value == "30"
    )
    error_message = "The logs go two days before ACCESS_LOG_DAYS, which the app is told for its privacy policy."
  }
  assert {
    condition = (
      aws_s3_bucket_public_access_block.logs.block_public_acls
      && aws_s3_bucket_public_access_block.logs.block_public_policy
      && aws_s3_bucket_public_access_block.logs.ignore_public_acls
      && aws_s3_bucket_public_access_block.logs.restrict_public_buckets
      && one(one(aws_s3_bucket_server_side_encryption_configuration.logs.rule).apply_server_side_encryption_by_default).sse_algorithm == "AES256"
    )
    error_message = "The logs bucket is never public, and encrypted with the keys S3 delivers access logs under."
  }
  assert {
    condition = {
      for statement in jsondecode(aws_s3_bucket_policy.logs.policy).Statement : statement.Sid => statement
      if statement.Effect == "Allow"
      } == {
      AccessLogsOfTheDocuments = {
        Sid       = "AccessLogsOfTheDocuments"
        Effect    = "Allow"
        Principal = { Service = "logging.s3.amazonaws.com" }
        Action    = "s3:PutObject"
        Resource  = "arn:aws:s3:::emudoc-test-logs-123456789012/access/*"
        Condition = {
          ArnLike      = { "aws:SourceArn" = "arn:aws:s3:::emudoc-test-documents-123456789012" }
          StringEquals = { "aws:SourceAccount" = "123456789012" }
        }
      }
      FlowLogsCheck = {
        Sid       = "FlowLogsCheck"
        Effect    = "Allow"
        Principal = { Service = "delivery.logs.amazonaws.com" }
        Action    = "s3:GetBucketAcl"
        Resource  = "arn:aws:s3:::emudoc-test-logs-123456789012"
        Condition = {
          StringEquals = { "aws:SourceAccount" = "123456789012" }
          ArnLike      = { "aws:SourceArn" = "arn:aws:logs:eu-central-1:123456789012:*" }
        }
      }
      FlowLogs = {
        Sid       = "FlowLogs"
        Effect    = "Allow"
        Principal = { Service = "delivery.logs.amazonaws.com" }
        Action    = "s3:PutObject"
        Resource  = "arn:aws:s3:::emudoc-test-logs-123456789012/flow/AWSLogs/123456789012/*"
        Condition = {
          StringEquals = { "aws:SourceAccount" = "123456789012", "s3:x-amz-acl" = "bucket-owner-full-control" }
          ArnLike      = { "aws:SourceArn" = "arn:aws:logs:eu-central-1:123456789012:*" }
        }
      }
    }
    error_message = "Only S3's logging of the documents bucket and this account's flow logs write to the logs bucket, each under its own prefix."
  }
  assert {
    condition     = jsondecode(aws_s3_bucket_policy.logs.policy).Statement[0].Condition.Bool["aws:SecureTransport"] == "false"
    error_message = "The logs bucket refuses a request without TLS."
  }
  assert {
    condition = (
      aws_db_instance.main.database_insights_mode == "standard"
      && aws_db_instance.main.performance_insights_enabled
      && aws_db_instance.main.performance_insights_retention_period == 7
    )
    error_message = "Database Insights' standard mode keeps the database's load for the 7 days AWS charges nothing for."
  }

  # The roles: the containers' reach the bucket alone, and only the host takes them on.
  assert {
    condition = toset(flatten([
      for statement in local.app_policy.Statement : statement.Resource
      ])) == toset([
      "arn:aws:s3:::emudoc-test-documents-123456789012",
      "arn:aws:s3:::emudoc-test-documents-123456789012/*",
    ])
    error_message = "The app's credentials reach the documents' bucket and nothing else."
  }
  assert {
    condition = alltrue([
      for action in flatten([for statement in local.reader_policy.Statement : statement.Action]) :
      startswith(action, "s3:List") || startswith(action, "s3:Get")
    ])
    error_message = "The drill's credentials only read."
  }
  assert {
    condition     = jsondecode(aws_iam_role.app.assume_role_policy).Statement[0].Principal.AWS == "arn:aws:iam::123456789012:role/emudoc-staging-instance"
    error_message = "Only the host takes on the app's role."
  }
  assert {
    condition = one([
      for statement in local.instance_policy.Statement : statement
      if statement.Action == "rds:DeleteDBInstance"
      ]).Condition == { StringEquals = { "rds:db-tag/purpose" = "restore-drill" } } && one([
      for statement in local.instance_policy.Statement : statement.Resource
      if statement.Action == "rds:DeleteDBInstance"
    ]) == "arn:aws:rds:eu-central-1:123456789012:db:emudoc-drill-*"
    error_message = "The drill deletes its own copies, never the database."
  }
  assert {
    condition = one([
      for statement in local.instance_policy.Statement : statement.Resource
      if statement.Sid == "ReadTheSettings"
      ]) == [
      "arn:aws:ssm:eu-central-1:123456789012:parameter/emudoc/staging",
      "arn:aws:ssm:eu-central-1:123456789012:parameter/emudoc/staging/*",
    ]
    error_message = "The instance reads its own environment's parameters only."
  }

  # CI's role: a workflow on main of the repository alone, which may release and nothing more.
  assert {
    condition = jsondecode(aws_iam_role.release[0].assume_role_policy).Statement[0] == {
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
    error_message = "Only a job of a workflow on the repository's main takes on the release role."
  }
  assert {
    condition = {
      for statement in local.release_policy.Statement : statement.Sid => statement.Resource
      } == {
      FindTheInstance  = "*"
      RunTheRelease    = ["arn:aws:ec2:eu-central-1:123456789012:instance/i-0123456789abcdef0", "arn:aws:ssm:eu-central-1::document/AWS-RunShellScript"]
      FollowTheRelease = "*"
      RecordTheRelease = "arn:aws:ssm:eu-central-1:123456789012:parameter/emudoc/staging/release"
      FindTheApp       = "arn:aws:ssm:eu-central-1:123456789012:parameter/emudoc/staging/settings/APP_URL"
    }
    error_message = "CI releases on this environment's instance and records its tag, and reads no setting but APP_URL."
  }
  assert {
    condition     = output.release_role == aws_iam_role.release[0].arn
    error_message = "The role is an output, for the repository variable."
  }

  # The settings: what production asks for, and the database checked against RDS's authority.
  assert {
    condition = toset(keys(aws_ssm_parameter.setting)) == toset([
      "APP_URL", "STORAGE_URL", "AWS_DEFAULT_REGION", "SCANNER_URL", "EMBEDDING_BASE_URL",
      "HOSTING_PROVIDER", "BACKUP_DAYS", "ACCESS_LOG_DAYS", "SENTRY_ENVIRONMENT", "OPERATOR_NAME",
      "OPERATOR_EMAIL", "AGENT_MODEL", "INDEX_MODEL",
    ])
    error_message = "Staging's settings are the module's and the root's, and no others."
  }
  assert {
    condition = (
      aws_ssm_parameter.setting["APP_URL"].value == "https://staging.emudoc.example.com"
      && aws_ssm_parameter.setting["STORAGE_URL"].value == "s3://emudoc-staging-documents-123456789012"
      && aws_ssm_parameter.setting["SCANNER_URL"].value == "clamd://clamav:3310"
      && aws_ssm_parameter.setting["EMBEDDING_BASE_URL"].value == "http://embeddings:8080/v1"
      && aws_ssm_parameter.setting["HOSTING_PROVIDER"].value == "Amazon Web Services, eu-central-1"
      && aws_ssm_parameter.setting["BACKUP_DAYS"].value == "35"
      && aws_ssm_parameter.setting["SENTRY_ENVIRONMENT"].value == "staging"
      && aws_ssm_parameter.setting["AGENT_MODEL"].value == "anthropic:claude-sonnet-5-5"
    )
    error_message = "The settings carry what the infrastructure decided."
  }
  assert {
    condition = (
      keys(aws_ssm_parameter.secret) == ["DATABASE_URL"]
      && aws_ssm_parameter.secret["DATABASE_URL"].type == "SecureString"
      && aws_ssm_parameter.secret["DATABASE_URL"].name == "/emudoc/staging/settings/DATABASE_URL"
      && can(regex(
        "^postgresql\\+psycopg://emudoc:[A-Za-z0-9]{40}@emudoc-test\\.abcdefghijkl\\.eu-central-1\\.rds\\.amazonaws\\.com:5432/emudoc\\?sslmode=verify-full&sslrootcert=/etc/emudoc/rds-ca\\.pem$",
        nonsensitive(aws_ssm_parameter.secret["DATABASE_URL"].value),
      ))
    )
    error_message = "DATABASE_URL is a SecureString that checks the server's certificate against RDS's authority."
  }
  assert {
    condition     = aws_ssm_parameter.release.value == "none" && aws_ssm_parameter.release.name == "/emudoc/staging/release"
    error_message = "No release is recorded until release.sh records one."
  }
  assert {
    condition     = output.dns_records == [{ name = "staging.emudoc.example.com", type = "A", value = "198.51.100.7" }]
    error_message = "Without a zone, the records are listed to be made by hand."
  }
  assert {
    condition     = length(aws_route53_record.app) == 0 && length(aws_sesv2_email_identity.mail) == 0 && length(aws_sns_topic_subscription.alerts) == 0
    error_message = "Staging makes no DNS record, mail identity or subscription it was not asked for."
  }
  assert {
    condition     = !contains(keys(aws_ssm_parameter.setting), "ALERT_EMAIL")
    error_message = "Without an alert address the app is given none."
  }
}

run "production" {
  variables {
    environment       = "production"
    domain            = "emudoc.example.com"
    dns_zone_id       = "Z0123456789ABCDEFGHIJ"
    database_multi_az = true
    backup_days       = 14
    access_log_days   = 14
    data_region       = "eu"
    alert_email       = "operator@example.com"
    restore_drill     = false
    mail              = { domain = "mail.example.com", from = "Emudoc <no-reply@mail.example.com>" }
  }

  assert {
    condition     = aws_db_instance.main.multi_az && aws_db_instance.main.backup_retention_period == 14
    error_message = "Production's database has a standby and keeps its backups for backup_days."
  }
  assert {
    condition     = aws_s3_bucket_lifecycle_configuration.documents.rule[0].noncurrent_version_expiration[0].noncurrent_days == 12
    error_message = "The bucket follows backup_days."
  }
  assert {
    condition = (
      aws_s3_bucket_lifecycle_configuration.logs.rule[0].expiration[0].days == 12
      && aws_ssm_parameter.setting["ACCESS_LOG_DAYS"].value == "14"
    )
    error_message = "The logs follow access_log_days."
  }
  assert {
    condition     = aws_ssm_parameter.setting["DATA_REGION"].value == "eu" && aws_ssm_parameter.setting["MAIL_FROM"].value == "Emudoc <no-reply@mail.example.com>"
    error_message = "The region and the sender are settings when asked for."
  }
  assert {
    condition = (
      toset(keys(aws_ssm_parameter.secret)) == toset(["DATABASE_URL", "SMTP_URL"])
      && nonsensitive(aws_ssm_parameter.secret["SMTP_URL"].value) == "smtp://AKIAEXAMPLEKEY:BAx%2Fy%2Bz%3D@email-smtp.eu-central-1.amazonaws.com:587"
    )
    error_message = "SES's SMTP password is escaped into SMTP_URL, which is a SecureString."
  }
  assert {
    condition = (
      aws_route53_record.app[0].records == toset(["198.51.100.7"])
      && [for record in aws_route53_record.dkim : record.name] == [
        "one._domainkey.mail.example.com", "two._domainkey.mail.example.com", "three._domainkey.mail.example.com",
      ]
    )
    error_message = "With a zone, the address and the DKIM records are made in it."
  }
  assert {
    condition     = length(aws_sns_topic_subscription.alerts) == 1 && strcontains(local.cloud_config, "      EMUDOC_DRILL=no\n")
    error_message = "The alert address is subscribed, and a drill can be turned off."
  }
  assert {
    condition     = aws_ssm_parameter.setting["ALERT_EMAIL"].value == "operator@example.com"
    error_message = "The app mails its own reports to the address the topic does."
  }
  assert {
    condition = one([
      for statement in local.release_policy.Statement : statement
      if statement.Action == "ssm:GetParameterHistory"
      ]).Resource == "arn:aws:ssm:eu-central-1:123456789012:parameter/emudoc/staging/release" && one([
      for statement in local.release_policy.Statement : statement.Resource
      if statement.Sid == "RecordTheRelease"
    ]) == "arn:aws:ssm:eu-central-1:123456789012:parameter/emudoc/production/release"
    error_message = "Production's role reads staging's release history, which release.sh checks a tag against, and records its own."
  }
}

run "without_github" {
  command = plan
  variables {
    github_repository = ""
  }
  assert {
    condition     = length(aws_iam_role.release) == 0 && output.release_role == null
    error_message = "Without a repository, nothing trusts GitHub."
  }
}

run "a_repository_without_its_owner" {
  command = plan
  variables {
    github_repository = "emudoc"
  }
  expect_failures = [var.github_repository]
}

run "backup_days_past_rds" {
  command = plan
  variables {
    backup_days = 40
  }
  expect_failures = [var.backup_days]
}

run "access_logs_gone_too_soon_for_the_rule" {
  command = plan
  variables {
    access_log_days = 2
  }
  expect_failures = [var.access_log_days]
}

run "a_key_among_the_settings" {
  command = plan
  variables {
    settings = {
      OPERATOR_NAME  = "Emudoc Ltd"
      OPERATOR_EMAIL = "privacy@example.com"
      AGENT_MODEL    = "anthropic:claude-sonnet-5-5"
      INDEX_MODEL    = "anthropic:claude-haiku-5-5"
      GOOGLE_API_KEY = "AIza…"
    }
  }
  expect_failures = [var.settings]
}

run "a_tracker_among_the_settings" {
  command = plan
  variables {
    settings = {
      OPERATOR_NAME  = "Emudoc Ltd"
      OPERATOR_EMAIL = "privacy@example.com"
      AGENT_MODEL    = "anthropic:claude-sonnet-5-5"
      INDEX_MODEL    = "anthropic:claude-haiku-5-5"
      SENTRY_DSN     = "https://0123456789abcdef@o1.ingest.de.sentry.io/2"
    }
  }
  expect_failures = [var.settings]
}

run "the_operator_left_unsaid" {
  command = plan
  variables {
    settings = {
      OPERATOR_NAME = "Emudoc Ltd"
      AGENT_MODEL   = "anthropic:claude-sonnet-5-5"
      INDEX_MODEL   = "anthropic:claude-haiku-5-5"
    }
  }
  expect_failures = [var.settings]
}

run "a_setting_the_module_makes" {
  command = plan
  variables {
    settings = {
      OPERATOR_NAME  = "Emudoc Ltd"
      OPERATOR_EMAIL = "privacy@example.com"
      AGENT_MODEL    = "anthropic:claude-sonnet-5-5"
      INDEX_MODEL    = "anthropic:claude-haiku-5-5"
      APP_URL        = "https://elsewhere.example.com"
    }
  }
  expect_failures = [aws_ssm_parameter.setting]
}

run "a_domain_with_a_scheme" {
  command = plan
  variables {
    domain = "https://emudoc.example.com"
  }
  expect_failures = [var.domain]
}
