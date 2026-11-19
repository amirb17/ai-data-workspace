# Optional private endpoints for the already inspected DataRise VPC.
# Separate module: NO changes to existing API, RDS, route tables or S3/Secrets endpoints.
terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0, < 7.0"
    }
  }
}

locals {
  services = toset(["ecr.api", "ecr.dkr", "logs"])
}

resource "aws_security_group" "endpoints" {
  name_prefix = "datarise-warehouse-endpoints-"
  vpc_id      = var.vpc_id
  description = "Warehouse private ECR and Logs interface endpoints"
  tags        = { Project = "datarise", Component = "warehouse-endpoints" }
}

resource "aws_vpc_security_group_ingress_rule" "worker_https" {
  security_group_id            = aws_security_group.endpoints.id
  referenced_security_group_id = var.worker_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  description                  = "TLS from independently scoped warehouse worker tasks"
}

resource "aws_vpc_endpoint" "worker" {
  for_each            = local.services
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.${var.region}.${each.key}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = var.private_subnet_ids
  security_group_ids  = [aws_security_group.endpoints.id]
  private_dns_enabled = true
  tags = {
    Project     = "datarise"
    Environment = "dev"
    Component   = "warehouse-workers"
    Name        = "datarise-warehouse-${replace(each.key, ".", "-")}"
  }
}

output "security_group_id" {
  value = aws_security_group.endpoints.id
}

output "endpoint_ids" {
  value = { for service, endpoint in aws_vpc_endpoint.worker : service => endpoint.id }
}
