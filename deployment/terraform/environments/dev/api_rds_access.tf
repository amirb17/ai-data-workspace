# Grant the existing RDS database access from the isolated FastAPI task SG only.
# Database/SG themselves are pre-existing and intentionally NOT imported or recreated.
variable "enable_api_rds_ingress" {
  description = "Opt in to a single PostgreSQL ingress rule on the existing RDS security group"
  type        = bool
  default     = false
}

variable "api_rds_security_group_id" {
  description = "Existing RDS security group (not created by this module)"
  type        = string
  default     = ""
  validation {
    condition     = var.api_rds_security_group_id == "" || can(regex("^sg-[0-9a-f]+$", var.api_rds_security_group_id))
    error_message = "Provide a valid pre-existing RDS security group ID."
  }
}

resource "aws_vpc_security_group_ingress_rule" "api_to_rds" {
  count                        = var.enable_api_rds_ingress ? 1 : 0
  security_group_id            = var.api_rds_security_group_id
  referenced_security_group_id = aws_security_group.api_task[0].id
  description                  = "PostgreSQL from DataRise ECS API tasks only"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432

  lifecycle {
    precondition {
      condition = (var.enable_api_task_security_group &&
        can(regex("^sg-[0-9a-f]+$", var.api_rds_security_group_id)) &&
        var.api_rds_security_group_id != aws_security_group.api_task[0].id)
      error_message = "Review the existing RDS SG and enable the isolated API task SG first."
    }
    prevent_destroy = true
  }
}

output "api_to_rds_ingress_rule_id" {
  description = "Single allowed PostgreSQL ingress from API task SG"
  value       = try(aws_vpc_security_group_ingress_rule.api_to_rds[0].id, null)
}
