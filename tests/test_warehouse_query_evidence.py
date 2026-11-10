"""Scoped PostgreSQL warehouse SQL readiness inspection, no provider calls."""
import pytest
from fastapi import HTTPException

from app.services import warehouse_query_evidence_service as evidence
from app.services import warehouse_model_service as models
from tests.test_warehouse_foundation import pg
from tests.test_warehouse_modeling import modeling_pg, configured, fact, dimension
from app.schemas.warehouse import ModelCreate


def test_approved_models_without_current_serving_receipts_block_sql(modeling_pg):
    pg=modeling_pg
    mid,vid,_,_=configured(pg, fact(), "Query evidence fact")
    models.approve_version(pg["workspace"],mid,vid,pg["owner"])
    result=evidence.snapshot(pg["workspace"],pg["owner"],[mid])
    assert result["workspace_id"]==pg["workspace"]
    assert result["models"]==[{
        "workspace_id":pg["workspace"],"model_id":mid,"model_version_id":vid,
        "status":"APPROVED","sources_current":True,
        "publication_current":False,"serving_receipt_current":False,
        "serving_verified":False,
    }]
    assert result["eligible"] is False
    assert not result["query_execution_enabled"]
    assert "WAREHOUSE_EXECUTION_DISABLED" in result["blocked_reasons"]
    assert "SERVING_NOT_VERIFIED" in result["blocked_reasons"]
    assert "PUBLICATION_NOT_CURRENT" in result["blocked_reasons"]
    assert result["provider_verification"]=="NOT_PERFORMED"


def test_draft_model_never_eligible(modeling_pg):
    pg=modeling_pg
    mid=models.create_model(pg["workspace"],pg["owner"],ModelCreate(display_name="Draft for SQL"))["warehouse_model_id"]
    response=evidence.snapshot(pg["workspace"],pg["owner"],[mid])
    assert response["models"][0]["status"]=="UNAPPROVED"
    assert not response["models"][0]["sources_current"]
    assert "UNAPPROVED_MODEL" in response["blocked_reasons"]


def test_foreign_owner_is_denied_before_evidence_lookup(modeling_pg,monkeypatch):
    pg=modeling_pg
    mid,vid,_,_=configured(pg,dimension(),"Foreign read forbidden")
    monkeypatch.setattr(evidence,"_model_rows",lambda *_:pytest.fail("Metadata read before access check"))
    with pytest.raises(HTTPException) as caught:
        evidence.snapshot(pg["workspace"],pg["other"],[mid])
    assert caught.value.status_code==403


def test_missing_model_and_scope_mismatch_are_not_leaked(modeling_pg):
    pg=modeling_pg
    mid,vid,_,_=configured(pg,fact(),"Scoped fact")
    with pytest.raises(HTTPException) as exc:
        evidence.snapshot(pg["workspace"],pg["owner"],[mid+100000])
    assert exc.value.status_code==404
    with pytest.raises(HTTPException) as exc:
        evidence.snapshot(pg["foreign_workspace"],pg["other"],[mid])
    assert exc.value.status_code==404


@pytest.mark.parametrize("ids",[[],[0],[True],[1,1],list(range(1,18)),["1"],None])
def test_invalid_selections_fail_before_database(ids,monkeypatch):
    monkeypatch.setenv("WAREHOUSE_ENABLED","true")
    monkeypatch.setenv("WAREHOUSE_PROVIDER","bigquery")
    monkeypatch.setenv("APP_ENV","development")
    monkeypatch.setattr(evidence,"repository_transaction",lambda:pytest.fail("Unexpected database access"))
    with pytest.raises(ValueError):
        evidence.snapshot(1,{"owner_key":"test"},ids)


def test_disabled_feature_never_touches_database(monkeypatch):
    monkeypatch.setenv("WAREHOUSE_ENABLED","false")
    monkeypatch.setattr(evidence,"repository_transaction",lambda:pytest.fail("Database accessed"))
    with pytest.raises(RuntimeError):
        evidence.snapshot(1,{"owner_key":"test"},[1])


def test_receipt_is_not_independent_serving_verification(modeling_pg,monkeypatch):
    pg=modeling_pg
    mid,vid,_,_=configured(pg,fact(),"Receipt diagnostic")
    models.approve_version(pg["workspace"],mid,vid,pg["owner"])
    monkeypatch.setattr(evidence,"_receipt_tables_available",lambda conn:True)
    monkeypatch.setattr(evidence,"_ledger_evidence",lambda *args:(True,True))
    result=evidence.snapshot(pg["workspace"],pg["owner"],[mid])
    assert result["models"][0]["publication_current"] is True
    assert result["models"][0]["serving_receipt_current"] is True
    assert result["models"][0]["serving_verified"] is False
    assert not result["eligible"]
    assert "SERVING_NOT_VERIFIED" in result["blocked_reasons"]
