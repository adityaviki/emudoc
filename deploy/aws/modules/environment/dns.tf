# The domain's address, the mail domain's DKIM, SPF and DMARC records and the inbound domain's
# MX and DKIM, in Route 53 when dns_zone_id names a zone; otherwise the dns_records output lists
# them for the DNS kept elsewhere. Caddy asks Let's Encrypt for the certificate once the name
# leads to the instance.

locals {
  dns_records = concat(
    [{ name = var.domain, type = "A", value = aws_eip.instance.public_ip }],
    local.dkim_records,
    local.mail_records,
    local.inbound_records,
  )
}

resource "aws_route53_record" "app" {
  count = var.dns_zone_id == "" ? 0 : 1

  zone_id = var.dns_zone_id
  name    = var.domain
  type    = "A"
  ttl     = 300
  records = [aws_eip.instance.public_ip]
}

resource "aws_route53_record" "dkim" {
  count = var.dns_zone_id == "" || var.mail == null ? 0 : 3

  zone_id = var.dns_zone_id
  name    = local.dkim_records[count.index].name
  type    = "CNAME"
  ttl     = 1800
  records = [local.dkim_records[count.index].value]
}

# By name and type, which the configuration knows before AWS answers.
resource "aws_route53_record" "mail" {
  for_each = var.dns_zone_id == "" ? {} : { for record in local.mail_records : "${record.type} ${record.name}" => record }

  zone_id = var.dns_zone_id
  name    = each.value.name
  type    = each.value.type
  ttl     = 1800
  # The provider quotes a TXT record's text itself.
  records = [each.value.value]
}

# By name and type, as the mail's are; the inbound domain's DKIM CNAMEs by their names, which
# the configuration knows only once SES answers, hence by their place.
resource "aws_route53_record" "inbound_mx" {
  count = var.dns_zone_id == "" || var.inbound == null ? 0 : 1

  zone_id = var.dns_zone_id
  name    = var.inbound.domain
  type    = "MX"
  ttl     = 1800
  records = [local.inbound_records[0].value]
}

resource "aws_route53_record" "inbound_dkim" {
  count = var.dns_zone_id == "" || var.inbound == null ? 0 : 3

  zone_id = var.dns_zone_id
  name    = local.inbound_records[count.index + 1].name
  type    = "CNAME"
  ttl     = 1800
  records = [local.inbound_records[count.index + 1].value]
}
