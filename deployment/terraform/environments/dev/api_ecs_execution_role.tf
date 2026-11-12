# Dedicated ECS Fargate execution identity for the FastAPI web container.
# Creates no IAM resources unless explicitly enabled. Separate from application
# task identity and EC2 worker identity; never carries application DB credentials.
variable "enable_api_ecs_execution_role" {
  description = "Opt in to the FastAPI Fargate execution role after permissions and cost review"
  type        = bool
  default     = false
}

data "aws_iam_policy_document" "api_ecs_task_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "api_ecs_execution" {
  count              = var.enable_api_ecs_execution_role ? 1 : 0
  name               = "ai-workspace-${var.environment}-api-ecs-execution"
  assume_role_policy = data.aws_iam_policy_document.api_ecs_task_assume.json
  tags               = merge(local.tags, { Name = "ai-workspace-${var.environment}-api-ecs-execution" })
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_iam_role_policy_attachment" "api_ecs_execution" {
  count      = var.enable_api_ecs_execution_role ? 1 : 0
  role       = aws_iam_role.api_ecs_execution[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

output "api_ecs_execution_role_arn" {
  description = "Fargate agent identity for ECR pulls and awslogs, not application AWS access"
  value       = try(aws_iam_role.api_ecs_execution[0].arn, null)
}
