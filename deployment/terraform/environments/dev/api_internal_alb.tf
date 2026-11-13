# Existing-VPC, internal-only ALB for CloudFront VPC origin (not internet-facing).
# OFF by default: creating an ALB incurs ongoing hourly/LCU costs.
# No ECS service or CloudFront distribution is provisioned here.
variable "enable_api_internal_alb" {
  description = "Opt in to billable internal ALB, HTTP listener and IP target group"
  type        = bool
  default     = false
}

variable "api_alb_private_subnet_ids" {
  description = "Two existing private subnets in distinct AZs; do not create subnets"
  type        = list(string)
  default     = []
  validation {
    condition = length(var.api_alb_private_subnet_ids) == 0 || (
      length(var.api_alb_private_subnet_ids) >= 2 &&
      length(distinct(var.api_alb_private_subnet_ids)) == length(var.api_alb_private_subnet_ids) &&
      alltrue([for id in var.api_alb_private_subnet_ids : can(regex("^subnet-[0-9a-f]+$", id))])
    )
    error_message = "Supply at least two distinct existing subnet IDs."
  }
}

resource "aws_lb" "api_internal" {
  count                      = var.enable_api_internal_alb ? 1 : 0
  name                       = "ai-workspace-${var.environment}-api"
  internal                   = true
  load_balancer_type         = "application"
  security_groups            = [aws_security_group.api_internal_alb[0].id]
  subnets                    = var.api_alb_private_subnet_ids
  enable_deletion_protection = true
  drop_invalid_header_fields = true

  tags = merge(local.tags, { Name = "ai-workspace-${var.environment}-api-internal" })

  lifecycle {
    precondition {
      condition = (
        var.enable_api_internal_alb_security &&
        length(var.api_alb_private_subnet_ids) >= 2 &&
        var.api_vpc_id != ""
      )
      error_message = "Review the internal ALB SG, VPC, and two private subnets first."
    }
    prevent_destroy = true
  }
}

resource "aws_lb_target_group" "api" {
  count       = var.enable_api_internal_alb ? 1 : 0
  name        = "ai-workspace-${var.environment}-api"
  port        = 8000
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = var.api_vpc_id

  health_check {
    enabled             = true
    path                = "/health"
    port                = "traffic-port"
    protocol            = "HTTP"
    matcher             = "200"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 30
    timeout             = 5
  }

  tags = merge(local.tags, { Name = "ai-workspace-${var.environment}-api-tg" })

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_lb_listener" "api_http" {
  count             = var.enable_api_internal_alb ? 1 : 0
  load_balancer_arn = aws_lb.api_internal[0].arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api[0].arn
  }

  lifecycle {
    prevent_destroy = true
  }
}

output "api_internal_alb_dns_name" {
  description = "Private ALB DNS (not a public URL)"
  value       = try(aws_lb.api_internal[0].dns_name, null)
}

output "api_target_group_arn" {
  value = try(aws_lb_target_group.api[0].arn, null)
}
