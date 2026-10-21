# The settings staging and production share, so that staging runs what production will: the
# operator's word, the models and their prices, the backups' window, how long the access logs
# are kept and the region, and the repository whose CI releases them, which the account root
# reads as well, with the address its alarms go to. Each root adds what is its own (its spending cap, its alert address) with
# merge(); keys and secrets go into each environment's secrets path by hand, never here
# (deploying.md, "On AWS"). An empty value is no setting; the plan refuses OPERATOR_NAME,
# OPERATOR_EMAIL, AGENT_MODEL and INDEX_MODEL empty.

output "settings" {
  description = "Settings by their names in .env.example."
  value = {
    # Who runs the deployment, as the terms of service and the privacy policy name them: the
    # company or person with a postal address, and the address people write to; where members
    # write for help from the app, empty for OPERATOR_EMAIL; and the status page outside AWS
    # that the app links to (deploying.md, "The status page").
    OPERATOR_NAME   = ""
    OPERATOR_EMAIL  = ""
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
  }
}

output "backup_days" {
  description = "BACKUP_DAYS, which the privacy policy names: RDS's retention and the bucket's rule."
  value       = 35
}

output "access_log_days" {
  description = "ACCESS_LOG_DAYS, which the privacy policy names: how long the access logs of the documents bucket and the VPC's flow logs are kept (deploying.md, \"The account's records\")."
  value       = 30
}

output "server_log_days" {
  description = "SERVER_LOG_DAYS, which the privacy policy names: how long the instance's journal keeps the containers' logs and RDS Postgres's (deploying.md, \"Logs\")."
  value       = 30
}

output "data_region" {
  description = "DATA_REGION: eu, us, or empty for no promise (deploying.md, \"Keeping the data in one region\")."
  value       = ""
}

output "github_repository" {
  description = "The GitHub repository, as owner/name, whose workflows on main push the images and release them (deploying.md, \"Releases from CI\"); empty for no role that trusts GitHub."
  value       = "emudocai/emudoc"
}

output "account_alert_email" {
  description = "Where the account root's alarms on its trail go (deploying.md, \"The account's records\"), as an SNS subscription the address must confirm; empty sends them to the topic alone. Each environment's alerts go to its root's alert_email."
  value       = ""
}
