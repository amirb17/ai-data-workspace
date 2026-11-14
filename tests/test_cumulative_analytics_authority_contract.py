"""Production Analytics release: API remains denied raw publishing DML.

Static guard complements PostgreSQL integration and live AWS acceptance; it is
not by itself proof that RDS triggers or S3 policy allow a refresh.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_analytics_uses_owner_fenced_authority():
    code = (ROOT / "app/services/dataset_analytics_service.py").read_text(encoding="utf-8")
    assert "task21_analytics_begin(%s,%s,%s,%s)" in code
    assert "task21_analytics_publish(%s,%s,%s,%s,%s,%s,%s)" in code
    assert "task21_analytics_fail(%s,%s,%s,%s,%s,%s)" in code
    assert "(user['user_id'], workspace_id, dataset_id" in code


def test_authority_migration_does_not_grant_api_table_writes():
    sql = (ROOT / "migrations/0063_cumulative_analytics_authority.sql").read_text(encoding="utf-8")
    assert "task21_analytics_owner(actor,w,d)" in sql
    assert "ws.owner=u.owner_key" in sql
    assert "actual IS DISTINCT FROM expected_state" in sql
    assert "IF actual=r.source_state_id THEN" in sql
    assert "status='PUBLISHED'" in sql
    assert "r.status='SUCCESS' THEN RETURN" in sql
    assert "TO datarise_api" in sql
    assert "GRANT INSERT ON datasets" not in sql
    assert "GRANT UPDATE ON datasets" not in sql
    assert "GRANT INSERT ON dataset_gold_runs" not in sql
    assert "GRANT UPDATE ON dataset_gold_runs" not in sql


def test_iam_policy_default_off_and_scoped_to_known_dataset():
    terraform = (ROOT / "deployment/terraform/environments/dev/api_analytics_artifacts.tf").read_text(encoding="utf-8")
    assert 'variable "api_enable_analytics_artifacts"' in terraform
    assert 'default     = false' in terraform
    assert 'silver/dataset_id=1/dataset_version_id=1/*' in terraform
    assert 'gold/dataset_id=1/*' in terraform
    assert '"s3:DeleteObject"' not in terraform
    assert '"s3:ListBucket"' not in terraform
