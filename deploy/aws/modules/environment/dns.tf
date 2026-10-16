# The domain's address and the mail domain's DKIM records, in Route 53 when dns_zone_id names
# a zone; otherwise the dns_records output lists them for the DNS kept elsewhere. Caddy asks
# Let's Encrypt for the certificate once the name leads to the instance.

locals {
  dns_records = concat(
    [{ name = var.domain, type = "A", value = aws_eip.instance.public_ip }],
    local.dkim_records,
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
