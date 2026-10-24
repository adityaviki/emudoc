# The app's settings as SSM parameters under /emudoc/<environment>/settings, which the host's
# release writes into the env file the containers read (deploy/aws/host/settings). The module
# writes what it knows from the infrastructure and the roots' `settings`; the operator puts the
# secrets it cannot know (the models' keys, the OAuth clients' secrets) under .../secrets by
# hand, so that no key passes through a plan or the state. A setting may live in one place
# only, and the release refuses one set twice.

locals {
  hosting_provider = var.hosting_provider != "" ? var.hosting_provider : "Amazon Web Services, ${local.region}"

  # Everything here is what Settings.production_problems (backend/app/config.py) asks of a
  # deployment and the infrastructure decides; the operator's part (OPERATOR_NAME,
  # OPERATOR_EMAIL, the models) comes in var.settings.
  managed_settings = merge(
    {
      APP_URL            = "https://${var.domain}"
      STORAGE_URL        = "s3://${aws_s3_bucket.documents.bucket}"
      AWS_DEFAULT_REGION = local.region
      SCANNER_URL        = "clamd://clamav:3310"
      EMBEDDING_BASE_URL = "http://embeddings:8080/v1"
      HOSTING_PROVIDER   = local.hosting_provider
      BACKUP_DAYS        = tostring(var.backup_days)
      ACCESS_LOG_DAYS    = tostring(var.access_log_days)
      SERVER_LOG_DAYS    = tostring(var.server_log_days)
      # What the error tracker files the reports under, should the operator give it a
      # SENTRY_DSN (a secret, since it carries the project's key): staging and production
      # are both ENVIRONMENT=production.
      SENTRY_ENVIRONMENT = var.environment
    },
    var.data_region == "" ? {} : { DATA_REGION = var.data_region },
    var.mail == null ? {} : {
      MAIL_FROM         = var.mail.from
      MAIL_EVENTS_QUEUE = aws_sqs_queue.mail_events[0].url
    },
    var.inbound == null ? {} : {
      INBOUND_DOMAIN     = var.inbound.domain
      INBOUND_MAIL_QUEUE = aws_sqs_queue.inbound[0].url
    },
    # The app's own reports (the jobs given up, the spending, the quarantines, its watch) go
    # where the instance's do.
    var.alert_email == "" ? {} : { ALERT_EMAIL = var.alert_email },
  )

  secret_settings = merge(
    { DATABASE_URL = local.database_url },
    var.mail == null ? {} : { SMTP_URL = local.smtp_url },
  )

  # An empty value in the roots' settings is a setting left unset.
  operator_settings = { for name, value in var.settings : name => value if value != "" }

  settings = merge(local.operator_settings, local.managed_settings)
}

resource "aws_ssm_parameter" "setting" {
  for_each = local.settings

  name  = "${local.parameters}/settings/${each.key}"
  type  = "String"
  tier  = "Intelligent-Tiering"
  value = each.value

  lifecycle {
    precondition {
      condition = length(setintersection(
        keys(local.operator_settings),
        concat(keys(local.managed_settings), keys(local.secret_settings)),
      )) == 0
      error_message = "settings names one the module sets itself (APP_URL, STORAGE_URL, AWS_DEFAULT_REGION, SCANNER_URL, EMBEDDING_BASE_URL, HOSTING_PROVIDER, BACKUP_DAYS, ACCESS_LOG_DAYS, SERVER_LOG_DAYS, SENTRY_ENVIRONMENT, DATA_REGION, MAIL_FROM, MAIL_EVENTS_QUEUE, INBOUND_DOMAIN, INBOUND_MAIL_QUEUE, ALERT_EMAIL, DATABASE_URL, SMTP_URL): use the module's own variable for it."
    }
  }
}

# Its value is in the state either way, and the plan shows it as sensitive.
resource "aws_ssm_parameter" "secret" {
  for_each = nonsensitive(toset(keys(local.secret_settings)))

  name  = "${local.parameters}/settings/${each.key}"
  type  = "SecureString"
  value = local.secret_settings[each.key]
}

# The image tag the instance runs, written by deploy/aws/release.sh once a release succeeded, so
# that a new instance starts the same one by itself.
resource "aws_ssm_parameter" "release" {
  name  = "${local.parameters}/release"
  type  = "String"
  value = "none"

  lifecycle {
    ignore_changes = [value]
  }
}
