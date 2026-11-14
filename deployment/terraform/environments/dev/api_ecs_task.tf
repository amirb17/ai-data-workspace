# API task registration ONLY. A registered task definition does not run a task.
# Production network/TLS ingress and secrets must be approved separately.
variable "enable_api_task_definition" {
  description = "Register FastAPI Fargate task after reviewing secret ARNs and HTTPS origin"
  type        = bool
  default     = false
}

variable "api_image_digest" {
  description = "Immutable ECR image sha256 digest (not a tag)"
  type        = string
  default     = ""
  validation {
    condition     = var.api_image_digest == "" || can(regex("^sha256:[0-9a-f]{64}$", var.api_image_digest))
    error_message = "API image digest must be a lowercase sha256 digest."
  }
}

variable "api_frontend_origin" {
  description = "Exact HTTPS Amplify frontend origin, without trailing slash"
  type        = string
  default     = "https://feature-warehouse-model-ux.d2bhduxm4lvbct.amplifyapp.com"
}

variable "api_backend_origin" {
  description = "Reviewed public HTTPS API origin, no path (required for task registration)"
  type        = string
  default     = ""
  validation {
    condition = var.api_backend_origin == "" || (
      can(regex("^https://[a-z0-9.-]+$", var.api_backend_origin))
    )
    error_message = "Backend origin must be a reviewed HTTPS hostname without a path."
  }
}

variable "api_public_host" {
  description = "Exact hostname of backend, without a port"
  type        = string
  default     = ""
  validation {
    condition     = var.api_public_host == "" || can(regex("^[a-z0-9][a-z0-9.-]*[a-z0-9]$", var.api_public_host))
    error_message = "API host must be an exact lowercase DNS hostname."
  }
}

variable "api_auth_secret_arn" {
  description = "Secrets Manager ARN of API session signing secret (never plaintext)"
  type        = string
  default     = ""
}

variable "api_database_secret_arn" {
  description = "Secrets Manager ARN for API-only PostgreSQL JSON secret (username/password/host/port/dbname)"
  type        = string
  default     = ""
}

variable "api_enable_public_workspace_creation" {
  description = "Separate reviewed opt-in for authenticated workspace/dataset creation; does not enable uploads, processing, AI or warehouse"
  type        = bool
  default     = false
}

variable "api_trust_cloudfront_vpc_origin" {
  description = "Explicitly enable HTTPS viewer scheme only behind CloudFront VPC origin and private ALB SG isolation"
  type        = bool
  default     = false
}

variable "api_enable_analytics_read" {
  description = "Default-off, authenticated dataset analytics dashboard read"
  type        = bool
  default     = false
}

variable "api_enable_analytics_refresh" {
  description = "Default-off state-pinned cumulative Gold refresh; requires separate reviewed DB and S3 grants"
  type        = bool
  default     = false
  validation {
    condition = !var.api_enable_analytics_refresh || var.api_enable_analytics_read
    error_message = "Analytics refresh requires analytics read to be enabled."
  }
}

variable "api_enable_sql_catalog" {
  description = "Default-off authenticated read-only SQL catalog; not SQL execution"
  type        = bool
  default     = false
  validation {
    condition = !var.api_enable_sql_catalog || var.api_enable_analytics_read
    error_message = "SQL catalog requires analytics read to be enabled."
  }
}

variable "api_enable_initial_append" {
  description = "Separate reviewed, default-off first-APPEND policy admission; never enables SQL or warehouse access"
  type        = bool
  default     = false
}

variable "api_enable_processing_jobs" {
  description = "Explicit reviewed opt-in for authenticated asynchronous processing admission; never enables dispatcher authority on the API"
  type        = bool
  default     = false
}

variable "api_task_cpu" {
  description = "Fargate CPU units; 512 is an initial allocation pending memory profiling"
  type        = number
  default     = 512
}

variable "api_task_memory" {
  description = "Fargate MiB; 1024 initially; no task runs until the service is separately enabled"
  type        = number
  default     = 1024
}

resource "aws_ecs_task_definition" "api" {
  count                    = var.enable_api_task_definition ? 1 : 0
  family                   = "ai-workspace-${var.environment}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.api_task_cpu)
  memory                   = tostring(var.api_task_memory)
  # Preserve prior ECS revisions when registering a replacement for rollback.
  skip_destroy             = true
  execution_role_arn       = aws_iam_role.api_ecs_execution[0].arn
  task_role_arn            = var.enable_api_s3_csv_upload ? aws_iam_role.api_s3_upload_task[0].arn : null
  depends_on               = [aws_iam_role_policy.api_s3_upload]

  container_definitions = jsonencode([{
    name      = "api"
    image     = "${aws_ecr_repository.api[0].repository_url}@${var.api_image_digest}"
    essential = true
    portMappings = [{
      containerPort = 8000
      protocol      = "tcp"
    }]
    readonlyRootFilesystem = true
    environment = concat([
      { name = "APP_ENV", value = "production" },
      { name = "AUTH_MODE", value = "session" },
      { name = "AUTH_COOKIE_SECURE", value = "true" },
      { name = "AUTH_FRONTEND_URL", value = var.api_frontend_origin },
      { name = "AUTH_BACKEND_URL", value = var.api_backend_origin },
      { name = "DATARISE_PUBLIC_HOSTS", value = var.api_public_host },
      { name = "DATARISE_TRUST_CLOUDFRONT_VPC_ORIGIN", value = tostring(var.api_trust_cloudfront_vpc_origin) },
      { name = "DATARISE_PUBLIC_LAUNCH_MODE", value = var.api_enable_processing_jobs ? "csv-processing" : "csv-readonly" },
      { name = "DATARISE_PUBLIC_INITIAL_APPEND", value = tostring(var.api_enable_initial_append) },
      { name = "DATARISE_PUBLIC_ANALYTICS_READ", value = tostring(var.api_enable_analytics_read) },
      { name = "DATARISE_PUBLIC_ANALYTICS_REFRESH", value = tostring(var.api_enable_analytics_refresh) },
      { name = "DATARISE_PUBLIC_SQL_CATALOG", value = tostring(var.api_enable_sql_catalog) },
      { name = "DATARISE_PROCESSING_DEPLOYMENT_APPROVED", value = tostring(var.api_enable_processing_jobs) },
      { name = "DATARISE_PROCESSING_READERS", value = tostring(var.api_enable_processing_jobs) },
      { name = "DATARISE_DISPATCH_APPROVED", value = "false" },
      { name = "DATARISE_PUBLIC_WORKSPACE_CREATION", value = tostring(var.api_enable_public_workspace_creation) },
      { name = "DATARISE_PUBLIC_CSV_UPLOAD", value = tostring(var.api_s3_csv_public_live) },
      { name = "ENABLE_DEV_IDENTITY", value = "false" },
      { name = "WAREHOUSE_ENABLED", value = "false" }
    ], var.enable_api_s3_csv_upload ? [
      { name = "AWS_REGION", value = var.api_s3_upload_region },
      { name = "S3_BUCKET_NAME", value = var.api_s3_upload_bucket }
    ] : [], var.enable_api_smtp_delivery ? [
      { name = "AUTH_EMAIL_BACKEND", value = "smtp" },
      { name = "SMTP_HOST", value = var.api_smtp_host },
      { name = "SMTP_PORT", value = tostring(var.api_smtp_port) },
      { name = "SMTP_SSL", value = "false" },
      { name = "SMTP_FROM", value = var.api_smtp_sender }
    ] : [])
    secrets = concat([
      { name = "AUTH_SECRET", valueFrom = var.api_auth_secret_arn },
      { name = "DATARISE_DB_USERNAME", valueFrom = "${var.api_database_secret_arn}:username::" },
      { name = "DATARISE_DB_PASSWORD", valueFrom = "${var.api_database_secret_arn}:password::" },
      { name = "DATARISE_DB_HOST", valueFrom = "${var.api_database_secret_arn}:host::" },
      { name = "DATARISE_DB_PORT", valueFrom = "${var.api_database_secret_arn}:port::" },
      { name = "DATARISE_DB_NAME", valueFrom = "${var.api_database_secret_arn}:dbname::" }
    ], var.enable_api_smtp_delivery ? [
      { name = "SMTP_USERNAME", valueFrom = "${var.api_smtp_secret_arn}:username::" },
      { name = "SMTP_PASSWORD", valueFrom = "${var.api_smtp_secret_arn}:password::" }
    ] : [])
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.api[0].name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "api"
      }
    }
  }])

  tags = merge(local.tags, { Name = "ai-workspace-${var.environment}-api" })

  lifecycle {
    precondition {
      condition = (var.enable_api_ecs_execution_role && var.enable_api_ecs_foundation &&
        var.enable_api_ecr && can(regex("^sha256:[0-9a-f]{64}$", var.api_image_digest)) && var.api_backend_origin != "" &&
        var.api_public_host != "" &&
        (!var.api_enable_processing_jobs || (var.api_s3_csv_public_live && var.api_enable_public_workspace_creation)) &&
        (!var.api_enable_initial_append || var.api_enable_processing_jobs) &&
        var.api_backend_origin == "https://${var.api_public_host}" &&
        startswith(var.api_auth_secret_arn, "arn:aws:secretsmanager:${var.aws_region}:") &&
        startswith(var.api_database_secret_arn, "arn:aws:secretsmanager:${var.aws_region}:"))
      error_message = "Task needs reviewed ECR/foundation/role, matching HTTPS host and Sydney Secrets Manager ARNs."
    }
    # ECS task definitions are immutable. skip_destroy retains old revisions
    # while Terraform replaces the tracked revision with a new registration.
  }
}

# Fargate agent must fetch ONLY these two approved API secrets.
data "aws_iam_policy_document" "api_execution_secrets" {
  count = var.enable_api_task_definition ? 1 : 0
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = concat([var.api_auth_secret_arn, var.api_database_secret_arn], var.enable_api_smtp_delivery ? [var.api_smtp_secret_arn] : [])
  }
}

resource "aws_iam_role_policy" "api_execution_secrets" {
  count  = var.enable_api_task_definition ? 1 : 0
  name   = "ai-workspace-${var.environment}-api-bootstrap-secrets"
  role   = aws_iam_role.api_ecs_execution[0].id
  policy = data.aws_iam_policy_document.api_execution_secrets[0].json
}

output "api_task_definition_arn" {
  value       = try(aws_ecs_task_definition.api[0].arn, null)
  description = "Registered task definition; no running service is created"
}
