data "aws_subnet" "worker" {
  count = var.enable_ec2_worker ? 1 : 0
  id    = var.worker_subnet_id
}

resource "aws_instance" "worker" {
  count = var.enable_ec2_worker ? 1 : 0

  ami                    = var.worker_ami_id
  instance_type          = var.worker_instance_type
  subnet_id              = var.worker_subnet_id
  vpc_security_group_ids = [aws_security_group.worker[0].id]

  iam_instance_profile = aws_iam_instance_profile.worker[0].name

  # A reviewed public subnet with an Internet Gateway is required.
  # The worker security group must have no inbound rules.
  associate_public_ip_address = true
  monitoring                  = false

  user_data = templatefile("${path.module}/worker-user-data.sh.tftpl", {
    aws_region             = var.aws_region
    artifact_bucket        = var.artifact_bucket_name
    queue_url              = aws_sqs_queue.processing[0].id
    verifier_function_name = aws_lambda_function.trusted_verifier[0].arn
    worker_secret_arn      = var.worker_db_secret_arn
    image_uri              = var.worker_image_uri
    registry_host          = split("/", var.worker_image_uri)[0]
  })

  user_data_replace_on_change = true

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
    instance_metadata_tags      = "disabled"
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = 16
    encrypted             = true
    delete_on_termination = true
  }

  tags = merge(local.tags, {
    Name = "ai-workspace-${var.environment}-worker"
  })

  lifecycle {
    precondition {
      condition = (
        var.enable_network &&
        var.enable_processing_queue &&
        var.enable_ecr &&
        var.enable_rds
      )
      error_message = "EC2 worker dependencies must be enabled."
    }

    precondition {
      condition = (
        var.worker_ami_id != "" &&
        var.worker_subnet_id != "" &&
        var.worker_image_uri != "" &&
        var.worker_image_digest != ""
      )
      error_message = "Reviewed AMI, subnet and immutable worker image are required."
    }

    precondition {
      condition = (
        can(regex("@sha256:[0-9a-f]{64}$", var.worker_image_uri)) &&
        endswith(var.worker_image_uri, var.worker_image_digest)
      )
      error_message = "Worker image URI must be pinned to the reviewed SHA-256 digest."
    }

    precondition {
      condition     = data.aws_subnet.worker[0].vpc_id == var.vpc_id
      error_message = "Worker subnet must belong to the selected target VPC."
    }
  }
}
