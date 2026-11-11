"""414F4: persistent cancel intent -> fake cancel -> original jobs.get.

Uses disposable PostgreSQL, fake provider only. No live GCP operations.
"""
from google.api_core.exceptions import NotFound
import pytest

from app.services import warehouse_sql_cancel_intent_service as cancellation
from app.services import warehouse_sql_provider_policy_service as policy
from app.services import warehouse_sql_send_recovery_service as recovery
from app.warehouse.query_cancellation_transport import cancel_exact_job_once_after_intent
from tests.test_warehouse_cancel_intent_414f4 import (
    pg, modeling_pg, fact_provider_pg, genuinely_served_query,
    served, checkpointed, prepared, lookup, unbound, pinned_db, cancel_db,
    pin, scalar, send, cancel,
)
from tests.test_warehouse_query_cancellation_transport_414f4 import FakeCancel


@pytest.mark.parametrize('failure', [None, TimeoutError('lost acknowledgement')])
def test_cancel_attempt_requires_original_job_lookup_and_preserves_holds(cancel_db, failure):
    s, lease, client = cancel_db
    original = pin(s)
    sent = send(lease)
    durable = cancel(lease, sent.revision)
    fake = FakeCancel(failure)
    observation = cancel_exact_job_once_after_intent(
        project=lease.job.project, location=lease.job.location,
        job_id=durable.job_id, transport=fake,
    )
    assert len(fake.calls)==1 and observation.lookup_required
    assert not observation.cancellation_confirmed and not observation.settlement_authorized
    assert observation.job_id==original.job_id
    # jobs.get may still report successful completion despite cancel request.
    verified = policy.lookup_pinned_submission_without_resend(
        lease=lease, expected_revision=sent.revision,
    )
    assert verified.observation=='SUCCEEDED'
    assert verified.recovery.job.job_id==durable.job_id
    assert scalar(s,"SELECT COUNT(*) FROM warehouse_query_cancel_intents")==1
    assert scalar(s,"SELECT COUNT(*) FROM query_governance_usage")==0
    assert scalar(s,"SELECT warehouse_query_publication_dependency_present(%s,%s)",
                  (s.f.resource,s.pub['publication_id']))
    assert len(client.calls)==1


def test_cancel_api_acknowledgement_is_not_proof_of_provider_cancelled(cancel_db):
    s, lease, client=cancel_db
    pin(s)
    sent=send(lease)
    durable=cancel(lease,sent.revision)
    fake=FakeCancel()
    response=cancel_exact_job_once_after_intent(
        project=lease.job.project,location=lease.job.location,
        job_id=durable.job_id,transport=fake,
    )
    assert response.classification=='CANCEL_ACKNOWLEDGED_UNVERIFIED'
    client.body['status']['errorResult']={'reason':'stopped'}
    recovered=policy.lookup_pinned_submission_without_resend(
        lease=lease,expected_revision=sent.revision,
    )
    assert recovered.observation=='CANCELLED'
    assert recovered.verdict=='VERIFIED_METADATA'
    assert scalar(s,"SELECT COUNT(*) FROM query_governance_usage")==0
    assert scalar(s,"SELECT COUNT(*) FROM warehouse_query_cancel_intents")==1


def test_provider_lookup_absence_after_cancel_still_holds_reservation(cancel_db):
    s, lease, client=cancel_db
    pin(s)
    sent=send(lease)
    durable=cancel(lease,sent.revision)
    fake=FakeCancel()
    cancel_exact_job_once_after_intent(
        project=lease.job.project,location=lease.job.location,
        job_id=durable.job_id,transport=fake,
    )
    client.error=NotFound('ambiguous absence')
    recovered=policy.lookup_pinned_submission_without_resend(
        lease=lease,expected_revision=sent.revision,
    )
    assert recovered.observation=='NOT_FOUND' and recovered.verdict=='ABSENT'
    assert recovered.recovery.phase=='SEND_UNKNOWN'
    assert scalar(s,"SELECT status FROM query_governance_reservations WHERE reservation_id=%s",(s.r,))=='EXECUTING'
    assert scalar(s,"SELECT COUNT(*) FROM query_governance_usage")==0
    assert scalar(s,"SELECT COUNT(*) FROM warehouse_query_cancel_intents")==1
