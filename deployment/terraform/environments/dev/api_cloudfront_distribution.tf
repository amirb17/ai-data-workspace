# Obtain a stable AWS-managed HTTPS hostname without opening the API yet.
# Distribution is created DISABLED; turn on only after trusted TLS/proxy and
# browser authentication integration are tested.
variable "enable_api_cloudfront_distribution" {
  description = "Create disabled-by-default API CloudFront distribution"
  type        = bool
  default     = false
}

variable "api_cloudfront_distribution_live" {
  description = "Explicit separate gate for public traffic; leave false for first provision"
  type        = bool
  default     = false
}

resource "aws_cloudfront_distribution" "api" {
  count           = var.enable_api_cloudfront_distribution ? 1 : 0
  enabled         = var.api_cloudfront_distribution_live
  is_ipv6_enabled = true
  comment         = "DataRise private FastAPI VPC origin; API responses not cached"
  price_class     = "PriceClass_100"

  origin {
    domain_name = aws_lb.api_internal[0].dns_name
    origin_id   = "datarise-private-api-alb"

    vpc_origin_config {
      vpc_origin_id = aws_cloudfront_vpc_origin.api[0].id
    }
  }

  default_cache_behavior {
    target_origin_id       = "datarise-private-api-alb"
    viewer_protocol_policy = "https-only"
    allowed_methods        = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods         = ["GET", "HEAD", "OPTIONS"]
    compress               = true

    # AWS managed CachingDisabled: no caching of private/auth responses.
    cache_policy_id = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
    # Preserve viewer Host, cookies, query and origin/CSRF headers.
    origin_request_policy_id = "216adef6-5c7f-47e4-b989-5492eafa07d3"
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
    minimum_protocol_version       = "TLSv1.2_2021"
  }

  tags = merge(local.tags, { Name = "ai-workspace-${var.environment}-api-distribution" })

  lifecycle {
    precondition {
      condition = (
        var.enable_api_internal_alb &&
        var.enable_api_cloudfront_vpc_origin &&
        var.enable_api_cloudfront_alb_ingress &&
        !var.api_cloudfront_distribution_live
      )
      error_message = "First create distribution DISABLED after verified ALB ingress; review HTTPS boundary before enabling."
    }
    prevent_destroy = true
  }
}

output "api_cloudfront_distribution_hostname" {
  description = "Public DNS name (distribution initially disabled; not yet a working API)"
  value       = try(aws_cloudfront_distribution.api[0].domain_name, null)
}

output "api_cloudfront_distribution_id" {
  value = try(aws_cloudfront_distribution.api[0].id, null)
}
