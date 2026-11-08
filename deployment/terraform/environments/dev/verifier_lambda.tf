
resource "aws_security_group" "verifier" {
  count       = var.enable_verifier_lambda ? 1 : 0
  name_prefix = "ai-workspace-verifier-"
  description = "Isolated verifier Lambda"
  vpc_id      = var.vpc_id

  tags = merge(local.tags, {
    Name = "ai-workspace-verifier-sg"
  })
}

resource "aws_vpc_security_group_ingress_rule" "verifier_to_postgres" {
  count = var.enable_verifier_lambda ? 1 : 0

  security_group_id            = aws_security_group.database[0].id
  referenced_security_group_id = aws_security_group.verifier[0].id

  ip_protocol = "tcp"
  from_port   = 5432
  to_port     = 5432

  description = "PostgreSQL access from verifier Lambda"
}

resource "aws_vpc_security_group_egress_rule" "verifier_postgres" {
  count = var.enable_verifier_lambda ? 1 : 0

  security_group_id            = aws_security_group.verifier[0].id
  referenced_security_group_id = aws_security_group.database[0].id

  ip_protocol = "tcp"
  from_port   = 5432
  to_port     = 5432
}

resource "aws_vpc_security_group_egress_rule" "verifier_https" {
  count = var.enable_verifier_lambda ? 1 : 0

  security_group_id = aws_security_group.verifier[0].id

  ip_protocol = "tcp"
  from_port   = 443
  to_port     = 443
  cidr_ipv4   = "0.0.0.0/0"

  description = "HTTPS to approved endpoints when routing is configured"
}

data "aws_iam_policy_document" "verifier_trust" {
  count = var.enable_verifier_lambda ? 1 : 0

  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "verifier" {
  count = var.enable_verifier_lambda ? 1 : 0

  name               = "ai-workspace-${var.environment}-trusted-verifier"
  assume_role_policy = data.aws_iam_policy_document.verifier_trust[0].json
}

resource "aws_iam_role_policy_attachment" "verifier_vpc" {
  count = var.enable_verifier_lambda ? 1 : 0

  role       = aws_iam_role.verifier[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

resource "aws_cloudwatch_log_group" "verifier" {
  count = var.enable_verifier_lambda ? 1 : 0

  name              = "/aws/lambda/ai-workspace-${var.environment}-trusted-verifier"
  retention_in_days = 7

  tags = local.tags
}

data "aws_iam_policy_document" "verifier_access" {
  count = var.enable_verifier_lambda ? 1 : 0

  statement {
    sid       = "ReadVerifierDatabaseSecret"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [var.verifier_db_secret_arn]
  }

  statement {
    sid = "ReadPinnedArtifacts"
    actions = [
      "s3:GetObject",
      "s3:GetObjectVersion"
    ]
    resources = [
      "arn:aws:s3:::${var.artifact_bucket_name}/raw/*",
      "arn:aws:s3:::${var.artifact_bucket_name}/bronze/*",
      "arn:aws:s3:::${var.artifact_bucket_name}/silver/*",
      "arn:aws:s3:::${var.artifact_bucket_name}/gold/*",
      "arn:aws:s3:::${var.artifact_bucket_name}/artifact-candidates/*"
    ]
  }

  statement {
    sid = "WriteVerifierLogs"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents"
    ]
    resources = ["${aws_cloudwatch_log_group.verifier[0].arn}:*"]
  }
}

resource "aws_iam_role_policy" "verifier_access" {
  count = var.enable_verifier_lambda ? 1 : 0

  name   = "trusted-verifier-runtime"
  role   = aws_iam_role.verifier[0].id
  policy = data.aws_iam_policy_document.verifier_access[0].json
}

resource "aws_lambda_function" "trusted_verifier" {
  count = var.enable_verifier_lambda ? 1 : 0

  function_name = "ai-workspace-${var.environment}-trusted-verifier"
  role          = aws_iam_role.verifier[0].arn

  package_type = "Image"
  image_uri    = var.verifier_image_uri

  timeout     = 300
  memory_size = 1024

  vpc_config {
    subnet_ids = [
      for subnet in aws_subnet.private : subnet.id
    ]
    security_group_ids = [aws_security_group.verifier[0].id]
  }

  environment {
    variables = {
      DATARISE_PROCESSING_DEPLOYMENT_APPROVED = "false"
      DATARISE_PROCESSING_READERS             = "false"
      DATARISE_ARTIFACT_BUCKET                = var.artifact_bucket_name
      DATARISE_VERIFIER_DB_SECRET_ARN         = var.verifier_db_secret_arn
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.verifier_vpc,
    aws_iam_role_policy.verifier_access,
    aws_cloudwatch_log_group.verifier,
    aws_vpc_endpoint.s3,
    aws_vpc_endpoint.secretsmanager
  ]

  lifecycle {
    precondition {
      condition = (
        var.enable_network &&
        var.enable_rds &&
        var.artifact_bucket_name != "" &&
        var.verifier_db_secret_arn != "" &&
        var.verifier_db_secret_arn != var.worker_db_secret_arn &&
        can(regex(
          "@sha256:[0-9a-f]{64}$",
          var.verifier_image_uri
        ))
      )

      error_message = "Verifier requires network, RDS, bucket, secret and digest-pinned image."
    }
  }
}

output "trusted_verifier_lambda_arn" {
  value = try(aws_lambda_function.trusted_verifier[0].arn, null)
}
