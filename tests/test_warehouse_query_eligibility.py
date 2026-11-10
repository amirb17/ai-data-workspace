"""Pure governed-query readiness guard tests, no provider/database needed."""
import pytest
from app.warehouse.query_eligibility import BlockReason, assess_eligibility


def model(**overrides):
    record={
        "workspace_id":9, "model_id":11, "status":"APPROVED",
        "sources_current":True,
        "serving_verified":True,
        "publication_current":True,
    }
    record.update(overrides)
    return record


def test_current_verified_pins_are_necessary_but_not_sufficient():
    ready=assess_eligibility(workspace_id=9,models=[model()],execution_enabled=True)
    assert ready.allowed
    assert ready.reasons == ()
    assert ready.model_ids == (11,)
    assert ready.workspace_id == 9


def test_execution_disabled_blocks_healthy_models():
    result=assess_eligibility(workspace_id=9,models=[model()],execution_enabled=False)
    assert not result.allowed
    assert result.reasons==(BlockReason.WAREHOUSE_EXECUTION_DISABLED,)


@pytest.mark.parametrize(("change","reason"),[
    ({"workspace_id":8},BlockReason.UNAPPROVED_MODEL),
    ({"status":"DRAFT"},BlockReason.UNAPPROVED_MODEL),
    ({"sources_current":False},BlockReason.SOURCE_NOT_CURRENT),
    ({"sources_current":None},BlockReason.SOURCE_NOT_CURRENT),
    ({"serving_verified":False},BlockReason.SERVING_NOT_VERIFIED),
    ({"serving_verified":None},BlockReason.SERVING_NOT_VERIFIED),
    ({"publication_current":False},BlockReason.PUBLICATION_NOT_CURRENT),
    ({"publication_current":None},BlockReason.PUBLICATION_NOT_CURRENT),
])
def test_missing_or_stale_required_evidence_blocks(change,reason):
    result=assess_eligibility(workspace_id=9,models=[model(**change)],execution_enabled=True)
    assert not result.allowed and reason in result.reasons


def test_each_selected_model_must_be_ready():
    second=model(model_id=12,serving_verified=False)
    result=assess_eligibility(workspace_id=9,models=[model(),second],execution_enabled=True)
    assert not result.allowed
    assert result.model_ids==(11,12)
    assert result.reasons==(BlockReason.SERVING_NOT_VERIFIED,)


def test_multiple_blockers_and_empty_input():
    result=assess_eligibility(workspace_id=9,models=[],execution_enabled=False)
    assert not result.allowed
    assert BlockReason.NO_MODELS in result.reasons
    assert BlockReason.WAREHOUSE_EXECUTION_DISABLED in result.reasons


@pytest.mark.parametrize("models",[
    [model(model_id=0)],
    [model(model_id=True)],
    [model(),model()],
    [{"model_id":11}],
    [None],
])
def test_invalid_models_do_not_bypass_policy(models):
    if models==[{"model_id":11}]:
        result=assess_eligibility(workspace_id=9,models=models,execution_enabled=True)
        assert not result.allowed
    else:
        with pytest.raises(ValueError):
            assess_eligibility(workspace_id=9,models=models,execution_enabled=True)


def test_existing_catalog_cannot_accidentally_enable_querying():
    # Catalog exposes neither serving verification nor a publication pin.
    existing={"model_id":11,"status":"APPROVED","resource_registered":True,
              "serving_status":"NOT_VERIFIED","queryable":False}
    result=assess_eligibility(workspace_id=9,models=[existing],execution_enabled=False)
    assert not result.allowed
    assert BlockReason.SERVING_NOT_VERIFIED in result.reasons
    assert BlockReason.PUBLICATION_NOT_CURRENT in result.reasons


def test_no_provider_or_network_imports():
    import ast
    from pathlib import Path
    file=Path(__file__).parents[1]/"app/warehouse/query_eligibility.py"
    tree=ast.parse(file.read_text(encoding="utf-8"))
    imports=[alias.name for item in ast.walk(tree)
             if isinstance(item,ast.Import) for alias in item.names]
    assert not any(name.startswith(("google","boto3","psycopg","duckdb"))
                   for name in imports)
