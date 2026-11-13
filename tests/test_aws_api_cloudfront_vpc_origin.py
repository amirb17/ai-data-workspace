"""CloudFront origin must be explicitly opted in and remain private."""
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[1] /
          "deployment/terraform/environments/dev/api_cloudfront_vpc_origin.tf").read_text(encoding="utf-8")


def test_private_origin_gated_and_bound_to_alb():
    assert 'variable "enable_api_cloudfront_vpc_origin"' in SOURCE
    assert 'default     = false' in SOURCE
    assert 'resource "aws_cloudfront_vpc_origin" "api"' in SOURCE
    assert 'arn                    = aws_lb.api_internal[0].arn' in SOURCE
    assert 'origin_protocol_policy = "http-only"' in SOURCE
    assert 'http_port              = 80' in SOURCE
    assert 'prevent_destroy = true' in SOURCE


def test_no_public_distribution_or_security_group_opening():
    assert 'resource "aws_cloudfront_distribution"' not in SOURCE
    assert 'resource "aws_vpc_security_group_ingress_rule"' not in SOURCE
    assert 'resource "aws_ecs_service"' not in SOURCE
    assert '"0.0.0.0/0"' not in SOURCE
