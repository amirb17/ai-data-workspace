
resource "aws_security_group" "worker" {
  count       = var.enable_network ? 1 : 0
  name_prefix = "ai-workspace-worker-"
  description = "Security group for processing worker"
  vpc_id      = var.vpc_id

  tags = merge(local.tags, {
    Name = "ai-workspace-worker-sg"
  })
}

resource "aws_security_group" "database" {
  count       = var.enable_network ? 1 : 0
  name_prefix = "ai-workspace-database-"
  description = "Private PostgreSQL database access"
  vpc_id      = var.vpc_id

  tags = merge(local.tags, {
    Name = "ai-workspace-database-sg"
  })
}

resource "aws_vpc_security_group_ingress_rule" "worker_to_postgres" {
  count                        = var.enable_network ? 1 : 0
  security_group_id            = aws_security_group.database[0].id
  referenced_security_group_id = aws_security_group.worker[0].id

  ip_protocol = "tcp"
  from_port   = 5432
  to_port     = 5432
  description = "PostgreSQL access from processing worker only"
}

resource "aws_vpc_security_group_egress_rule" "worker_to_postgres" {
  count                        = var.enable_network ? 1 : 0
  security_group_id            = aws_security_group.worker[0].id
  referenced_security_group_id = aws_security_group.database[0].id

  ip_protocol = "tcp"
  from_port   = 5432
  to_port     = 5432
  description = "Worker access to PostgreSQL"
}

resource "aws_db_subnet_group" "private" {
  count      = var.enable_network ? 1 : 0
  name       = "ai-workspace-${var.environment}-db-subnets"
  subnet_ids = [for subnet in aws_subnet.private : subnet.id]

  tags = merge(local.tags, {
    Name = "ai-workspace-db-subnets"
  })

  lifecycle {
    precondition {
      condition = length(distinct([
        for subnet in values(var.private_subnets) :
        subnet.availability_zone
      ])) >= 2

      error_message = "RDS requires subnets in at least two availability zones."
    }
  }
}

resource "aws_vpc_security_group_egress_rule" "worker_https" {
  count = var.enable_network ? 1 : 0

  security_group_id = aws_security_group.worker[0].id

  ip_protocol = "tcp"
  from_port   = 443
  to_port     = 443
  cidr_ipv4   = "0.0.0.0/0"

  description = "Outbound HTTPS for approved AWS service APIs and ECR"
}
