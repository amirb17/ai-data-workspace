"""414F0-B8: evaluate B6 audit result from a real migrated disposable schema."""
from tests.test_warehouse_query_function_trigger_integration_414f0b7 import (
    pg, modeling_pg, fact_provider_pg, genuinely_served_query,
    served, checkpointed, prepared, lookup, unbound, linked_pg,
    reliability_pg, registered, intent, execute, collect_audit,
)
from app.security.warehouse_catalog_assessment import assess_catalog


def test_real_catalog_produces_non_authorizing_diagnostic(unbound):
    s, _, client = unbound
    with s.f.pg["connect"]() as conn:
        groups = collect_audit(conn)
        conn.rollback()
    result = assess_catalog(*groups)
    assert result.verdict == "NO_GO"
    assert result.permissions_approved is False
    assert result.functions_checked >= 14
    assert result.triggers_checked >= 20
    assert all(isinstance(b, str) for b in result.blockers)
    assert not client.calls
