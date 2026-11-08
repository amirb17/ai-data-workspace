
data "aws_caller_identity" "current" {}

resource "aws_iam_instance_profile" "worker" {
  count = var.enable_ec2_worker ? 1 : 0

  name = "ai-workspace-${var.environment}-worker-profile"
  role = aws_iam_role.worker[0].name

  lifecycle {
    precondition {
      condition = (
        var.enable_processing_queue &&
        var.enable_ecr &&
        var.enable_network &&
        var.enable_rds &&
        var.artifact_bucket_name != "" &&
        var.worker_db_secret_arn != "" &&
        var.enable_verifier_lambda
      )
      error_message = "Worker requires queue, ECR, network, RDS, S3 bucket, worker DB secret and verifier Lambda."
    }
  }
}

data "aws_iam_policy_document" "worker_runtime" {
  count = var.enable_ec2_worker ? 1 : 0

  statement {
    sid       = "ECRAuthorization"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "PullWorkerImage"
    effect = "Allow"

    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer"
    ]

    resources = [aws_ecr_repository.worker[0].arn]
  }

  statement {
    sid    = "ReadVersionedArtifacts"
    effect = "Allow"

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
    sid    = "WriteCandidateArtifacts"
    effect = "Allow"

    actions   = ["s3:PutObject"]
    resources = ["arn:aws:s3:::${var.artifact_bucket_name}/artifact-candidates/*"]
    condition {
      test     = "StringEquals"
      variable = "s3:if-none-match"
      values   = ["*"]
    }
  }

  statement {
    sid     = "WriteImmutableIncrementalArtifacts"
    effect  = "Allow"
    actions = ["s3:PutObject"]
    resources = [
      "arn:aws:s3:::${var.artifact_bucket_name}/silver/dataset_id=*/dataset_version_id=*/delivery/upload_request_id=*/*",
      "arn:aws:s3:::${var.artifact_bucket_name}/silver/dataset_id=*/dataset_version_id=*/state/application_id=*/*"
    ]
    condition {
      test     = "StringEquals"
      variable = "s3:if-none-match"
      values   = ["*"]
    }
  }

  statement {
    sid    = "ReadWorkerDatabaseSecret"
    effect = "Allow"

    actions   = ["secretsmanager:GetSecretValue"]
    resources = [var.worker_db_secret_arn]
  }


  statement {
    sid    = "InvokeTrustedVerifier"
    effect = "Allow"

    actions = ["lambda:InvokeFunction"]

    resources = [
      "arn:aws:lambda:${var.aws_region}:${data.aws_caller_identity.current.account_id}:function:ai-workspace-${var.environment}-trusted-verifier"
    ]
  }

}

resource "aws_iam_role_policy" "worker_runtime" {
  count = var.enable_ec2_worker ? 1 : 0

  name   = "ai-workspace-worker-runtime"
  role   = aws_iam_role.worker[0].id
  policy = data.aws_iam_policy_document.worker_runtime[0].json
}
