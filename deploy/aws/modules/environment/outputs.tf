output "instance_id" {
  description = "The instance, for release.sh and for a shell: aws ssm start-session --target <id>."
  value       = aws_instance.app.id
}

output "address" {
  description = "The instance's public address, which the domain leads to."
  value       = aws_eip.instance.public_ip
}

output "app_url" {
  description = "APP_URL."
  value       = "https://${var.domain}"
}

output "bucket" {
  description = "The documents' bucket."
  value       = aws_s3_bucket.documents.bucket
}

output "database" {
  description = "The RDS instance's identifier and address."
  value       = { identifier = aws_db_instance.main.identifier, address = aws_db_instance.main.address }
}

output "parameters" {
  description = "The SSM path of the settings (/settings), the secrets the operator adds (/secrets) and the release."
  value       = local.parameters
}

output "alerts" {
  description = "The SNS topic the instance reports failures to."
  value       = aws_sns_topic.alerts.arn
}

output "dns_records" {
  description = "The records the domain and the mail need, for a DNS outside Route 53; with dns_zone_id they are made here."
  value       = local.dns_records
}
