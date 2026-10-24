# Staging: production's module, settings and image at smaller sizes, with a database, a
# bucket, model keys and a spending cap of its own. Every release runs here first
# (deploying.md, "On AWS").
#
#   tofu -chdir=deploy/aws/staging init -backend-config=bucket=emudoc-state-<account>
#   tofu -chdir=deploy/aws/staging apply

terraform {
  required_version = ">= 1.12"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.69"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.9"
    }
  }

  backend "s3" {
    key          = "staging.tfstate"
    region       = "eu-central-1"
    use_lockfile = true
    encrypt      = true
  }

  # The state holds the database's password and SES's SMTP key: OpenTofu encrypts it with the
  # account root's key before it leaves this machine, and refuses to write it otherwise.
  encryption {
    key_provider "aws_kms" "state" {
      kms_key_id = "alias/emudoc-state"
      region     = "eu-central-1"
      key_spec   = "AES_256"
    }
    method "aes_gcm" "state" {
      keys = key_provider.aws_kms.state
    }
    state {
      method   = method.aes_gcm.state
      enforced = true
    }
    plan {
      method   = method.aes_gcm.state
      enforced = true
    }
  }
}

provider "aws" {
  region = "eu-central-1"

  default_tags {
    tags = { app = "emudoc", environment = "staging" }
  }
}

module "shared" {
  source = "../shared"
}

module "emudoc" {
  source = "../modules/environment"

  environment = "staging"
  domain      = "staging.emudoc.example.com"
  # A Route 53 zone the domain is in, or empty to make the records of `tofu output dns_records`
  # by hand.
  dns_zone_id = ""

  instance_type       = "t3a.large"
  database_class      = "db.t4g.small"
  database_multi_az   = false
  deletion_protection = false

  backup_days     = module.shared.backup_days
  access_log_days = module.shared.access_log_days
  server_log_days = module.shared.server_log_days
  data_region     = module.shared.data_region
  settings = merge(module.shared.settings, {
    DAILY_SPEND_CAP = "5"
  })
  mail = {
    domain = "staging.emudoc.example.com"
    from   = "Emudoc staging <no-reply@staging.emudoc.example.com>"
  }
  # The projects' addresses, <token>@in.staging.emudoc.example.com, whose mail becomes their documents; null
  # receives none.
  inbound = {
    domain   = "in.staging.emudoc.example.com"
    rule_set = module.shared.receipt_rule_set
  }
  alert_email = ""

  github_repository = module.shared.github_repository
}

output "instance_id" {
  value = module.emudoc.instance_id
}

output "app_url" {
  value = module.emudoc.app_url
}

output "dns_records" {
  value = module.emudoc.dns_records
}

output "parameters" {
  value = module.emudoc.parameters
}

output "release_role" {
  value = module.emudoc.release_role
}

output "pages" {
  value = module.emudoc.pages
}
