# Explicitly approved, default-off S3 CSV ingestion for ECS API.
# References an existing bucket; does not create or overwrite its CORS,
# block-public-access, encryption, versioning or lifecycle configuration.
variable "enable_api_s3_csv_upload" {
  type        = bool
  default     = false
  description = "Opt-in to a dedicated, narrowly scoped ECS task identity for CSV uploads"
}
variable "api_s3_csv_public_live" {
  type        = bool
  default     = false
  description = "Independent emergency-reversible public CSV upload release switch"
  validation {
    condition = !var.api_s3_csv_public_live || var.enable_api_s3_csv_upload
    error_message = "Public CSV uploads require the approved dedicated S3 task role."
  }
}
variable "api_s3_upload_bucket" {
  type        = string
  default     = ""
  description = "Reviewed existing private upload bucket name"
  validation {
    condition     = var.api_s3_upload_bucket == "" || can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.api_s3_upload_bucket))
    error_message = "Provide an exact DNS-compatible S3 bucket name."
  }
}
variable "api_s3_upload_region" {
  type        = string
  default     = ""
  description = "Actual region of the existing S3 bucket, must match the ECS region"
}
resource "aws_iam_role" "api_s3_upload_task" {
  count              = var.enable_api_s3_csv_upload ? 1 : 0
  name               = "ai-workspace-${var.environment}-api-s3-task"
  assume_role_policy = data.aws_iam_policy_document.api_ecs_task_assume.json
  tags               = merge(local.tags, { Name = "ai-workspace-${var.environment}-api-s3-task" })
  lifecycle {
    precondition {
      condition = (var.enable_api_task_definition &&
        var.api_s3_upload_bucket != "" &&
        var.api_s3_upload_region == var.aws_region &&
        var.api_frontend_origin != "")
      error_message = "Enable the task only for an approved existing bucket in the ECS region."
    }
    prevent_destroy = true
  }
}

data "aws_iam_policy_document" "api_s3_upload" {
  count = var.enable_api_s3_csv_upload ? 1 : 0
  statement {
    sid       = "CreatePrivateStagingCsvAndRawObjects"
    actions   = ["s3:PutObject"]
    resources = [
      "arn:aws:s3:::${var.api_s3_upload_bucket}/staging/user-*/*",
      "arn:aws:s3:::${var.api_s3_upload_bucket}/raw/*"
    ]
  }
  statement {
    sid       = "ValidateStagingAndDeduplicateRaw"
    actions   = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = [
      "arn:aws:s3:::${var.api_s3_upload_bucket}/staging/user-*/*",
      "arn:aws:s3:::${var.api_s3_upload_bucket}/raw/*"
    ]
  }
  statement {
    sid       = "RemoveCompletedStagingObjects"
    actions   = ["s3:DeleteObject"]
    resources = ["arn:aws:s3:::${var.api_s3_upload_bucket}/staging/user-*/*"]
  }
}
resource "aws_iam_role_policy" "api_s3_upload" {
  count  = var.enable_api_s3_csv_upload ? 1 : 0
  name   = "ai-workspace-${var.environment}-api-csv-staging"
  role   = aws_iam_role.api_s3_upload_task[0].id
  policy = data.aws_iam_policy_document.api_s3_upload[0].json
}
output "api_s3_upload_task_role_arn" {
  value = try(aws_iam_role.api_s3_upload_task[0].arn, null)
}
