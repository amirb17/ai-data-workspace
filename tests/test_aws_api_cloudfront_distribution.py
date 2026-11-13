"""Guard the first CloudFront distribution deployment as disabled/no-cache."""
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[1] /
          "deployment/terraform/environments/dev/api_cloudfront_distribution.tf").read_text(encoding="utf-8")


def test_distribution_is_disabled_and_opt_in():
    assert 'variable "enable_api_cloudfront_distribution"' in SOURCE
    assert 'variable "api_cloudfront_distribution_live"' in SOURCE
    assert SOURCE.count('default     = false') >= 2
    assert 'enabled         = var.api_cloudfront_distribution_live' in SOURCE
    assert '!var.api_cloudfront_distribution_live' in SOURCE
    assert 'prevent_destroy = true' in SOURCE


def test_https_only_private_origin_and_no_cache():
    for setting in (
        'domain_name = aws_lb.api_internal[0].dns_name',
        'vpc_origin_id = aws_cloudfront_vpc_origin.api[0].id',
        'viewer_protocol_policy = "https-only"',
        'cache_policy_id = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"',
        'origin_request_policy_id = "33f36d7e-f396-46d9-90e0-52428a34d9dc"',
        'cloudfront_default_certificate = true',
        'var.enable_api_cloudfront_alb_ingress &&',
    ):
        assert setting in SOURCE
    assert 'resource "aws_ecs_service"' not in SOURCE


def test_cloudfront_supplies_original_viewer_scheme():
    assert 'CloudFront-Forwarded-Proto' in SOURCE
    assert 'AllViewerAndCloudFrontHeaders-2022-06' in SOURCE
