# Dedicated FastAPI web image registry; isolated from worker/verifier images.
# Off by default. Apply only after AWS account, billing and plan review.
variable "enable_api_ecr" {
  description = "Create a dedicated immutable ECR repository for FastAPI"
  type        = bool
  default     = false
}

resource "aws_ecr_repository" "api" {
  count                = var.enable_api_ecr ? 1 : 0
  name                 = "ai-workspace-${var.environment}-api"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false

  encryption_configuration {
    encryption_type = "AES256"
  }

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = merge(local.tags, { Name = "ai-workspace-${var.environment}-api" })

  lifecycle {
    prevent_destroy = true
  }
}

output "api_ecr_repository_url" {
  description = "Dedicated private FastAPI image repository"
  value       = try(aws_ecr_repository.api[0].repository_url, null)
}
