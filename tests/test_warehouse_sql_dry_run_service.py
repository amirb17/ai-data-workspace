"""Step 413A: dry-run admission separation and per-attempt token identity."""
from dataclasses import replace
import ast
from pathlib import Path

import pytest

from app.query_governance.types import QueryEstimate, EstimateConfidence
from app.services.warehouse_sql_compiled_evidence_service import WarehouseCompiledEvidence
from app.services.warehouse_sql_dry_run_service import (
    prepare_warehouse_query_attempt, estimate_warehouse_query_attempt,
)
from tests.test_warehouse_sql_single_model_compiler import compile_with


def template():
    return compile_with()


def evidence():
    return WarehouseCompiledEvidence(workspace_id=7,model_count=1)


class DryProvider:
    identity="BIGQUERY"
    def __init__(self, estimate=None):
        self.estimate_value=estimate if estimate is not None else QueryEstimate(10*1024*1024)
        self.calls=[]
    def estimate(self, query):
        self.calls.append(query)
        return self.estimate_value
    def execute(self,*args,**kwargs):
        pytest.fail("Dry-run-only step attempted provider execution")
    def recover(self,*args,**kwargs):
        pytest.fail("Dry-run-only step attempted job recovery")


def test_server_issued_attempt_preserves_compiled_intent_and_sql():
    original=template()
    attempt=prepare_warehouse_query_attempt(
        template=original,evidence=evidence(),
        random_token=lambda:"a"*64,
    )
    assert attempt.sql==original.sql
    assert attempt.physical_tables==original.physical_tables
    assert attempt.intent_sha256==original.intent_sha256
    assert attempt.request_token=="a"*64
    assert original.request_token!=attempt.request_token


def test_two_real_random_attempts_cannot_reuse_deterministic_template_token():
    original=template()
    first=prepare_warehouse_query_attempt(template=original,evidence=evidence())
    second=prepare_warehouse_query_attempt(template=original,evidence=evidence())
    assert first.request_token!=second.request_token
    assert first.request_token!=original.request_token
    assert second.request_token!=original.request_token
    assert first.intent_sha256==second.intent_sha256==original.intent_sha256
    assert first.sql==second.sql


def test_dry_run_only_estimate_uses_distinct_attempt_and_never_claims():
    provider=DryProvider()
    result=estimate_warehouse_query_attempt(
        template=template(),evidence=evidence(),provider=provider,
    )
    assert len(provider.calls)==1
    assert provider.calls[0]==result.query
    assert result.query.request_token!=template().request_token
    assert result.estimate.bytes_processed==10*1024*1024
    assert result.admission_created is False
    assert result.execution_authorized is False


@pytest.mark.parametrize("revised",[
    lambda e:replace(e,workspace_id=8),
    lambda e:replace(e,model_count=2),
    lambda e:replace(e,table_metadata_verified=False),
    lambda e:replace(e,current_publication_checked_after_metadata=False),
    lambda e:replace(e,query_execution_authorized=True),
    lambda e:replace(e,row_contents_verified=True),
])
def test_invalid_or_unrelated_evidence_fails_before_provider(revised):
    provider=DryProvider()
    with pytest.raises(ValueError):
        estimate_warehouse_query_attempt(
            template=template(),evidence=revised(evidence()),provider=provider,
        )
    assert provider.calls==[]


@pytest.mark.parametrize("token",[
    "not-a-token","F"*64,"g"*64,"0"*63,
    template().request_token,template().intent_sha256,
])
def test_bad_or_reused_attempt_token_fails_closed(token):
    with pytest.raises(ValueError):
        prepare_warehouse_query_attempt(
            template=template(),evidence=evidence(),random_token=lambda:token,
        )


@pytest.mark.parametrize("invalid",[
    QueryEstimate(None,EstimateConfidence.UNKNOWN),
    QueryEstimate(100,EstimateConfidence.LOWER_BOUND),
    QueryEstimate(0,EstimateConfidence.EXACT),
])
def test_uncertain_lower_bound_or_zero_byte_dry_run_is_not_admissible(invalid):
    with pytest.raises(ValueError):
        estimate_warehouse_query_attempt(
            template=template(),evidence=evidence(),
            provider=DryProvider(invalid),
        )


def test_wrong_provider_is_rejected_before_estimation():
    provider=DryProvider()
    provider.identity="DUCKDB"
    with pytest.raises(ValueError):
        estimate_warehouse_query_attempt(
            template=template(),evidence=evidence(),provider=provider,
        )
    assert provider.calls==[]


def test_source_has_no_reservation_claim_or_provider_execution():
    path=Path(__file__).parents[1]/"app/services/warehouse_sql_dry_run_service.py"
    tree=ast.parse(path.read_text(encoding="utf-8"))
    methods=[x.attr for x in ast.walk(tree) if isinstance(x,ast.Attribute)]
    assert not any(value in methods for value in (
        "execute","recover","admit","reserve","claim","release","complete",
    ))
    imports=[node.module for node in ast.walk(tree) if isinstance(node,ast.ImportFrom)]
    assert not any(value and value.startswith(("app.db","psycopg","google"))
                   for value in imports)
