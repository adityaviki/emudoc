# The instance: Amazon Linux 2023 with Docker, which runs deploy/compose.yml from the image with
# deploy/aws/compose.yml over it (Caddy in front, the credentials and the database's authority
# for the containers). It holds nothing that is not made again: the documents are in the
# bucket, the rows in RDS, the settings in SSM, and a new instance runs the recorded release
# by itself at its first start. So a change to the boot (cloud-config.yaml) replaces it, and a
# new Amazon Linux comes with the next replacement, not at every plan.

data "aws_ssm_parameter" "amazon_linux" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

locals {
  # What the host's scripts read, from the boot onwards; plain words only, since it is both a
  # shell file and a systemd EnvironmentFile.
  host_env = {
    EMUDOC_ENVIRONMENT = var.environment
    EMUDOC_REPOSITORY  = data.aws_ecr_repository.app.repository_url
    EMUDOC_PARAMETERS  = local.parameters
    EMUDOC_APP_ROLE    = aws_iam_role.app.arn
    EMUDOC_READER_ROLE = aws_iam_role.reader.arn
    EMUDOC_ALERTS      = aws_sns_topic.alerts.arn
    # Alertmanager's (deploy/aws/compose.yml, deploy/aws/alertmanager.yml).
    EMUDOC_ALERTMANAGER_ROLE = aws_iam_role.alertmanager.arn
    EMUDOC_HEARTBEAT         = aws_sns_topic.heartbeat.arn
    EMUDOC_DRILL             = var.restore_drill ? "yes" : "no"
    AWS_REGION               = local.region
    AWS_DEFAULT_REGION       = local.region
    RDS_SOURCE               = aws_db_instance.main.identifier
    RDS_SUBNET_GROUP         = aws_db_subnet_group.main.name
    RDS_SECURITY_GROUPS      = aws_security_group.database.id
    RDS_PARAMETER_GROUP      = aws_db_parameter_group.main.name
    RDS_INSTANCE_CLASS       = var.drill_instance_class != "" ? var.drill_instance_class : var.database_class
  }

  cloud_config = templatefile("${path.module}/cloud-config.yaml", {
    host_env        = join("", [for name, value in local.host_env : "${name}=${value}\n"])
    registry        = split("/", data.aws_ecr_repository.app.repository_url)[0]
    release         = file("${path.module}/emudoc-release")
    alert           = file("${path.module}/emudoc-alert")
    compose_version = "5.5.1"
    compose_sha256  = "db1889184726840f75c4f9c001048430d4f25b3be3cb084d3ddd762bc0aed576"
  })
}

resource "aws_instance" "app" {
  ami                    = data.aws_ssm_parameter.amazon_linux.value
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.instance.id]
  iam_instance_profile   = aws_iam_instance_profile.instance.name

  user_data_base64            = base64gzip(local.cloud_config)
  user_data_replace_on_change = true

  # The metadata service answers the host alone: a container is one hop further, and the
  # token's reply does not reach it.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
    instance_metadata_tags      = "disabled"
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.disk_gb
    encrypted             = true
    delete_on_termination = true
  }

  tags = { Name = local.name }

  lifecycle {
    ignore_changes = [ami]
  }

  # The release reads the settings and the database at its first start.
  depends_on = [
    aws_ssm_parameter.setting,
    aws_ssm_parameter.secret,
    aws_iam_role_policy.instance,
    aws_iam_role_policy_attachment.ssm,
  ]
}

# The address the domain leads to, kept when the instance is replaced.
resource "aws_eip" "instance" {
  domain   = "vpc"
  instance = aws_instance.app.id

  tags = { Name = local.name }
}
