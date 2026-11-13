terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0, < 7.0"
    }
  }
}

# Deliberately separate state from deployment/terraform/environments/dev.
# Existing API, queue, RDS, ECR and worker are data sources, never resources.
provider "aws" {
  region  = "ap-southeast-2"
  profile = "datarise"
}

variable "dispatcher_image_uri" {
  type = string
}
variable "worker_subnet_id" {
  type    = string
  default = "subnet-02faa75eeef119039"
}
variable "rds_security_group_id" {
  description = "Existing RDS security group ID from aws rds describe-db-instances"
  type        = string
  validation {
    condition     = can(regex("^sg-[0-9a-f]+$", var.rds_security_group_id))
    error_message = "Existing RDS security group ID is required."
  }
}
variable "worker_ami_id" {
  type    = string
  default = "ami-062900c135ab91582"
}
variable "instance_type" {
  type    = string
  default = "t3.micro"
}

locals {
  account_id         = "202064056386"
  image_repository   = "${local.account_id}.dkr.ecr.ap-southeast-2.amazonaws.com/ai-workspace-dev-worker"
  dispatch_secret    = "arn:aws:secretsmanager:ap-southeast-2:${local.account_id}:secret:ai-workspace/dev/db/dispatch-aMEHug"
  worker_secret      = "arn:aws:secretsmanager:ap-southeast-2:${local.account_id}:secret:ai-workspace/dev/db/worker-kSDc7D"
  verifier_secret    = "arn:aws:secretsmanager:ap-southeast-2:${local.account_id}:secret:ai-workspace/dev/db/verifier-ERVjHj"
}

data "aws_caller_identity" "current" {}
data "aws_subnet" "worker" {
  id = var.worker_subnet_id
}
data "aws_db_instance" "workspace" {
  db_instance_identifier = "ai-workspace-dev-postgres"
}
data "aws_security_group" "database" {
  id = var.rds_security_group_id
}

# An interface endpoint accepts traffic only from sources allowed by its
# attached security group(s). The existing API has this rule; the new
# dedicated dispatcher SG must be authorized separately.
data "aws_vpc_endpoint" "secretsmanager" {
  vpc_id       = data.aws_subnet.worker.vpc_id
  service_name = "com.amazonaws.ap-southeast-2.secretsmanager"
}
data "aws_sqs_queue" "processing" {
  name = "ai-workspace-dev-processing"
}
data "aws_ecr_repository" "worker" {
  name = "ai-workspace-dev-worker"
}
data "aws_secretsmanager_secret" "dispatch" {
  arn = local.dispatch_secret
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}
resource "aws_iam_role" "dispatch" {
  name               = "ai-workspace-dev-dispatcher"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}
resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.dispatch.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}
resource "aws_iam_instance_profile" "dispatch" {
  name = "ai-workspace-dev-dispatcher-profile"
  role = aws_iam_role.dispatch.name
}

data "aws_iam_policy_document" "runtime" {
  statement {
    sid       = "RegistryAuth"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    sid       = "ReadOnlyPinnedImage"
    actions   = ["ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
    resources = [data.aws_ecr_repository.worker.arn]
  }
  statement {
    sid       = "SendOnlyProcessingQueue"
    actions   = ["sqs:SendMessage"]
    resources = [data.aws_sqs_queue.processing.arn]
  }
  statement {
    sid       = "ReadOnlyDispatchDatabaseSecret"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [data.aws_secretsmanager_secret.dispatch.arn]
  }
}
resource "aws_iam_role_policy" "runtime" {
  name   = "processing-outbox-dispatcher"
  role   = aws_iam_role.dispatch.id
  policy = data.aws_iam_policy_document.runtime.json
}

resource "aws_security_group" "dispatcher" {
  name_prefix = "ai-workspace-dev-dispatcher-"
  description = "Dedicated dispatcher no ingress"
  vpc_id      = data.aws_subnet.worker.vpc_id
  tags        = { Name = "ai-workspace-dev-dispatcher-sg" }
}
resource "aws_vpc_security_group_ingress_rule" "dispatch_to_secrets_endpoint" {
  for_each                     = toset(data.aws_vpc_endpoint.secretsmanager.security_group_ids)
  security_group_id            = each.value
  referenced_security_group_id = aws_security_group.dispatcher.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  description                  = "Allow isolated dispatcher to retrieve its Secrets Manager credential"
}

resource "aws_vpc_security_group_egress_rule" "https" {
  security_group_id = aws_security_group.dispatcher.id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
  description       = "AWS APIs and ECR"
}
resource "aws_vpc_security_group_egress_rule" "postgres" {
  security_group_id            = aws_security_group.dispatcher.id
  referenced_security_group_id = data.aws_security_group.database.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  description                  = "Private PostgreSQL RDS"
}
resource "aws_vpc_security_group_ingress_rule" "rds_dispatch" {
  security_group_id            = data.aws_security_group.database.id
  referenced_security_group_id = aws_security_group.dispatcher.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  description                  = "Allow dedicated dispatcher to database"
}

resource "aws_instance" "dispatcher" {
  ami                         = var.worker_ami_id
  instance_type               = var.instance_type
  subnet_id                   = data.aws_subnet.worker.id
  associate_public_ip_address = true
  iam_instance_profile        = aws_iam_instance_profile.dispatch.name
  vpc_security_group_ids      = [aws_security_group.dispatcher.id]
  monitoring                  = false

  user_data = templatefile("${path.module}/../environments/dev/dispatcher-user-data.sh.tftpl", {
    region        = "ap-southeast-2"
    queue         = data.aws_sqs_queue.processing.url
    secret        = data.aws_secretsmanager_secret.dispatch.arn
    role_arn      = aws_iam_role.dispatch.arn
    image_uri     = var.dispatcher_image_uri
    registry_host = split("/", var.dispatcher_image_uri)[0]
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
  tags = { Name = "ai-workspace-dev-dispatcher" }

  lifecycle {
    precondition {
      condition = (
        data.aws_caller_identity.current.account_id == local.account_id &&
        data.aws_subnet.worker.vpc_id == "vpc-0235c4ff54488355c" &&
        data.aws_security_group.database.vpc_id == data.aws_subnet.worker.vpc_id &&
        var.dispatcher_image_uri == "${local.image_repository}@sha256:fe7dac21ff37856203a7d7ded8bc6bc0f4c3de99996327584473c0faed2d86ab"
      )
      error_message = "Dispatcher rollout requires the reviewed account, VPC and verified immutable image."
    }
  }
}

output "dispatcher_instance_id" {
  value = aws_instance.dispatcher.id
}
output "dispatcher_role_arn" {
  value = aws_iam_role.dispatch.arn
}
output "dispatcher_image_uri" {
  value = var.dispatcher_image_uri
}
