"""414F4 fake cancellation transport; never a proof of job termination."""
import pytest
from app.warehouse.query_cancellation_transport import (
    cancel_exact_job_once_after_intent, CancelAttemptObservation,
)

PROJECT='synthetic-project'
JOB='dr_query_w5_r7_'+'a'*32


class FakeCancel:
    def __init__(self, error=None):
        self.error=error
        self.calls=[]
    def cancel_job_once(self, *, project, location, job_id):
        self.calls.append((project,location,job_id))
        if self.error: raise self.error
        return {'jobReference':{'jobId':job_id},'job':{'status':{'state':'DONE'}}}


def call(client):
    return cancel_exact_job_once_after_intent(
        project=PROJECT, location='asia-south1', job_id=JOB, transport=client,
    )


def test_cancel_acknowledgement_is_not_terminal_verification_or_billing_permission():
    fake=FakeCancel()
    result=call(fake)
    assert result==CancelAttemptObservation(JOB,'CANCEL_ACKNOWLEDGED_UNVERIFIED')
    assert result.lookup_required and not result.cancellation_confirmed
    assert not result.settlement_authorized and not result.provider_dispatched
    assert fake.calls==[(PROJECT,'asia-south1',JOB)]


@pytest.mark.parametrize('error', [
    TimeoutError('private response'),
    ConnectionError('lost connection'),
    RuntimeError('job no longer active'),
    PermissionError('denied'),
])
def test_ambiguous_provider_result_does_not_retry_or_leak_errors(error):
    fake=FakeCancel(error)
    result=call(fake)
    assert result.classification=='CANCEL_OUTCOME_UNKNOWN'
    assert result.lookup_required and not result.cancellation_confirmed
    assert len(fake.calls)==1
    assert 'private response' not in str(result)


@pytest.mark.parametrize('overrides',[
    {'project':'foreign'},
    {'location':'US'},
    {'job_id':'other'},
    {'job_id':'dr_query_w0_r7_'+'a'*32},
])
def test_unreviewed_identity_rejected_before_transport(overrides):
    fake=FakeCancel()
    kwargs={'project':PROJECT,'location':'asia-south1','job_id':JOB,'transport':fake}
    kwargs.update(overrides)
    with pytest.raises(ValueError,match='SCOPE'):
        cancel_exact_job_once_after_intent(**kwargs)
    assert not fake.calls


def test_no_default_credentials_or_implicit_cancel_client():
    with pytest.raises(TypeError):
        cancel_exact_job_once_after_intent(project=PROJECT,location='asia-south1',job_id=JOB,transport=object())
