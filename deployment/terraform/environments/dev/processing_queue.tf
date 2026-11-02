resource "aws_sqs_queue" "processing_dlq" {
  count = var.enable_processing_queue ? 1 : 0

  name                      = "ai-workspace-${var.environment}-processing-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "processing" {
  count = var.enable_processing_queue ? 1 : 0

  name                       = "ai-workspace-${var.environment}-processing"
  visibility_timeout_seconds = 300
  message_retention_seconds  = 345600
  receive_wait_time_seconds  = 20
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.processing_dlq[0].arn
    maxReceiveCount     = 5
  })
}

resource "aws_sqs_queue_redrive_allow_policy" "processing_dlq" {
  count = var.enable_processing_queue ? 1 : 0

  queue_url = aws_sqs_queue.processing_dlq[0].id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.processing[0].arn]
  })
}