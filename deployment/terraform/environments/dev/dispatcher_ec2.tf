# Dedicated dispatcher host. Opt-in infrastructure only; activation remains
# fail-closed until distinct PostgreSQL privileges and live readiness are proved.
variable "enable_dispatcher_ec2" {
  description = "Provision dedicated outbox dispatcher EC2 identity and host"
  type        = bool
  default     = false
}
variable "dispatcher_image_uri" {
  description = "Separately reviewed, digest-pinned dispatcher image (same ECR repository)"
  type        = string
  default     = ""
}
variable "dispatcher_image_digest" {
  description = "Immutable SHA-256 digest for dispatcher image"
  type        = string
  default     = ""
}
variable "dispatcher_db_secret_arn" {
  description = "Secrets Manager ARN for datarise_dispatch only"
  type        = string
  default     = ""
}

data "aws_subnet" "dispatcher" {
  count = var.enable_dispatcher_ec2 ? 1 : 0
  id    = var.worker_subnet_id
}

data "aws_iam_policy_document" "dispatcher_trust" {
  count = var.enable_dispatcher_ec2 ? 1 : 0
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "dispatcher" {
  count              = var.enable_dispatcher_ec2 ? 1 : 0
  name               = "ai-workspace-${var.environment}-dispatcher"
  assume_role_policy = data.aws_iam_policy_document.dispatcher_trust[0].json
}

resource "aws_iam_role_policy_attachment" "dispatcher_ssm" {
  count      = var.enable_dispatcher_ec2 ? 1 : 0
  role       = aws_iam_role.dispatcher[0].name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "dispatcher" {
  count = var.enable_dispatcher_ec2 ? 1 : 0
  name  = "ai-workspace-${var.environment}-dispatcher-profile"
  role  = aws_iam_role.dispatcher[0].name
}

data "aws_iam_policy_document" "dispatcher_runtime" {
  count = var.enable_dispatcher_ec2 ? 1 : 0
  statement {
    sid       = "ECRAuthorization"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    sid       = "PullPinnedDispatchImage"
    actions   = ["ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
    resources = [aws_ecr_repository.worker[0].arn]
  }
  statement {
    sid       = "SendOnlyProcessingJobs"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.processing[0].arn]
  }
  statement {
    sid       = "ReadOnlyDispatchSecret"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [var.dispatcher_db_secret_arn]
  }
}

resource "aws_iam_role_policy" "dispatcher_runtime" {
  count  = var.enable_dispatcher_ec2 ? 1 : 0
  name   = "processing-outbox-dispatcher"
  role   = aws_iam_role.dispatcher[0].id
  policy = data.aws_iam_policy_document.dispatcher_runtime[0].json
}

resource "aws_security_group" "dispatcher" {
  count       = var.enable_dispatcher_ec2 ? 1 : 0
  name_prefix = "ai-workspace-dispatcher-"
  description = "Outbound only dedicated dispatcher"
  vpc_id      = var.vpc_id
  tags        = merge(local.tags, { Name = "ai-workspace-${var.environment}-dispatcher-sg" })
}

resource "aws_vpc_security_group_egress_rule" "dispatcher_https" {
  count             = var.enable_dispatcher_ec2 ? 1 : 0
  security_group_id = aws_security_group.dispatcher[0].id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
  description       = "AWS APIs and ECR HTTPS"
}

resource "aws_vpc_security_group_egress_rule" "dispatcher_postgres" {
  count                        = var.enable_dispatcher_ec2 ? 1 : 0
  security_group_id            = aws_security_group.dispatcher[0].id
  referenced_security_group_id = aws_security_group.database[0].id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  description                  = "Private RDS only"
}

resource "aws_vpc_security_group_ingress_rule" "rds_from_dispatcher" {
  count                        = var.enable_dispatcher_ec2 ? 1 : 0
  security_group_id            = aws_security_group.database[0].id
  referenced_security_group_id = aws_security_group.dispatcher[0].id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  description                  = "PostgreSQL dispatcher principal"
}

resource "aws_instance" "dispatcher" {
  count                       = var.enable_dispatcher_ec2 ? 1 : 0
  ami                         = var.worker_ami_id
  instance_type               = var.worker_instance_type
  subnet_id                   = var.worker_subnet_id
  associate_public_ip_address = true
  vpc_security_group_ids      = [aws_security_group.dispatcher[0].id]
  iam_instance_profile        = aws_iam_instance_profile.dispatcher[0].name
  monitoring                  = false

  user_data = templatefile("${path.module}/dispatcher-user-data.sh.tftpl", {
    region          = var.aws_region
    queue           = aws_sqs_queue.processing[0].id
    secret          = var.dispatcher_db_secret_arn
    role_arn        = aws_iam_role.dispatcher[0].arn
    image_uri       = var.dispatcher_image_uri
    registry_host   = split("/", var.dispatcher_image_uri)[0]
  })
  user_data_replace_on_change = true

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }
  root_block_device {
    volume_type           = "gp3"
    volume_size           = 16
    encrypted             = true
    delete_on_termination = true
  }

  tags = merge(local.tags, { Name = "ai-workspace-${var.environment}-dispatcher" })

  lifecycle {
    precondition {
      condition = (
        var.enable_network && var.enable_rds && var.enable_ecr &&
        var.enable_processing_queue && var.worker_ami_id != "" &&
        var.worker_subnet_id != "" && var.dispatcher_db_secret_arn != "" &&
        var.worker_db_secret_arn != "" && var.verifier_db_secret_arn != "" &&
        var.dispatcher_db_secret_arn != var.worker_db_secret_arn &&
        var.dispatcher_db_secret_arn != var.verifier_db_secret_arn &&
        var.dispatcher_image_digest != "" &&
        can(regex("^sha256:[0-9a-f]{64}$", var.dispatcher_image_digest)) &&
        can(regex("@sha256:[0-9a-f]{64}$", var.dispatcher_image_uri)) &&
        endswith(var.dispatcher_image_uri, var.dispatcher_image_digest)
      )
      error_message = "Dispatcher requires reviewed immutable image, queue, RDS and a distinct DB secret."
    }
    precondition {
      condition     = data.aws_subnet.dispatcher[0].vpc_id == var.vpc_id
      error_message = "Dispatcher subnet is outside target VPC."
    }
  }
}

output "dispatcher_instance_id" {
  value = try(aws_instance.dispatcher[0].id, null)
}
output "dispatcher_role_arn" {
  value = try(aws_iam_role.dispatcher[0].arn, null)
}
