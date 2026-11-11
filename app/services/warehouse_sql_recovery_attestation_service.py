"""414E2 backend-only lookup attestation. No submission, result fetch or settlement."""
from dataclasses import dataclass
from types import MappingProxyType

from app.db import warehouse_query_send_repository as repo
from app.services import warehouse_sql_send_recovery_service as recovery
from app.warehouse.query_recovery_attestation import (
    ExpectedJob,LookupPolicy,LookupRuntime,LookupOnlyClient,lookup_metadata,
)

# Trusted startup must provide reviewed runtimes; no env switch/client/credentials.
_RUNTIMES=MappingProxyType({})


class LookupUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class RecoveryAttestation(recovery.NonDispatch):
    revision:int
    observation:str
    verdict:str
    reason:str
    billing_complete:bool
    recovery:recovery.RecoveryView


def _runtime_for(project,location):
    runtime=_RUNTIMES.get((project,location))
    if (type(runtime) is not LookupRuntime or type(runtime.policy) is not LookupPolicy
            or type(runtime.transport) is not LookupOnlyClient
            or (runtime.policy.project,runtime.policy.location)!=(project,location)):
        raise LookupUnavailable('Reviewed lookup runtime unavailable')
    return runtime


def _snapshot(conn,c,lease,revision):
    r=repo.lock_identity(conn,c)
    if r['status']!='EXECUTING':raise repo.RecoveryConflict('RESERVATION_SETTLED_HELD')
    intent,event=repo.latest(conn,c['attempt_id']);repo.require_owner(conn,event,lease,revision)
    if (intent['reservation_id']!=r['reservation_id'] or intent['compiled_intent_sha256']!=c['compiled_intent_sha256']
            or intent['provider_job_id']!=c['provider_job_id']):
        raise repo.RecoveryConflict('IMMUTABLE_LOOKUP_CONTEXT_MISMATCH')
    tables=c['evidence'].get('physical_tables')
    if (type(tables) is not list or len(tables)!=1 or any(type(t) is not str or t.split('.')[0]!=intent['provider_project'] for t in tables)):
        raise repo.RecoveryConflict('IMMUTABLE_TABLE_SCOPE_REQUIRED')
    ceiling=min(r['reserved_bytes'],r['maximum_bytes_billed'],c['policy']['auto_execute_bytes'],c['policy']['hard_bytes'])
    return ExpectedJob(intent['provider_project'],intent['provider_location'],intent['provider_job_id'],
        c['workspace_id'],r['reservation_id'],c['compiled_intent_sha256'].strip(),c['compiled_sql_sha256'].strip(),
        tuple(tables),ceiling,int(r['execution_started_at'].timestamp()*1000))


def _persist(conn,attempt_id,revision,report,policy_sha):
    conn.execute("""INSERT INTO warehouse_query_recovery_attestations(attempt_id,journal_revision,
        observation,verdict,reason,policy_sha256,payload_sha256,bytes_processed,bytes_billed,billing_complete)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (attempt_id,revision,report.observation,report.verdict,report.reason,policy_sha,report.payload_sha256,
         report.bytes_processed,report.bytes_billed,report.billing_complete))


def lookup_and_attest(*,lease:recovery.RecoveryLease,expected_revision:int):
    """Only trusted backend leases; no caller response/provider/permit/SQL accepted."""
    recovery._validate_lease(lease)
    with recovery._worker(lease.attempt_id) as (conn,c):
        expected=_snapshot(conn,c,lease,expected_revision)
    # All locks/connections above are closed before the only external GET.
    runtime=_runtime_for(expected.project,expected.location)
    report=lookup_metadata(runtime,expected)
    policy_sha=runtime.policy.sha256
    with recovery._worker(lease.attempt_id) as (conn,c):
        if _snapshot(conn,c,lease,expected_revision)!=expected:
            raise repo.RecoveryConflict('LOOKUP_CONTEXT_CHANGED')
        result=recovery._record_observation_locked(conn,c,lease,expected_revision,
            recovery.JobIdentity(expected.project,expected.location,expected.job_id),recovery.Observation(report.observation))
        _persist(conn,c['attempt_id'],result.revision,report,policy_sha)
        return RecoveryAttestation(result.revision,report.observation,report.verdict,report.reason,report.billing_complete,result)
