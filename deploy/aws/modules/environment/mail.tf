# Mail through Amazon SES, when var.mail names a domain: the domain verified by DKIM (its
# records in the zone, or listed by the dns_records output) and SMTP credentials that may send
# from it and do nothing else. A new SES account sends only to verified addresses until AWS
# grants production access, which is asked for by hand (deploying.md, "On AWS").

resource "aws_sesv2_email_identity" "mail" {
  count = var.mail == null ? 0 : 1

  email_identity = var.mail.domain

  # Easy DKIM: SES keeps the key and rotates it; the zone holds three CNAMEs to it.
  dkim_signing_attributes {
    next_signing_key_length = "RSA_2048_BIT"
  }
}

locals {
  dkim_records = var.mail == null ? [] : [
    for token in aws_sesv2_email_identity.mail[0].dkim_signing_attributes[0].tokens : {
      name  = "${token}._domainkey.${var.mail.domain}"
      type  = "CNAME"
      value = "${token}.dkim.amazonses.com"
    }
  ]
}

resource "aws_iam_user" "smtp" {
  count = var.mail == null ? 0 : 1

  name = "${local.name}-smtp"
}

resource "aws_iam_user_policy" "smtp" {
  count = var.mail == null ? 0 : 1

  name = "send"
  user = aws_iam_user.smtp[0].name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "ses:SendRawEmail"
      Resource = aws_sesv2_email_identity.mail[0].arn
    }]
  })
}

# SES's SMTP password is derived from the key's secret; both are in the state, which the roots
# encrypt. A new key is made by replacing this resource (tofu apply -replace=…) and releasing.
resource "aws_iam_access_key" "smtp" {
  count = var.mail == null ? 0 : 1

  user = aws_iam_user.smtp[0].name
}

locals {
  smtp_url = var.mail == null ? "" : format(
    "smtp://%s:%s@email-smtp.%s.amazonaws.com:587",
    urlencode(aws_iam_access_key.smtp[0].id),
    urlencode(aws_iam_access_key.smtp[0].ses_smtp_password_v4),
    local.region,
  )
}
