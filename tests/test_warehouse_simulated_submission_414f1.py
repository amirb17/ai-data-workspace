"""414F1 disposable PG boundary + fake provider insert: never retry after uncertainty."""
from copy import deepcopy

import pytest

from app.db import warehouse_query_send_repository as repo
from app.services import warehouse_sql_send_recovery_service as recovery
from app.warehouse.query_job_request import build_reviewed_query_job_request
from app.warehouse.query_submission_transport import submit_exact_job_attempt_after_boundary
from app.warehouse.query_recovery_attestation import digest
from tests.test_warehouse_query_envelope_pinning_414f1 import (
    pg, modeling_pg, fact_provider_pg, genuinely_served_query,
    served, checkpointed, prepared, lookup, unbound, pinned_db,
    pin, scalar,
)
from tests.test_warehouse_query_submission_transport_414f1 import FakeInsert


def build_from_server(s):
    # The policy is deliberately sourced from the fixture database, not
    # user-supplied text or client-side request fields.
    with s.f.pg["connect"]() as conn:
        expected = conn.execute(
            "SELECT expected FROM warehouse_query_provider_policies WHERE attempt_id=%s",
            (s.a.attempt_id,),
        ).fetchone()[0]
        pinned = conn.execute(
            "SELECT request_sha256 FROM warehouse_query_request_envelopes WHERE attempt_id=%s",
            (s.a.attempt_id,),
        ).fetchone()[0]
    job = build_reviewed_query_job_request(expected=expected, compiled_sql=s.q.sql)
    assert digest(job.body) == pinned
    return job


@pytest.mark.parametrize("failure", [None, TimeoutError("lost provider ack")])
def test_postgres_boundary_then_one_fake_insert_and_lookup_only(pinned_db, failure):
    s, lease, _ = pinned_db
    pin(s)
    job = build_from_server(s)
    boundary = recovery.record_pinned_send_boundary_without_dispatch(
        lease=lease, expected_revision=lease.revision,
    )
    assert boundary.phase == "SEND_UNKNOWN"
    transport = FakeInsert(failure)
    outcome = submit_exact_job_attempt_after_boundary(request=job, transport=transport)
    assert len(transport.calls) == 1
    assert outcome.lookup_required and not outcome.verified
    assert outcome.job_id == boundary.job.job_id
    assert recovery.inspect_recovery(attempt_id=s.a.attempt_id).phase == "SEND_UNKNOWN"
    with pytest.raises(repo.RecoveryConflict):
        recovery.record_pinned_send_boundary_without_dispatch(
            lease=lease, expected_revision=lease.revision,
        )
    assert scalar(
        s, "SELECT COUNT(*) FROM warehouse_query_send_journal WHERE event='SEND_BOUNDARY'"
    ) == 1
    assert scalar(s, "SELECT COUNT(*) FROM query_governance_usage") == 0
    assert scalar(s, "SELECT COUNT(*) FROM warehouse_query_request_envelopes") == 1
