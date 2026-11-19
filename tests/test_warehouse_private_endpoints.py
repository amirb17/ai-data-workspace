"""Guard the opt-in AWS warehouse endpoint infrastructure against public routes."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "deployment/terraform/modules/warehouse_private_endpoints"


def test_exactly_three_required_endpoints_and_no_existing_gateway_replacement():
    config = (ROOT / "main.tf").read_text(encoding="utf-8")
    assert 'toset(["ecr.api", "ecr.dkr", "logs"])' in config
    assert '"com.amazonaws.${var.region}.${each.key}"' in config
    assert 'vpc_endpoint_type   = "Interface"' in config
    assert 'private_dns_enabled = true' in config
    assert "secretsmanager" not in config
    assert "aws_route_table" not in config
    assert "aws_subnet" not in config


def test_tls_ingress_scoped_to_worker_security_group():
    config = (ROOT / "main.tf").read_text(encoding="utf-8")
    assert "referenced_security_group_id = var.worker_security_group_id" in config
    assert "from_port                    = 443" in config
    assert "to_port                      = 443" in config
    assert "0.0.0.0/0" not in config
    assert 'aws_security_group" "endpoints"' in config


def test_verified_vpc_and_two_private_subnets_are_pinned():
    config = (ROOT / "variables.tf").read_text(encoding="utf-8")
    assert 'var.vpc_id == "vpc-0235c4ff54488355c"' in config
    assert 'var.region == "ap-southeast-2"' in config
    assert 'length(var.private_subnet_ids) == 2' in config
    assert 'length(setsubtract(' in config
    assert "subnet-07e43e5559f36b21c" in config
    assert "subnet-044eb7e6626f02bbf" in config
