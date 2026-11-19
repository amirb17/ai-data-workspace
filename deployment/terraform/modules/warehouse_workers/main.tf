# Opt-in, independently scoped ECS worker duties.
# No BigQuery provider dispatch, query runtime binding, or RDS grants.
terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0, < 7.0"
    }
  }
}

locals {
  duties = {
    provider_submission   = 18441
    recovery_attestation  = 18442
    accounting            = 18443
    result_authorization  = 18444
  }
  service_names = {
    for duty, _ in local.duties : duty => replace(duty, "_", "-")
  }
}

# An explicit opt-in module: no worker is created unless callers instantiate
# this module in the reviewed AWS account and provide the full set of inputs.
resource "aws_service_discovery_private_dns_namespace" "workers" {
  name        = "warehouse.internal"
  description = "Private DataRise warehouse worker discovery"
  vpc         = var.vpc_id
}

resource "aws_service_discovery_service" "worker" {
  for_each = local.duties
  name     = local.service_names[each.key]

  dns_config {
    namespace_id = aws_service_discovery_private_dns_namespace.workers.id
    dns_records {
      ttl  = 10
      type = "A"
    }
    routing_policy = "MULTIVALUE"
  }

  health_check_custom_config {
    failure_threshold = 1
  }
}

resource "aws_cloudwatch_log_group" "worker" {
  for_each          = local.duties
  name              = "/ecs/datarise/warehouse/${each.key}"
  retention_in_days = 14
}

resource "aws_security_group" "worker" {
  name_prefix = "datarise-warehouse-worker-"
  description = "Private warehouse duties: accept only API worker RPC"
  vpc_id      = var.vpc_id

  tags = { Project = "datarise", Boundary = "warehouse-workers" }
}

resource "aws_vpc_security_group_ingress_rule" "api_to_worker" {
  for_each                     = local.duties
  security_group_id            = aws_security_group.worker.id
  referenced_security_group_id = var.api_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = each.value
  to_port                      = each.value
  description                  = "Private mTLS RPC for ${each.key} from API SG"
}

resource "aws_vpc_security_group_egress_rule" "worker_to_database" {
  security_group_id            = aws_security_group.worker.id
  referenced_security_group_id = var.database_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  description                  = "PostgreSQL only"
}

resource "aws_vpc_security_group_egress_rule" "worker_to_efs" {
  security_group_id            = aws_security_group.worker.id
  referenced_security_group_id = var.efs_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 2049
  to_port                      = 2049
  description                  = "TLS certificate EFS access points"
}

resource "aws_iam_role" "execution" {
  for_each = local.duties
  name     = "${var.name_prefix}-${replace(each.key, "_", "-")}-exec"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = "sts:AssumeRole"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "execution" {
  for_each = local.duties
  name     = "reviewed-ecr-logs-and-own-secrets"
  role     = aws_iam_role.execution[each.key].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
        Resource = var.image_repository_arn
      },
      {
        Effect = "Allow"
        Action = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.worker[each.key].arn}:*"
      },
      {
        Effect = "Allow"
        Action = ["secretsmanager:GetSecretValue"]
        Resource = [
          var.duties[each.key].db_dsn_secret_arn,
          var.duties[each.key].rpc_secret_arn
        ]
      }
    ]
  })
}

# Task roles intentionally have no BigQuery/GCP, S3, provider-submit or
# cross-duty AWS permissions. Each task uses its own task role.
resource "aws_iam_role" "task" {
  for_each = local.duties
  name     = "${var.name_prefix}-${replace(each.key, "_", "-")}-task"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = "sts:AssumeRole"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "task_efs" {
  for_each = local.duties
  name     = "read-only-tls-material-access-point"
  role     = aws_iam_role.task[each.key].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["elasticfilesystem:ClientMount"]
      Resource = var.duties[each.key].efs_file_system_arn
      Condition = {
        StringEquals = {
          "elasticfilesystem:AccessPointArn" = var.duties[each.key].efs_access_point_arn
        }
      }
    }]
  })
}

resource "aws_ecs_task_definition" "worker" {
  for_each                 = local.duties
  family                   = "${var.name_prefix}-${replace(each.key, "_", "-")}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.execution[each.key].arn
  task_role_arn            = aws_iam_role.task[each.key].arn

  volume {
    name = "tls"
    efs_volume_configuration {
      file_system_id     = var.duties[each.key].efs_file_system_id
      transit_encryption = "ENABLED"
      authorization_config {
        access_point_id = var.duties[each.key].efs_access_point_id
        iam             = "ENABLED"
      }
    }
  }

  container_definitions = jsonencode([{
    name      = each.key
    image     = var.image_digest_uri
    essential = true
    readonlyRootFilesystem = true
    command   = ["python", "-m", "app.warehouse.worker_rpc_server", each.key, "--port", tostring(each.value), "--private-mtls"]
    portMappings = [{
      containerPort = each.value
      protocol      = "tcp"
    }]
    environment = [
      { name = "DATARISE_WAREHOUSE_TLS_CERT", value = "/run/warehouse-tls/server.crt" },
      { name = "DATARISE_WAREHOUSE_TLS_KEY", value = "/run/warehouse-tls/server.key" },
      { name = "DATARISE_WAREHOUSE_TLS_CLIENT_CA", value = "/run/warehouse-tls/client_ca.pem" },
      { name = "PYTHONDONTWRITEBYTECODE", value = "1" }
    ]
    secrets = [
      { name = "DATARISE_WAREHOUSE_DUTY_DSN", valueFrom = var.duties[each.key].db_dsn_secret_arn },
      { name = "DATARISE_WAREHOUSE_RPC_SECRET_B64", valueFrom = var.duties[each.key].rpc_secret_arn }
    ]
    mountPoints = [{
      sourceVolume  = "tls"
      containerPath = "/run/warehouse-tls"
      readOnly      = true
    }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.worker[each.key].name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "worker"
      }
    }
  }])

  lifecycle {
    precondition {
      condition     = can(regex("@sha256:[0-9a-f]{64}$", var.image_digest_uri))
      error_message = "Warehouse worker image must be immutable ECR SHA256 digest."
    }
  }
}

resource "aws_ecs_service" "worker" {
  for_each        = local.duties
  name            = "${var.name_prefix}-${replace(each.key, "_", "-")}"
  cluster         = var.cluster_arn
  task_definition = aws_ecs_task_definition.worker[each.key].arn
  launch_type     = "FARGATE"
  desired_count   = 1

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [aws_security_group.worker.id]
    assign_public_ip = false
  }

  service_registries {
    registry_arn = aws_service_discovery_service.worker[each.key].arn
  }

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  lifecycle {
    precondition {
      condition     = length(var.private_subnet_ids) >= 2
      error_message = "Two reviewed private subnets required."
    }
  }

  depends_on = [
    aws_iam_role_policy.execution,
    aws_iam_role_policy.task_efs,
  ]
}
