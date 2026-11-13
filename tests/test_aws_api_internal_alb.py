"""Offline guardrails for the internal ALB; no billable AWS calls."""
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[1] /
          "deployment/terraform/environments/dev/api_internal_alb.tf").read_text(encoding="utf-8")


def test_internal_alb_opt_in_and_not_public():
    assert 'variable "enable_api_internal_alb"' in SOURCE
    assert 'default     = false' in SOURCE
    assert 'resource "aws_lb" "api_internal"' in SOURCE
    assert 'internal                   = true' in SOURCE
    assert 'enable_deletion_protection = true' in SOURCE
    assert 'subnets                    = var.api_alb_private_subnet_ids' in SOURCE
    assert 'prevent_destroy = true' in SOURCE


def test_target_group_private_health_only():
    assert 'resource "aws_lb_target_group" "api"' in SOURCE
    assert 'target_type = "ip"' in SOURCE
    assert 'path                = "/health"' in SOURCE
    assert 'matcher             = "200"' in SOURCE
    assert 'resource "aws_lb_listener" "api_http"' in SOURCE
    assert 'protocol          = "HTTP"' in SOURCE
    assert 'resource "aws_ecs_service"' not in SOURCE
    assert 'resource "aws_cloudfront_distribution"' not in SOURCE
