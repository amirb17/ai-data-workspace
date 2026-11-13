# CloudFront VPC origin for the existing, private API ALB.
# OFF by default. No public distribution or ECS service is created here.
# ALB ingress is intentionally not added automatically: after AWS creates the
# CloudFront-VPCOrigins-Service-SG, explicitly review and allow only that SG.
variable "enable_api_cloudfront_vpc_origin" {
  description = "Opt in to a CloudFront VPC origin pointing at the private API ALB"
  type        = bool
  default     = false
}

resource "aws_cloudfront_vpc_origin" "api" {
  count = var.enable_api_cloudfront_vpc_origin ? 1 : 0

  vpc_origin_endpoint_config {
    name                   = "ai-workspace-${var.environment}-api"
    arn                    = aws_lb.api_internal[0].arn
    http_port              = 80
    https_port             = 443
    origin_protocol_policy = "http-only"

    # Required by the Terraform provider schema even for HTTP-only origins.
    origin_ssl_protocols {
      items    = ["TLSv1.2"]
      quantity = 1
    }
  }

  tags = merge(local.tags, { Name = "ai-workspace-${var.environment}-api-vpc-origin" })

  lifecycle {
    precondition {
      condition     = var.enable_api_internal_alb && var.enable_api_internal_alb_security
      error_message = "An existing private ALB and its isolated SG are required."
    }
    prevent_destroy = true
  }
}

output "api_cloudfront_vpc_origin_id" {
  description = "VPC origin ID for a later no-cache API CloudFront distribution"
  value       = try(aws_cloudfront_vpc_origin.api[0].id, null)
}
