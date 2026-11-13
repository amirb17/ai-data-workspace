# Security-group foundation for a future INTERNAL ALB / CloudFront VPC origin.
# No ALB, listener, target group, CloudFront distribution, or service is created.
# ALB remains without inbound access until the CloudFront VPC origin is available.
variable "enable_api_internal_alb_security" {
  type        = bool
  default     = false
  description = "Opt in to a zero-ingress internal ALB security group and ALB-to-API rule"
}

resource "aws_security_group" "api_internal_alb" {
  count       = var.enable_api_internal_alb_security ? 1 : 0
  name_prefix = "ai-workspace-${var.environment}-api-alb-"
  description = "Internal API ALB; no public inbound permissions"
  vpc_id      = var.api_vpc_id
  tags        = merge(local.tags, { Name = "ai-workspace-${var.environment}-api-internal-alb" })

  lifecycle {
    precondition {
      condition     = var.enable_api_task_security_group && can(regex("^vpc-[0-9a-f]+$", var.api_vpc_id))
      error_message = "API task SG and reviewed existing VPC are required."
    }
    prevent_destroy = true
  }
}

resource "aws_vpc_security_group_egress_rule" "api_alb_to_task" {
  count                        = var.enable_api_internal_alb_security ? 1 : 0
  security_group_id            = aws_security_group.api_internal_alb[0].id
  referenced_security_group_id = aws_security_group.api_task[0].id
  description                  = "Internal ALB forwards exclusively to FastAPI TCP 8000"
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
}

resource "aws_vpc_security_group_ingress_rule" "api_from_alb" {
  count                        = var.enable_api_internal_alb_security ? 1 : 0
  security_group_id            = aws_security_group.api_task[0].id
  referenced_security_group_id = aws_security_group.api_internal_alb[0].id
  description                  = "FastAPI TCP 8000 from internal ALB only"
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
  lifecycle {
    prevent_destroy = true
  }
}

output "api_internal_alb_security_group_id" {
  value       = try(aws_security_group.api_internal_alb[0].id, null)
  description = "Zero-ingress ALB SG; future CloudFront origin access requires explicit review"
}
