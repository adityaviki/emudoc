# Postgres 17 on RDS (deploying.md, "The database"): pgvector comes with it (0.8 or later from
# 17.1), on the private subnets with no public address, TLS required and the certificate
# checked against RDS's own authority, encrypted at rest, backed up for BACKUP_DAYS to any
# moment within them. The app connects as the master user, which may create the extension
# the first migration asks for; nobody else connects.

resource "aws_db_subnet_group" "main" {
  name       = local.name
  subnet_ids = aws_subnet.private[*].id

  tags = { Name = local.name }
}

# The limits deploying.md's "The role's timeouts" gives the app's role, here for the whole
# server, which is the app's alone: the app reads them as its connection sees them. The work
# whose length is the data's (the migrations, a re-embedding, the housekeeping, a purge, a
# restore) lifts them for its own transaction.
resource "aws_db_parameter_group" "main" {
  name   = "${local.name}-postgres17"
  family = "postgres17"

  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }

  parameter {
    name  = "statement_timeout"
    value = "30000"
  }

  parameter {
    name  = "idle_in_transaction_session_timeout"
    value = "300000"
  }

  # Postgres's own log is among the server logs, kept for SERVER_LOG_DAYS, which hold no email
  # address. An error's DETAIL repeats the key or row it refused ("Key
  # (email)=(…) already exists"), so the log keeps an error's first line and its statement
  # alone, whose values the app binds as $1. RDS deletes a file of the log, an hour's lines,
  # once it is older than its period in minutes (7 days at most): a day short of the policy's.
  parameter {
    name  = "log_error_verbosity"
    value = "terse"
  }

  parameter {
    name  = "rds.log_retention_period"
    value = tostring(min(var.server_log_days - 1, 7) * 1440)
  }

  tags = { Name = local.name }
}

# Letters and digits only, so that it stands in DATABASE_URL as it is. It is kept in the state,
# which the roots encrypt.
resource "random_password" "database" {
  length  = 40
  special = false
}

resource "aws_db_instance" "main" {
  identifier     = local.name
  engine         = "postgres"
  engine_version = "17"
  instance_class = var.database_class

  allocated_storage     = var.database_storage_gb
  max_allocated_storage = var.database_max_storage_gb
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = "emudoc"
  username = "emudoc"
  password = random_password.database.result

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.database.id]
  parameter_group_name   = aws_db_parameter_group.main.name
  publicly_accessible    = false
  multi_az               = var.database_multi_az
  ca_cert_identifier     = "rds-ca-rsa2048-g1"

  # Point-in-time recovery over the last backup_days, and no snapshot kept longer, so that a
  # deleted account is gone from the backups within BACKUP_DAYS.
  backup_retention_period  = var.backup_days
  backup_window            = "02:00-02:30"
  maintenance_window       = "sun:03:00-sun:04:00"
  copy_tags_to_snapshot    = true
  delete_automated_backups = true

  # Database Insights' standard mode (deploying.md, "The account's records"): the load by wait,
  # statement and host over the last 7 days, which AWS keeps without charge. A statement is the
  # app's with $1 in place of every value, since it binds them all, and the one host is the
  # instance.
  database_insights_mode                = "standard"
  performance_insights_enabled          = true
  performance_insights_retention_period = 7

  auto_minor_version_upgrade = true
  apply_immediately          = false

  deletion_protection       = var.deletion_protection
  skip_final_snapshot       = !var.deletion_protection
  final_snapshot_identifier = var.deletion_protection ? "${local.name}-final" : null

  tags = { Name = local.name }
}

locals {
  # RDS's authority signs the server's certificate, which no public bundle holds: the
  # instance fetches the region's bundle (cloud-config.yaml) and the containers find it here.
  database_authority = "/etc/emudoc/rds-ca.pem"
  database_url = format(
    "postgresql+psycopg://%s:%s@%s:%d/%s?sslmode=verify-full&sslrootcert=%s",
    aws_db_instance.main.username,
    random_password.database.result,
    aws_db_instance.main.address,
    aws_db_instance.main.port,
    aws_db_instance.main.db_name,
    local.database_authority,
  )
}
