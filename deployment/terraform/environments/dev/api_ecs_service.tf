# Explicitly opt-in to ECS API service registration; zero tasks by default.
# ECS tasks use existing PUBLIC subnets for ECR/logs egress, no NAT gateway,
# and the dedicated API SG permits inbound 8000 from private ALB SG ONLY.
# Public IP does not mean an ingress rule; verify SG before launch.
variable "enable_api_ecs_service" {
  description = "Opt in to creating the API ECS Fargate service"
  type        = bool
  default     = false
}

variable "api_ecs_public_subnet_ids" {
  description = "Existing VPC public subnet IDs in two different availability zones"
  type        = list(string)
  default     = []
  validation {
    condition = length(var.api_ecs_public_subnet_ids) == 0 || (
      length(var.api_ecs_public_subnet_ids) >= 2 &&
      length(distinct(var.api_ecs_public_subnet_ids)) == length(var.api_ecs_public_subnet_ids) &&
      alltrue([for id in var.api_ecs_public_subnet_ids : can(regex("^subnet-[0-9a-f]+$", id))])
    )
    error_message = "Specify at least two unique existing public subnet IDs."
  }
}

variable "api_ecs_desired_count" {
  description = "Launch 0 tasks until HTTPS ingress and secret permissions are verified; 1 for first live rollout"
  type        = number
  default     = 0
  validation {
    condition     = contains([0, 1], var.api_ecs_desired_count)
    error_message = "Initial API rollout supports only zero or one task."
  }
}

resource "aws_ecs_service" "api" {
  count           = var.enable_api_ecs_service ? 1 : 0
  name            = "ai-workspace-${var.environment}-api"
  cluster         = aws_ecs_cluster.api[0].id
  task_definition = aws_ecs_task_definition.api[0].arn
  launch_type     = "FARGATE"
  desired_count   = var.api_ecs_desired_count

  health_check_grace_period_seconds = 90
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = var.api_ecs_public_subnet_ids
    security_groups  = [aws_security_group.api_task[0].id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api[0].arn
    container_name   = "api"
    container_port   = 8000
  }

  depends_on = [
    aws_lb_listener.api_http,
    aws_iam_role_policy.api_execution_secrets
  ]

  tags = merge(local.tags, { Name = "ai-workspace-${var.environment}-api-fargate" })

  lifecycle {
    precondition {
      condition = (
        var.enable_api_task_definition &&
        var.enable_api_ecs_foundation &&
        var.enable_api_internal_alb &&
        var.enable_api_task_security_group &&
        length(var.api_ecs_public_subnet_ids) >= 2
      )
      error_message = "ECS service requires an approved task, ALB, task SG and two public subnets."
    }
    prevent_destroy = true
  }
}

output "api_ecs_service_name" {
  value = try(aws_ecs_service.api[0].name, null)
}
