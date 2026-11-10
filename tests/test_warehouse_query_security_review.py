"""413H: non-executing warehouse gateway security regression contracts.

These AST checks are guardrails, not proof of authentication, data isolation
or absence of runtime dispatch via dynamic imports. See 413H release review.
"""
import ast
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
WAREHOUSE_SERVICES=(
    "warehouse_sql_compiled_evidence_service.py",
    "warehouse_sql_dry_run_service.py",
    "warehouse_sql_admission_service.py",
    "warehouse_sql_preclaim_service.py",
    "warehouse_sql_claim_rehearsal_service.py",
    "warehouse_sql_recovery_inspection_service.py",
    "warehouse_sql_recovery_settlement_service.py",
)


@pytest.mark.parametrize("filename",WAREHOUSE_SERVICES)
def test_warehouse_services_never_dispatch_provider_execute(filename):
    source=(ROOT/"app/services"/filename).read_text(encoding="utf-8")
    tree=ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node,ast.Call):
            callee=node.func
            if isinstance(callee,ast.Attribute):
                assert callee.attr not in {"execute","query","query_and_wait"}
            elif isinstance(callee,ast.Name):
                assert callee.id not in {"execute_governed_query"}


@pytest.mark.parametrize("filename",WAREHOUSE_SERVICES)
def test_warehouse_services_do_not_directly_import_bigquery_or_client_sql(filename):
    source=(ROOT/"app/services"/filename).read_text(encoding="utf-8")
    tree=ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node,ast.Import):
            assert not any(
                name.name.startswith(("google.cloud","psycopg"))
                for name in node.names
            )
        elif isinstance(node,ast.ImportFrom):
            assert not (node.module or "").startswith((
                "google.cloud","app.query_governance.providers.bigquery",
                "psycopg",
            ))


def test_claim_rehearsal_must_advertise_no_execution_authorization():
    path=ROOT/"app/services/warehouse_sql_claim_rehearsal_service.py"
    source=path.read_text(encoding="utf-8")
    tree=ast.parse(source)
    assert "provider_dispatched: bool = False" in source
    assert "execution_authorized: bool = False" in source
    assert not any(
        isinstance(node,ast.Call)
        and isinstance(node.func,ast.Attribute)
        and node.func.attr in {"execute","recover","complete","fail","release"}
        for node in ast.walk(tree)
    )


def test_recovery_settlement_calls_existing_recovery_only_not_execution():
    path=ROOT/"app/services/warehouse_sql_recovery_settlement_service.py"
    tree=ast.parse(path.read_text(encoding="utf-8"))
    calls=[
        node.func.id for node in ast.walk(tree)
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Name)
    ]
    assert "recover_governed_query" in calls
    assert "execute_governed_query" not in calls


def test_dispatch_audit_exists_and_blocks_unreviewed_live_queries():
    source=(ROOT/"docs/warehouse_query_security_review_413h.md").read_text(
        encoding="utf-8"
    )
    assert "NO-GO for live warehouse query dispatch" in source
    assert "Publication freshness not atomic" in source
    assert "No authenticated dispatch entry point" in source
    assert "does not change the existing execution configuration" in source
