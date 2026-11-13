# Allow the dedicated FastAPI ECS task SG to retrieve bootstrap secrets through
# the existing Secrets Manager interface VPC endpoint. No subnet, endpoint, NAT,
# RDS, or public ingress is created or changed.
variable "enable_api_secrets_endpoint_ingress" {
  description = "Opt in to API task SG -> existing Secrets Manager endpoint SG TCP 443"
  type        = bool
  default     = false
}

variable "api_secrets_endpoint_security_group_id" {
  description = "Verified security group attached to existing Secrets Manager interface endpoint"
  type        = string
  default     = ""
  validation {
    condition = (
      var.api_secrets_endpoint_security_group_id == "" ||
      can(regex("^sg-[0-9a-f]{8,17}$", var.api_secrets_endpoint_security_group_id))
    )
    error_message = "Pass a verified Secrets Manager endpoint security group ID."
  }
}

resource "aws_vpc_security_group_ingress_rule" "api_to_secrets_endpoint" {
  count             = var.enable_api_secrets_endpoint_ingress ? 1 : 0
  security_group_id = var.api_secrets_endpoint_security_group_id
  description       = "FastAPI Fargate bootstrap HTTPS from dedicated API task SG"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443

  referenced_security_group_id = aws_security_group.api_task[0].id

  tags = merge(local.tags, { Name = "ai-workspace-${var.environment}-api-to-secrets-endpoint" })

  lifecycle {
    precondition {
      condition = (
        var.enable_api_task_security_group &&
        var.api_vpc_id != "" &&
        var.api_secrets_endpoint_security_group_id != "" &&
        var.api_secrets_endpoint_security_group_id != aws_security_group.api_task[0].id
      )
      error_message = "Require verified distinct API task and Secrets Manager endpoint SGs."
    }
    prevent_destroy = true
  }
}

output "api_secrets_endpoint_ingress_rule_id" {
  value = try(aws_vpc_security_group_ingress_rule.api_to_secrets_endpoint[0].id, null)
}
