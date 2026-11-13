# Explicit opt-in SMTP delivery via an external provider. Store SMTP credentials
# as Secrets Manager JSON (username, password); never commit or pass plaintext.
variable "enable_api_smtp_delivery" {
  description = "Enable transactional SMTP from ECS only after sender and egress review"
  type        = bool
  default     = false
}

variable "api_smtp_secret_arn" {
  description = "Secrets Manager ARN holding JSON keys username and password"
  type        = string
  default     = ""
}

variable "api_smtp_sender" {
  description = "Verified SMTP sender email; not a secret"
  type        = string
  default     = ""
}

variable "api_smtp_host" {
  description = "Approved SMTP relay hostname"
  type        = string
  default     = "smtp-relay.brevo.com"
}

variable "api_smtp_port" {
  description = "SMTP STARTTLS port"
  type        = number
  default     = 587
  validation {
    condition     = var.api_smtp_port == 587
    error_message = "Public SMTP rollout currently approves STARTTLS port 587 only."
  }
}

resource "aws_vpc_security_group_egress_rule" "api_smtp_starttls" {
  count             = var.enable_api_smtp_delivery ? 1 : 0
  security_group_id = aws_security_group.api_task[0].id
  ip_protocol       = "tcp"
  from_port         = 587
  to_port           = 587
  cidr_ipv4         = "0.0.0.0/0"
  description       = "Explicit egress for approved authenticated TLS SMTP; SG cannot filter DNS"
  tags              = merge(local.tags, { Name = "ai-workspace-${var.environment}-api-smtp-egress" })

  lifecycle {
    precondition {
      condition = (
        var.enable_api_task_definition &&
        var.enable_api_task_security_group &&
        can(regex("^arn:aws:secretsmanager:${var.aws_region}:[0-9]{12}:secret:", var.api_smtp_secret_arn)) &&
        can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.api_smtp_sender)) &&
        var.api_smtp_host == "smtp-relay.brevo.com"
      )
      error_message = "SMTP egress needs an approved task, Sydney secret, sender and Brevo relay."
    }
  }
}

output "api_smtp_egress_rule_id" {
  value       = try(aws_vpc_security_group_egress_rule.api_smtp_starttls[0].id, null)
  description = "Opt-in TCP/587 outbound access for API SMTP; not proof of mail delivery"
}
