"""Static policy guards for CloudFront-to-internal-ALB ingress."""
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[1] /
          "deployment/terraform/environments/dev/api_cloudfront_alb_ingress.tf").read_text(encoding="utf-8")


def test_ingress_opt_in_sg_reference_only():
    assert 'variable "enable_api_cloudfront_alb_ingress"' in SOURCE
    assert 'default     = false' in SOURCE
    assert 'resource "aws_vpc_security_group_ingress_rule" "api_alb_from_cloudfront_vpc_origin"' in SOURCE
    assert 'security_group_id = aws_security_group.api_internal_alb[0].id' in SOURCE
    assert 'referenced_security_group_id = var.api_cloudfront_service_security_group_id' in SOURCE
    assert 'from_port         = 80' in SOURCE
    assert 'to_port           = 80' in SOURCE
    assert "0.0.0.0/0" not in SOURCE
    assert "cidr_ipv4" not in SOURCE


def test_ingress_requires_existing_private_architecture():
    for required in (
        'var.enable_api_internal_alb_security &&',
        'var.enable_api_internal_alb &&',
        'var.enable_api_cloudfront_vpc_origin &&',
        'var.api_cloudfront_service_security_group_id != ""',
        'prevent_destroy = true',
    ):
        assert required in SOURCE
