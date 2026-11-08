
variable "enable_rds" {
  description = "Provision the private PostgreSQL database"
  type        = bool
  default     = false
}

variable "rds_instance_class" {
  description = "Development RDS instance class"
  type        = string
  default     = "db.t4g.micro"
}

resource "aws_db_instance" "workspace" {
  count = var.enable_rds ? 1 : 0

  identifier = "ai-workspace-${var.environment}-postgres"


  engine         = "postgres"
  engine_version = "18.4"
  instance_class = var.rds_instance_class


  allocated_storage = 20
  storage_type      = "gp3"
  storage_encrypted = true

  db_name                     = "aiworkspace"
  username                    = "workspace_admin"
  manage_master_user_password = true

  db_subnet_group_name   = aws_db_subnet_group.private[0].name
  vpc_security_group_ids = [aws_security_group.database[0].id]

  publicly_accessible = false
  multi_az            = false

  backup_retention_period = 1
  deletion_protection     = true

  skip_final_snapshot       = false
  final_snapshot_identifier = "ai-workspace-${var.environment}-final"

  apply_immediately = false

  tags = merge(local.tags, {
    Name = "ai-workspace-${var.environment}-postgres"
  })

  lifecycle {
    precondition {
      condition     = var.enable_network
      error_message = "RDS requires the private network to be enabled."
    }
  }
}

output "rds_endpoint" {
  description = "Private PostgreSQL endpoint"
  value       = try(aws_db_instance.workspace[0].address, null)
}
