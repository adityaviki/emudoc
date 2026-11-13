# The settings staging and production share, so that staging runs what production will: the
# models and their prices, the backups' window and how long the logs are kept, and the
# repository whose CI releases them, which the account root reads as well, with the address its
# alarms go to. Each root adds what is its own (its spending cap, its alert address) with
# merge(); keys and secrets go into each environment's secrets path by hand, never here
# (deploying.md, "On AWS"). An empty value is no setting; the plan refuses AGENT_MODEL and
# INDEX_MODEL empty.

output "settings" {
  description = "Settings by their names in .env.example."
  value = {
    # Where members write for help from the app, empty for the workspace's admins; and the
    # status page outside AWS that the app links to (deploying.md, "The status page").
    SUPPORT_EMAIL   = ""
    STATUS_PAGE_URL = ""

    # The models, each named by its version, and a fallback of another provider for each
    # (deploying.md, "The models"); every one of them priced in MODEL_PRICES, so that the
    # spending limits count it. A google: model needs GOOGLE_PAID_TIER=true with a paid key.
    AGENT_MODEL          = ""
    AGENT_FALLBACK_MODEL = ""
    INDEX_MODEL          = ""
    INDEX_FALLBACK_MODEL = ""
    MODEL_PRICES         = ""
    PRICE_CURRENCY       = ""
    GOOGLE_PAID_TIER     = ""

    # Nobody signs up (deploying.md, "The first run, and who signs up"), as the app's default
    # says too.
    SIGNUP_OPEN = "false"
  }
}

output "backup_days" {
  description = "BACKUP_DAYS: RDS's retention and the bucket's rule."
  value       = 35
}

output "access_log_days" {
  description = "How long the access logs of the documents bucket and the VPC's flow logs are kept (deploying.md, \"The account's records\")."
  value       = 30
}

output "server_log_days" {
  description = "SERVER_LOG_DAYS: how long the instance's journal keeps the containers' logs and RDS Postgres's (deploying.md, \"Logs\")."
  value       = 30
}

output "github_repository" {
  description = "The GitHub repository, as owner/name, whose workflows on main push the images and release them (deploying.md, \"Releases from CI\"); empty for no role that trusts GitHub."
  value       = "emudocai/emudoc"
}

output "account_alert_email" {
  description = "Where the account root's alarms on its trail go (deploying.md, \"The account's records\"), as an SNS subscription the address must confirm; empty sends them to the topic alone. Each environment's alerts go to its root's alert_email."
  value       = ""
}

output "receipt_rule_set" {
  description = "The name of the account's one active SES receipt rule set in the region, which the account root makes and each environment's mail in adds its rule to (deploying.md, \"Mail in\")."
  value       = "emudoc"
}
