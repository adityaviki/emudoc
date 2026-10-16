variable "environment" {
  description = "The environment's name, in every resource's name: staging, production."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,19}$", var.environment))
    error_message = "The environment's name is 2 to 20 lowercase letters, digits and hyphens, starting with a letter."
  }
}

variable "domain" {
  description = "The name the app is reached under, such as emudoc.example.com; APP_URL is https:// and this."
  type        = string

  validation {
    condition     = can(regex("^([a-z0-9]([a-z0-9-]*[a-z0-9])?\\.)+[a-z]{2,}$", var.domain))
    error_message = "The domain is a host name in lowercase, such as emudoc.example.com, without a scheme or a path."
  }
}

variable "dns_zone_id" {
  description = "The Route 53 hosted zone the domain's record (and the mail domain's DKIM records) go into; empty when the DNS is kept elsewhere, and the dns_records output lists what to create there."
  type        = string
  default     = ""
}

variable "repository" {
  description = "The ECR repository the images are pushed to, by name (the account root's repository output)."
  type        = string
  default     = "emudoc"
}

variable "network_cidr" {
  description = "The VPC's address range, a /16; the instance's subnet and the database's two are carved from it."
  type        = string
  default     = "10.80.0.0/16"

  validation {
    condition     = can(cidrhost(var.network_cidr, 0)) && endswith(var.network_cidr, "/16")
    error_message = "The network is a /16, such as 10.80.0.0/16."
  }
}

variable "instance_type" {
  description = "The EC2 instance that runs the API, the worker, ClamAV, Ollama and Caddy. x86_64 only: the image is built and checked for it. It needs about 8 GB of memory, and Ollama embeds on its CPUs."
  type        = string
  default     = "t3a.large"
}

variable "disk_gb" {
  description = "The instance's disk: the images (Ollama's alone is 9 GB on disk), the embedding model, ClamAV's signatures and the logs. The documents are in the bucket."
  type        = number
  default     = 60
}

variable "database_class" {
  description = "The RDS instance class."
  type        = string
  default     = "db.t4g.small"
}

variable "database_storage_gb" {
  description = "The database's disk at creation; RDS grows it up to database_max_storage_gb."
  type        = number
  default     = 20
}

variable "database_max_storage_gb" {
  description = "How far RDS may grow the database's disk by itself."
  type        = number
  default     = 200
}

variable "database_multi_az" {
  description = "A standby in a second availability zone that takes over under the same address."
  type        = bool
  default     = false
}

variable "deletion_protection" {
  description = "Whether the database refuses to be deleted, and leaves a final snapshot when it is. The final snapshot outlives BACKUP_DAYS until it is deleted by hand."
  type        = bool
  default     = true
}

variable "backup_days" {
  description = "BACKUP_DAYS: how long the database's backups and the bucket's earlier versions are kept, which the privacy policy names. RDS keeps at most 35."
  type        = number
  default     = 35

  validation {
    condition     = var.backup_days >= 3 && var.backup_days <= 35 && floor(var.backup_days) == var.backup_days
    error_message = "backup_days is a whole number from 3 to 35: RDS keeps its backups at most 35 days, and the bucket's rule keeps earlier versions two days fewer."
  }
}

variable "data_region" {
  description = "DATA_REGION: eu, us, or empty for no promise. The app checks the bucket and the models against it at start; the region the provider is configured for must be in it."
  type        = string
  default     = ""

  validation {
    condition     = contains(["", "eu", "us"], var.data_region)
    error_message = "data_region is eu, us or empty."
  }
}

variable "hosting_provider" {
  description = "HOSTING_PROVIDER, as the subprocessor list names who runs the servers and the database; empty says Amazon Web Services and the region."
  type        = string
  default     = ""
}

variable "settings" {
  description = "The app's other settings, by their names in .env.example: the operator, the models, their prices and limits. An empty value is no setting. Secrets (keys, passwords, client secrets) never go here: they are put into the secrets path by hand (deploying.md, \"On AWS\")."
  type        = map(string)
  default     = {}

  validation {
    condition     = alltrue([for name in keys(var.settings) : can(regex("^[A-Z][A-Z0-9_]*$", name))])
    error_message = "A setting's name is the variable's, in capitals, as .env.example spells it."
  }

  # What Settings.production_problems (backend/app/config.py) asks for that only the operator
  # can say; backend/tests/test_deploy.py keeps this list in step with it.
  validation {
    condition = alltrue([
      for name in ["OPERATOR_NAME", "OPERATOR_EMAIL", "AGENT_MODEL", "INDEX_MODEL"] :
      lookup(var.settings, name, "") != ""
    ])
    error_message = "OPERATOR_NAME, OPERATOR_EMAIL, AGENT_MODEL and INDEX_MODEL are the operator's to fill in (deploy/aws/shared/main.tf): production refuses to start without them."
  }

  validation {
    condition     = alltrue([for value in values(var.settings) : !can(regex("[\\r\\n]", value))])
    error_message = "A setting's value is one line: no env file can carry a line break."
  }

  validation {
    condition = alltrue([
      for name in keys(var.settings) :
      !can(regex("(_KEY|_SECRET|_PASSWORD|_TOKEN|_CREDENTIALS)$|^(DATABASE_URL|DATABASE_LISTEN_URL|SMTP_URL)$", name))
    ])
    error_message = "Keys, secrets, passwords and the URLs that carry one are put into the secrets path by hand, never into the module's settings, which the plan shows and the state keeps."
  }
}

variable "mail" {
  description = "Mail through Amazon SES: the domain the app mails from, verified with DKIM, and MAIL_FROM. Null leaves SMTP_URL and MAIL_FROM to the secrets path."
  type = object({
    domain = string
    from   = string
  })
  default = null
}

variable "alert_email" {
  description = "Where the instance reports a failed restore drill, credentials refresh or start: an SNS subscription the address must confirm. Empty reports to the topic alone."
  type        = string
  default     = ""
}

variable "restore_drill" {
  description = "Whether the instance runs the monthly restore drill (deploy/managed-restore-drill.sh with deploy/rds-drill.sh)."
  type        = bool
  default     = true
}

variable "drill_instance_class" {
  description = "The class of the drill's copy of the database; empty takes database_class."
  type        = string
  default     = ""
}
