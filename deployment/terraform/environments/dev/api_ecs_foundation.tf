# FastAPI-only ECS foundation. This creates no running Fargate tasks,
# load balancers, network endpoints, or externally accessible services.
variable "enable_api_ecs_foundation" {
  description = "Opt in to an empty ECS cluster and dedicated API log group"
  type        = bool
  default     = false
}

resource "aws_ecs_cluster" "api" {
  count = var.enable_api_ecs_foundation ? 1 : 0
  name  = "ai-workspace-${var.environment}-api"
  tags  = merge(local.tags, { Name = "ai-workspace-${var.environment}-api" })
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_cloudwatch_log_group" "api" {
  count             = var.enable_api_ecs_foundation ? 1 : 0
  name              = "/ecs/ai-workspace-${var.environment}-api"
  retention_in_days = 7
  tags              = merge(local.tags, { Name = "/ecs/ai-workspace-${var.environment}-api" })
  lifecycle {
    prevent_destroy = true
  }
}

output "api_ecs_cluster_name" {
  description = "Name of the API-only ECS cluster"
  value       = try(aws_ecs_cluster.api[0].name, null)
}

output "api_ecs_log_group_name" {
  description = "CloudWatch log group for the API task"
  value       = try(aws_cloudwatch_log_group.api[0].name, null)
}
