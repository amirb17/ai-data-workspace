data "aws_iam_policy_document" "worker_trust" {
  count = var.enable_processing_queue ? 1 : 0

  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "worker" {
  count = var.enable_processing_queue ? 1 : 0

  name = "ai-workspace-${var.environment}-worker"

  assume_role_policy = data.aws_iam_policy_document.worker_trust[0].json
}

data "aws_iam_policy_document" "worker_queue" {
  count = var.enable_processing_queue ? 1 : 0

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

resource "aws_iam_role_policy" "worker_queue" {
  count = var.enable_processing_queue ? 1 : 0

  name   = "processing-queue-consumer"
  role   = aws_iam_role.worker[0].id
  policy = data.aws_iam_policy_document.worker_queue[0].json
}