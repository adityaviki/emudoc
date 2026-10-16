# The settings staging and production share, so that staging runs what production will: the
# operator's word, the models and their prices, the backups' window and the region. Each root
# adds what is its own (its spending cap, its alert address) with merge(); keys and secrets go
# into each environment's secrets path by hand, never here (deploying.md, "On AWS"). An empty
# value is no setting; the plan refuses OPERATOR_NAME, OPERATOR_EMAIL, AGENT_MODEL and
# INDEX_MODEL empty.

output "settings" {
  description = "Settings by their names in .env.example."
  value = {
    # Who runs the deployment, as the terms of service and the privacy policy name them: the
    # company or person with a postal address, and the address people write to.
    OPERATOR_NAME  = ""
    OPERATOR_EMAIL = ""

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

output "data_region" {
  description = "DATA_REGION: eu, us, or empty for no promise (deploying.md, \"Keeping the data in one region\")."
  value       = ""
}
