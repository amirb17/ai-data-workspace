# API task security group only. No inbound rules: neither task public IP
# nor direct internet traffic may enter port 8000.
# Load balancer SG ingress, DB access, and tasks are separate reviewed steps.
variable "enable_api_task_security_group" {
  description = "Opt in to API task security group; no inbound rules are created"
  type        = bool
  default     = false
}

variable "api_vpc_id" {
  description = "Existing reviewed VPC for FastAPI; does not create a VPC"
  type        = string
  default     = ""
}

resource "aws_security_group" "api_task" {
  count       = var.enable_api_task_security_group ? 1 : 0
  name_prefix = "ai-workspace-${var.environment}-api-task-"
  description = "FastAPI ECS task; ingress denied until explicitly reviewed"
  vpc_id      = var.api_vpc_id

  # No inline ingress or egress blocks. Terraform AWS provider 6 will
  # create a group without inbound permissions. Egress is created below.
  tags = merge(local.tags, { Name = "ai-workspace-${var.environment}-api-task" })

  lifecycle {
    precondition {
      condition     = can(regex("^vpc-[0-9a-f]+$", var.api_vpc_id))
      error_message = "Specify an existing reviewed VPC ID."
    }
    prevent_destroy = true
  }
}

# AWS API/bootstrap outbound TLS; PostgreSQL TCP/5432 stays VPC-internal
# and will require a separate, narrowly scoped database ingress rule.
resource "aws_vpc_security_group_egress_rule" "api_https" {
  count             = var.enable_api_task_security_group ? 1 : 0
  security_group_id = aws_security_group.api_task[0].id
  description       = "Fargate bootstrap and external HTTPS APIs"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "api_database" {
  count             = var.enable_api_task_security_group ? 1 : 0
  security_group_id = aws_security_group.api_task[0].id
  description       = "PostgreSQL within existing VPC only"
  ip_protocol       = "tcp"
  from_port         = 5432
  to_port           = 5432
  cidr_ipv4         = "172.31.0.0/16"
}

output "api_task_security_group_id" {
  value       = try(aws_security_group.api_task[0].id, null)
  description = "No inbound rules; ALB-only ingress must be separately reviewed"
}
