"""414D4d strict server reconstruction: fail closed in isolated PostgreSQL."""
from pathlib import Path

import pytest

from app.services.warehouse_sql_trusted_recompilation_service import (
    recompile_stored_warehouse_attempt_without_dispatch as recompile,
)
from tests.test_warehouse_foundation import pg
from tests.test_warehouse_modeling import modeling_pg
from tests.test_warehouse_reliability import reliability_pg, registered, intent, execute
from tests.test_warehouse_query_reservation_publication_links_postgres import linked_pg
from tests.test_warehouse_sql_stored_publication_evidence_414d4c import (
    evidence_pg, insert_receipt,
)
from tests.test_warehouse_sql_admission_postgres import service

ROOT=Path(__file__).resolve().parents[1]


def test_legacy_fixture_physical_reference_drift_fails_closed(evidence_pg):
    f,publication,governance,attempt,reservation=evidence_pg
    with pytest.raises(ValueError,match="WAREHOUSE_RESOURCE_DRIFT"):
        recompile(governance=governance,attempt_handle=attempt.attempt_handle)
    assert governance.reservation(reservation)["status"]=="ACTIVE"


def test_valid_generated_resource_name_still_requires_serving_receipt(evidence_pg):
    # The historical reliability fixture intentionally registers a generic
    # synthetic_dataset/synthetic_generation_1 reference, which fails the
    # production naming contract BEFORE receipt validation. Test the
    # missing-receipt branch separately using an in-memory corrected row.
    # This does not mutate PostgreSQL or bypass service authorization.
    from app.db import warehouse_repository
    from app.services.metric_warehouse_binding_service import (
        _publication_state_id, _source,
    )

    f, publication, governance, attempt, reservation = evidence_pg
    with f.pg["connect"]() as conn:
        rows = warehouse_repository.metric_warehouse_bindings(
            conn, f.pg["workspace"], [f.pg["dataset"]],
            environment="development",
        )
    assert len(rows) == 1
    row = dict(rows[0])
    assert row["serving_materialization_id"] is None
    row["physical_reference"] = {
        "reference_version": 1,
        "project": "synthetic-project",
        "dataset": f"dr_dev_w{f.pg['workspace']}",
        "table": (
            f"m{f.model}_v{f.version}_g{row['generation']}"
        ),
    }
    with pytest.raises(ValueError, match="WAREHOUSE_SERVING_NOT_READY"):
        _source(row, f.pg["workspace"], _publication_state_id(row))
    assert governance.reservation(reservation)["status"] == "ACTIVE"


def test_fabricated_receipt_hashes_do_not_pass_production_resolver(evidence_pg):
    f,publication,governance,attempt,reservation=evidence_pg
    insert_receipt(f,publication)
    with pytest.raises(ValueError,match=(
        "WAREHOUSE_SERVING_EVIDENCE_INVALID|WAREHOUSE_RESOURCE_DRIFT"
    )):
        recompile(governance=governance,attempt_handle=attempt.attempt_handle)
    assert governance.reservation(reservation)["status"]=="ACTIVE"


def test_released_reservation_cannot_recompile(evidence_pg):
    f,publication,governance,attempt,reservation=evidence_pg
    governance.release(reservation)
    with pytest.raises(ValueError,match="WAREHOUSE_QUERY_DEPENDENCY_NOT_READY"):
        recompile(governance=governance,attempt_handle=attempt.attempt_handle)


def test_foreign_owner_cannot_resolve_physical_source(evidence_pg):
    f,publication,governance,attempt,reservation=evidence_pg
    from app.db.query_governance_repository import ReservationConflict
    with pytest.raises(ReservationConflict,match="WORKSPACE_NOT_OWNED"):
        recompile(governance=service(f.pg,owner=f.pg["other"]),
                  attempt_handle=attempt.attempt_handle)


def test_recompilation_is_internal_and_non_dispatching():
    src=(ROOT/"app/services/warehouse_sql_trusted_recompilation_service.py"
         ).read_text(encoding="utf-8")
    for clause in (
        "metric_warehouse_bindings",
        "_publication_state_id(row)",
        "_source(row,governance.workspace_id,state_id)",
        "bind_draft(draft,fact=source)",
        "compile_single_model_sql(",
        "WAREHOUSE_COMPILED_RESERVATION_INTENT_MISMATCH",
        "execution_authorized: bool = False",
    ):
        assert clause in src
    for forbidden in ("provider.execute(","governance.claim(","governance.admit(",
                      "query_and_wait(","client.query("):
        assert forbidden not in src
