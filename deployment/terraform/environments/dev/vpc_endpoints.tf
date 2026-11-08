variable "enable_secretsmanager_endpoint" {
  description = "Enable private Secrets Manager access independently of verifier deployment"
  type        = bool
  default     = false
}

# Verify DNS prerequisites of the selected existing VPC.

data "aws_vpc" "verifier_network" {
  count = (var.enable_verifier_lambda || var.enable_secretsmanager_endpoint) ? 1 : 0
  id    = var.vpc_id
}


variable "enable_s3_gateway_endpoint" {
  description = "Enable private S3 connectivity independently of the verifier Lambda"
  type        = bool
  default     = false
}

# S3 gateway endpoint: private S3 connectivity without NAT.
resource "aws_vpc_endpoint" "s3" {
  count = var.enable_s3_gateway_endpoint ? 1 : 0

  vpc_id            = var.vpc_id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"

  route_table_ids = [aws_route_table.private[0].id]

  tags = merge(local.tags, {
    Name = "ai-workspace-${var.environment}-s3-endpoint"
  })
}

# Dedicated network boundary for Secrets Manager.
resource "aws_security_group" "secrets_endpoint" {
  count = (var.enable_verifier_lambda || var.enable_secretsmanager_endpoint) ? 1 : 0

  name_prefix = "ai-workspace-secrets-endpoint-"
  description = "Secrets Manager endpoint for trusted verifier"
  vpc_id      = var.vpc_id

  tags = merge(local.tags, {
    Name = "ai-workspace-secrets-endpoint-sg"
  })
}

resource "aws_vpc_security_group_ingress_rule" "secrets_from_verifier" {
  count = var.enable_verifier_lambda ? 1 : 0

  security_group_id = aws_security_group.secrets_endpoint[0].id

  referenced_security_group_id = aws_security_group.verifier[0].id

  ip_protocol = "tcp"
  from_port   = 443
  to_port     = 443

  description = "Allow verifier HTTPS to Secrets Manager"
}

resource "aws_vpc_endpoint" "secretsmanager" {
  count = (var.enable_verifier_lambda || var.enable_secretsmanager_endpoint) ? 1 : 0

  vpc_id = var.vpc_id

  service_name      = "com.amazonaws.${var.aws_region}.secretsmanager"
  vpc_endpoint_type = "Interface"

  # One AZ initially to reduce endpoint costs.
  subnet_ids = [
    values(aws_subnet.private)[0].id
  ]

  security_group_ids = [
    aws_security_group.secrets_endpoint[0].id
  ]

  private_dns_enabled = true

  tags = merge(local.tags, {
    Name = "ai-workspace-${var.environment}-secrets-endpoint"
  })

  lifecycle {
    precondition {
      condition     = var.enable_network && var.enable_rds
      error_message = "Verifier endpoints require network and RDS."
    }

    precondition {
      condition = (
        data.aws_vpc.verifier_network[0].enable_dns_support &&
        data.aws_vpc.verifier_network[0].enable_dns_hostnames
      )
      error_message = "Verifier requires VPC DNS resolution and DNS hostnames enabled."
    }
  }
}

resource "aws_vpc_security_group_ingress_rule" "secrets_from_bootstrap" {
  # Private DNS also selects this endpoint for the EC2 runtime when it was
  # created by verifier deployment rather than the standalone endpoint switch.
  count = (var.enable_secretsmanager_endpoint || var.enable_ec2_worker) && var.enable_processing_queue ? 1 : 0

  security_group_id            = aws_security_group.secrets_endpoint[0].id
  referenced_security_group_id = aws_security_group.worker[0].id

  ip_protocol = "tcp"
  from_port   = 443
  to_port     = 443

  description = "Private HTTPS access for database bootstrap"
}
