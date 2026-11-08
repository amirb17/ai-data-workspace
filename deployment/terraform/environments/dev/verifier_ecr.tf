resource "aws_ecr_repository" "verifier" {
  count = var.enable_verifier_ecr ? 1 : 0

  name                 = "ai-workspace-${var.environment}-verifier"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false

  encryption_configuration {
    encryption_type = "AES256"
  }

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = local.tags
}

resource "aws_ecr_lifecycle_policy" "verifier" {
  count      = var.enable_verifier_ecr ? 1 : 0
  repository = aws_ecr_repository.verifier[0].name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Retain latest five verifier images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 5
      }
      action = {
        type = "expire"
      }
    }]
  })
}

output "verifier_ecr_repository_url" {
  value = try(aws_ecr_repository.verifier[0].repository_url, null)
}