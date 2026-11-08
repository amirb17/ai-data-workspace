resource "aws_s3_bucket" "artifacts" {
  count  = var.enable_artifact_bucket ? 1 : 0
  bucket = var.artifact_bucket_name

  tags = merge(local.tags, {
    Name = "ai-workspace-${var.environment}-artifacts"
  })

  lifecycle {
    prevent_destroy = true

    precondition {
      condition     = length(var.artifact_bucket_name) >= 3
      error_message = "Supply a new unique artifact bucket name."
    }
  }
}

resource "aws_s3_bucket_versioning" "artifacts" {
  count  = var.enable_artifact_bucket ? 1 : 0
  bucket = aws_s3_bucket.artifacts[0].id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  count  = var.enable_artifact_bucket ? 1 : 0
  bucket = aws_s3_bucket.artifacts[0].id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  count  = var.enable_artifact_bucket ? 1 : 0
  bucket = aws_s3_bucket.artifacts[0].id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

output "artifact_bucket_name" {
  value = try(aws_s3_bucket.artifacts[0].bucket, null)
}