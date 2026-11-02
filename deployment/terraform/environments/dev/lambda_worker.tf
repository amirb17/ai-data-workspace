
data "aws_iam_policy_document" "lambda_trust" {
  count = var.enable_lambda_worker ? 1 : 0

  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda_worker" {
  count = var.enable_lambda_worker ? 1 : 0

  name               = "ai-workspace-${var.environment}-lambda-worker"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust[0].json
}

resource "aws_iam_role_policy_attachment" "lambda_logs" {
  count = var.enable_lambda_worker ? 1 : 0

  role       = aws_iam_role.lambda_worker[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "lambda_queue" {
  count = var.enable_lambda_worker ? 1 : 0

  statement {
    effect = "Allow"

    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:ChangeMessageVisibility",
      "sqs:GetQueueAttributes"
    ]

    resources = [aws_sqs_queue.processing[0].arn]
  }
}

resource "aws_iam_role_policy" "lambda_queue" {
  count = var.enable_lambda_worker ? 1 : 0

  name   = "processing-queue-consumer"
  role   = aws_iam_role.lambda_worker[0].id
  policy = data.aws_iam_policy_document.lambda_queue[0].json
}

resource "aws_lambda_function" "processing_worker" {
  count = var.enable_lambda_worker ? 1 : 0

  function_name = "ai-workspace-${var.environment}-processing-worker"
  role          = aws_iam_role.lambda_worker[0].arn
  handler       = "app.lambda_worker.handler.lambda_handler"
  runtime       = "python3.13"

  filename         = var.lambda_package_path
  source_code_hash = var.enable_lambda_worker ? filebase64sha256(var.lambda_package_path) : null

  timeout     = 30
  memory_size = 256

  lifecycle {
    precondition {
      condition     = var.enable_processing_queue
      error_message = "Lambda worker requires the processing queue to be enabled."
    }

    precondition {
      condition     = !var.enable_lambda_worker || var.lambda_package_path != ""
      error_message = "Provide a packaged Lambda ZIP before enabling Lambda."
    }
  }
}

resource "aws_lambda_event_source_mapping" "processing" {
  count = var.enable_lambda_worker && var.enable_lambda_trigger ? 1 : 0

  event_source_arn = aws_sqs_queue.processing[0].arn
  function_name    = aws_lambda_function.processing_worker[0].arn

  batch_size              = 1
  function_response_types = ["ReportBatchItemFailures"]
  enabled                 = true

  depends_on = [aws_iam_role_policy.lambda_queue]
}
