
variable "enable_ecr" {
  description = "Create a private ECR repository for the worker image"
  type        = bool
  default     = false
}

resource "aws_ecr_repository" "worker" {
  count = var.enable_ecr ? 1 : 0

  name                 = "ai-workspace-${var.environment}-worker"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false

  encryption_configuration {
    encryption_type = "AES256"
  }

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = merge(local.tags, {
    Name = "ai-workspace-${var.environment}-worker"
  })
}

resource "aws_ecr_lifecycle_policy" "worker" {
  count      = var.enable_ecr ? 1 : 0
  repository = aws_ecr_repository.worker[0].name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep the latest five worker images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 5
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}

output "worker_ecr_repository_url" {
  description = "Private container registry URL"
  value       = try(aws_ecr_repository.worker[0].repository_url, null)
}
