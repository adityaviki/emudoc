# A network of the environment's own: one public subnet for the instance, which the internet
# reaches on 80 and 443 only, and two private ones for the database (RDS asks for two zones),
# which have no route out and take connections from the instance alone.

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  zones = slice(data.aws_availability_zones.available.names, 0, 2)
}

resource "aws_vpc" "main" {
  cidr_block           = var.network_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = local.name }
}

# The VPC's default group lets its members reach each other; nothing uses it, so it allows
# nothing.
resource "aws_default_security_group" "main" {
  vpc_id = aws_vpc.main.id

  tags = { Name = "${local.name}-default" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = { Name = local.name }
}

resource "aws_subnet" "public" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.network_cidr, 8, 0)
  availability_zone = local.zones[0]

  tags = { Name = "${local.name}-public" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = { Name = "${local.name}-public" }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

resource "aws_subnet" "private" {
  count = 2

  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.network_cidr, 8, 10 + count.index)
  availability_zone = local.zones[count.index]

  tags = { Name = "${local.name}-private-${count.index}" }
}

# The VPC's main route table, which the private subnets use: the VPC's own range and no more.

resource "aws_security_group" "instance" {
  name        = "${local.name}-instance"
  description = "Emudoc's instance: HTTPS and the ACME challenge in, anything out"
  vpc_id      = aws_vpc.main.id

  tags = { Name = "${local.name}-instance" }
}

# Caddy answers on 443 over TCP and, for HTTP/3, UDP, and on 80 for the ACME HTTP challenge
# and the redirect to HTTPS. Nothing else listens: the instance is reached through Systems
# Manager, never SSH.
resource "aws_vpc_security_group_ingress_rule" "web" {
  for_each = {
    http  = { protocol = "tcp", port = 80 }
    https = { protocol = "tcp", port = 443 }
    http3 = { protocol = "udp", port = 443 }
  }

  security_group_id = aws_security_group.instance.id
  description       = each.key
  ip_protocol       = each.value.protocol
  from_port         = each.value.port
  to_port           = each.value.port
  cidr_ipv4         = "0.0.0.0/0"
}

# Out to the model providers, the image registries, ClamAV's signatures, Ollama's models, the
# AWS APIs and the database.
resource "aws_vpc_security_group_egress_rule" "instance" {
  security_group_id = aws_security_group.instance.id
  description       = "anything"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_security_group" "database" {
  name        = "${local.name}-database"
  description = "Emudoc's database: Postgres from the instance only"
  vpc_id      = aws_vpc.main.id

  tags = { Name = "${local.name}-database" }
}

resource "aws_vpc_security_group_ingress_rule" "database" {
  security_group_id            = aws_security_group.database.id
  description                  = "Postgres from the instance"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = aws_security_group.instance.id
}
