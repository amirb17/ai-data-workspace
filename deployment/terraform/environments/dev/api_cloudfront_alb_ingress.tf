# CloudFront VPC origin -> private ALB. This modifies ONLY our ALB security group.
# Never edit AWS-managed CloudFront-VPCOrigins-Service-SG.
variable "enable_api_cloudfront_alb_ingress" {
  description = "Opt in to inbound HTTP from CloudFront service-managed VPC origin SG only"
  type        = bool
  default     = false
}

variable "api_cloudfront_service_security_group_id" {
  description = "Verified CloudFront-VPCOrigins-Service-SG ID in this existing VPC"
  type        = string
  default     = ""
  validation {
    condition     = var.api_cloudfront_service_security_group_id == "" || can(regex("^sg-[0-9a-f]{8,17}$", var.api_cloudfront_service_security_group_id))
    error_message = "Pass an existing verified security group ID, not a CIDR."
  }
}

resource "aws_vpc_security_group_ingress_rule" "api_alb_from_cloudfront_vpc_origin" {
  count             = var.enable_api_cloudfront_alb_ingress ? 1 : 0
  security_group_id = aws_security_group.api_internal_alb[0].id
  description       = "CloudFront VPC origin only to private API ALB HTTP listener"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80

  referenced_security_group_id = var.api_cloudfront_service_security_group_id

  tags = merge(local.tags, { Name = "ai-workspace-${var.environment}-api-cloudfront-alb-ingress" })

  lifecycle {
    precondition {
      condition = (
        var.enable_api_internal_alb_security &&
        var.enable_api_internal_alb &&
        var.enable_api_cloudfront_vpc_origin &&
        var.api_cloudfront_service_security_group_id != ""
      )
      error_message = "Require existing private ALB, deployed VPC origin, and explicit CloudFront service SG ID."
    }
    prevent_destroy = true
  }
}

output "api_cloudfront_alb_ingress_rule_id" {
  value = try(aws_vpc_security_group_ingress_rule.api_alb_from_cloudfront_vpc_origin[0].id, null)
}
