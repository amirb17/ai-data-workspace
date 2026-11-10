"""414A: proposed gateway security design is explicitly non-dispatching.

These checks guard documentation and current integration limits, not proof of
production authorization or an atomic publication fence.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESIGN = ROOT / "docs/warehouse_query_dispatch_design_414a.md"
REVIEW = ROOT / "docs/warehouse_query_security_review_413h.md"


def test_review_and_design_remain_explicitly_no_go():
    design = DESIGN.read_text(encoding="utf-8")
    review = REVIEW.read_text(encoding="utf-8")
    assert "NO-GO" in review
    assert "NO-GO" in design
    assert "does not enable a BigQuery query" in design
    assert "atomic publication" in design


def test_design_disallows_untrusted_execution_capabilities():
    design = DESIGN.read_text(encoding="utf-8")
    for name in (
        "SQL", "physical tables", "provider clients", "request tokens",
        "execution permits", "client-controlled", "authenticated",
    ):
        assert name in design


def test_design_covers_retries_races_recovery_and_revocation():
    design = DESIGN.read_text(encoding="utf-8")
    for name in (
        "retry-safe", "publication revocation", "Lease expires",
        "Two concurrent workers", "permanently missing job",
        "recovery-only", "retention", "idempotently",
    ):
        assert name in design


def test_current_warehouse_claim_path_remains_hard_disabled():
    source = (
        ROOT / "app/services/warehouse_sql_claim_rehearsal_service.py"
    ).read_text(encoding="utf-8")
    assert "provider_dispatched: bool = False" in source
    assert "execution_authorized: bool = False" in source
    assert "provider.execute(" not in source
