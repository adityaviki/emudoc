# The account's one active SES receipt rule set in the region: SES keeps one active a region, so
# staging and production cannot each have their own. Each environment's mail in adds its rule to
# it by the name shared/ gives it (modules/environment/inbound.tf), for its own domain; with no
# rule it receives nothing.

resource "aws_ses_receipt_rule_set" "inbound" {
  rule_set_name = module.shared.receipt_rule_set
}

resource "aws_ses_active_receipt_rule_set" "inbound" {
  rule_set_name = aws_ses_receipt_rule_set.inbound.rule_set_name
}
