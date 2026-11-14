# Separately approved, dataset-1-only S3 data plane for the first cumulative
# Analytics refresh. No wildcard dataset grants, delete, list, or public access.
# These resources do not enable any HTTP route by themselves.
variable "api_enable_analytics_artifacts" {
  type        = bool
  default     = false
  description = "Explicit S3 IAM approval for the first dataset cumulative Analytics build"
}

resource "aws_iam_role_policy" "api_analytics_artifacts" {
  count = var.api_enable_analytics_artifacts ? 1 : 0
  name  = "ai-workspace-${var.environment}-api-analytics-dataset-1"
  role  = aws_iam_role.api_s3_upload_task[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ReadAcceptedDatasetOneState"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:GetObjectVersion"]
        Resource = [
          "arn:aws:s3:::${var.api_s3_upload_bucket}/silver/dataset_id=1/dataset_version_id=1/*"
        ]
      },
      {
        Sid    = "CreateAndValidateCumulativeGoldDatasetOne"
        Effect = "Allow"
        Action = ["s3:PutObject", "s3:GetObject", "s3:GetObjectVersion"]
        Resource = [
          "arn:aws:s3:::${var.api_s3_upload_bucket}/gold/dataset_id=1/*"
        ]
      }
    ]
  })

  lifecycle {
    precondition {
      condition = (var.enable_api_s3_csv_upload &&
        var.api_s3_upload_bucket != "" &&
        var.api_s3_upload_region == var.aws_region &&
        var.api_enable_analytics_read)
      error_message = "Dataset analytics IAM requires approved API bucket, region, and analytics read gate."
    }
    prevent_destroy = true
  }
}
