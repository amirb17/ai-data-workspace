"""414E1 metadata-only send journal. No provider IO, permits or settlement.

Worker operations are trusted backend bookkeeping, NOT user authorization APIs.
Observations are unverified reports: none authorize results, send or hold release.
"""
from contextlib import contextmanager
from dataclasses import dataclass,field,replace
from datetime import datetime
from enum import StrEnum
import hashlib
import re
from uuid import UUID,uuid4

from fastapi import HTTPException,Request
from app.auth.config import require_secret
from app.auth.repository import principal
from app.db import warehouse_query_attempt_repository as attempts
from app.db import warehouse_query_send_repository as repo
from app.db import query_governance_repository as budgets
from app.query_governance.policy import QueryPolicy
from app.services.query_governance_service import QueryGovernanceService
from app.services.warehouse_sql_dispatch_checkpoint_service import _session_locked,_budget_locked
from app.services.warehouse_sql_trusted_recompilation_service import _recompile_in_transaction
from app.services.warehouse_sql_recovery_settlement_service import _expected_job_id


class Observation(StrEnum):
    NOT_FOUND='NOT_FOUND'
    UNCERTAIN='UNCERTAIN'
    IN_PROGRESS='IN_PROGRESS'
    SUCCEEDED='SUCCEEDED'
    FAILED='FAILED'
    CANCELLED='CANCELLED'


class NonDispatch:
    @property
    def execution_authorized(self):return False
    @property
    def provider_dispatched(self):return False


@dataclass(frozen=True)
class JobIdentity:
    project:str
    location:str
    job_id:str


@dataclass(frozen=True)
class RecoveryView(NonDispatch):
    attempt_id:int
    revision:int
    phase:str
    classification:str
    revoked:bool
    expired:bool
    job:JobIdentity


@dataclass(frozen=True)
class RecoveryLease(NonDispatch):
    attempt_id:int
    revision:int
    generation:int
    token:UUID=field(repr=False)
    lease_until:datetime
    job:JobIdentity


@dataclass(frozen=True)
class IntentPreparation(NonDispatch):
    attempt_handle:str
    revision:int
    classification:str


def _identity(intent):
    return JobIdentity(intent['provider_project'],intent['provider_location'],intent['provider_job_id'])


def _view(conn,c,r,intent,event):
    revoked=conn.execute('SELECT EXISTS(SELECT 1 FROM warehouse_query_dispatch_revocations WHERE attempt_id=%s)',(c['attempt_id'],)).fetchone()[0]
    expired=conn.execute('SELECT %s<=clock_timestamp()',(c['valid_until'],)).fetchone()[0]
    if r['status']!='EXECUTING':decision='SETTLED_HELD'
    elif revoked:decision='REVOKED_HELD'
    elif expired:decision='EXPIRED_HELD'
    elif event['phase'].endswith('_OBSERVED'):decision='TERMINAL_OBSERVED_HELD'
    elif event['observation']=='NOT_FOUND':decision='NOT_FOUND_HELD'
    elif event['observation']=='UNCERTAIN':decision='LOOKUP_RETRY'
    elif event['phase']=='PROVIDER_RUNNING':decision='LOOKUP_LATER'
    elif event['phase']=='SEND_UNKNOWN':decision='LOOKUP_REQUIRED'
    else:decision='NO_DISPATCH_REVIEW_REQUIRED'
    return RecoveryView(c['attempt_id'],event['revision'],event['phase'],decision,revoked,expired,_identity(intent))


def _live_context(conn,c):
    """Freeze and revalidate original checkpoint context, not a passed principal."""
    u=repo.one(conn,'SELECT * FROM users WHERE user_id=%s FOR SHARE',(c['user_id'],))
    s=repo.one(conn,"""SELECT *,LEAST(expires_at,last_seen_at+interval '30 minutes') AS session_until
        FROM auth_sessions WHERE token_digest=%s FOR SHARE""",(c['session_digest'],))
    w=repo.one(conn,'SELECT owner,status FROM workspaces WHERE workspace_id=%s FOR SHARE',(c['workspace_id'],))
    if (not u or u['account_status']!='ACTIVE' or u['owner_key']!=c['owner_key'] or not s
            or s['user_id']!=u['user_id'] or s['revoked_at'] is not None or not w
            or w['owner']!=c['owner_key'] or w['status']!='ACTIVE'):
        raise repo.RecoveryConflict('CHECKPOINT_REVOKED_LOOKUP_ONLY')
    r=repo.lock_identity(conn,c)
    if (r['status']!='EXECUTING' or not conn.execute('SELECT LEAST(%s,%s,%s)>clock_timestamp()',
            (c['valid_until'],s['session_until'],r['expires_at'])).fetchone()[0]
            or conn.execute('SELECT EXISTS(SELECT 1 FROM warehouse_query_dispatch_revocations WHERE attempt_id=%s)',(c['attempt_id'],)).fetchone()[0]):
        raise repo.RecoveryConflict('CHECKPOINT_EXPIRED_OR_REVOKED_LOOKUP_ONLY')
    resource=repo.one(conn,"""SELECT r.*,sm.serving_materialization_id FROM warehouse_query_attempt_publication_pins p
        JOIN warehouse_resources r USING(resource_id)
        JOIN warehouse_publication_heads h ON h.resource_id=p.resource_id AND h.publication_id=p.publication_id
          AND h.revision=p.publication_revision
        JOIN warehouse_serving_materializations sm ON sm.resource_id=p.resource_id AND sm.publication_id=p.publication_id
          AND sm.publication_revision=p.publication_revision
        WHERE p.attempt_id=%s FOR SHARE OF p,r,h,sm""",(c['attempt_id'],))
    if not resource:raise repo.RecoveryConflict('CURRENT_SERVING_PUBLICATION_REQUIRED')
    draft=repo.one(conn,'SELECT * FROM warehouse_query_attempt_logical_drafts WHERE attempt_id=%s FOR SHARE',(c['attempt_id'],))
    if not draft:raise repo.RecoveryConflict('STORED_LOGICAL_DRAFT_REQUIRED')
    conn.execute("""SELECT s.source_mapping_id FROM warehouse_model_sources s JOIN datasets d ON d.dataset_id=s.source_dataset_id
        WHERE s.warehouse_model_version_id=%s ORDER BY d.dataset_id FOR SHARE OF s,d""",(draft['warehouse_model_version_id'],)).fetchall()
    policy=QueryPolicy.from_environment()
    g=QueryGovernanceService(c['workspace_id'],principal(u),policy=policy)
    compiled,q=_recompile_in_transaction(conn,g,conn.execute('SELECT btrim(attempt_handle) FROM warehouse_query_attempt_registry WHERE attempt_id=%s',(c['attempt_id'],)).fetchone()[0])
    q=replace(q,request_token=c['request_token'].strip())
    if (compiled.reservation_id!=c['reservation_id'] or q.intent_sha256!=c['compiled_intent_sha256']
            or hashlib.sha256(q.sql.encode()).hexdigest()!=c['compiled_sql_sha256']
            or _expected_job_id(q,c['reservation_id'])!=c['provider_job_id']):
        raise repo.RecoveryConflict('CHECKPOINT_COMPILED_IDENTITY_MISMATCH')
    _budget_locked(conn,c['workspace_id'],r,draft['row_limit'],policy)
    return r,q,resource


def prepare_authenticated_send_intent(*,request:Request,workspace_id:int,attempt_handle:str):
    """Idempotent preparation only. Never claim/re-admit an EXECUTING query."""
    if (not isinstance(request,Request) or type(workspace_id) is not int or workspace_id<1
            or type(attempt_handle) is not str or not re.fullmatch('[a-f0-9]{32}',attempt_handle)):
        raise ValueError('Verified session and server attempt handle required')
    try:settings=require_secret()
    except ValueError as exc:raise HTTPException(503,'Warehouse session unavailable') from exc
    if settings.mode!='session':raise HTTPException(503,'Warehouse session required')
    token=request.cookies.get(settings.session_cookie)
    if not token or len(token)>128:raise HTTPException(401,'Sign in to continue.')
    with repo.transaction(workspace_id) as conn:
        user,_=_session_locked(conn,token)
        w=repo.one(conn,'SELECT owner,status FROM workspaces WHERE workspace_id=%s FOR SHARE',(workspace_id,))
        if not w or w['owner']!=user['owner_key'] or w['status']!='ACTIVE':raise HTTPException(403,'Workspace unavailable')
        a=attempts.read_attempt(conn,workspace_id=workspace_id,owner_key=user['owner_key'],attempt_handle=attempt_handle)
        c=repo.checkpoint(conn,a['attempt_id'])
        r=repo.lock_identity(conn,c)
        existing=repo.one(conn,'SELECT 1 FROM warehouse_query_send_intents WHERE attempt_id=%s',(a['attempt_id'],))
        if existing:
            intent,event=repo.latest(conn,a['attempt_id'])
        else:
            r,q,resource=_live_context(conn,c)
            conn.execute("""INSERT INTO warehouse_query_send_intents(attempt_id,reservation_id,provider_project,
                provider_location,provider_job_id,compiled_intent_sha256) VALUES(%s,%s,%s,%s,%s,%s)""",
                (c['attempt_id'],c['reservation_id'],q.physical_tables[0].split('.')[0],resource['location'],c['provider_job_id'],q.intent_sha256))
            repo.append(conn,c['attempt_id'],None,'CREATED','INTENT_RECORDED')
            intent,event=repo.latest(conn,c['attempt_id'])
        v=_view(conn,c,r,intent,event)
        return IntentPreparation(attempt_handle,v.revision,v.classification)


@contextmanager
def _worker(attempt_id,*,duty='recovery_attestation'):
    if type(attempt_id) is not int or attempt_id<1:raise ValueError('Server attempt ID required')
    with repo.transaction(duty=duty) as conn:
        c=repo.checkpoint(conn,attempt_id)
        budgets._lock_scopes(conn,c['workspace_id'])
        yield conn,c


def inspect_recovery(*,attempt_id:int):
    """Trusted backend inspection, including revoked accounts; NOT a public API."""
    with _worker(attempt_id) as (conn,c):
        # The worker transaction holds the global query-governance advisory
        # lock. Inspection needs no row UPDATE rights on immutable metadata.
        r=repo.lock_identity(conn,c,lock=False)
        intent,event=repo.latest(conn,attempt_id,lock=False)
        return _view(conn,c,r,intent,event)


def acquire_recovery(*,attempt_id:int,expected_revision:int,lease_seconds:int=30):
    if type(lease_seconds) is not int or not 1<=lease_seconds<=120:raise ValueError('Bounded recovery lease required')
    with _worker(attempt_id) as (conn,c):
        # Competing acquisitions serialize on the same global advisory
        # transaction lock; a unique append-only journal revision fences the
        # winner. No UPDATE permission on identity or send intent is needed.
        r=repo.lock_identity(conn,c,lock=False)
        if r['status']!='EXECUTING':raise repo.RecoveryConflict('RESERVATION_SETTLED_HELD')
        intent,event=repo.latest(conn,attempt_id,lock=False)
        repo.require_revision(event,expected_revision)
        if event['lease_until'] is not None and conn.execute('SELECT %s>clock_timestamp()',(event['lease_until'],)).fetchone()[0]:
            raise repo.RecoveryConflict('RECOVERY_BUSY')
        token=uuid4();until=conn.execute('SELECT clock_timestamp()+make_interval(secs=>%s)',(lease_seconds,)).fetchone()[0]
        updated=repo.append(conn,attempt_id,event,'LEASE_ACQUIRED',event['phase'],token=token,until=until)
        return RecoveryLease(attempt_id,updated['revision'],updated['lease_generation'],token,until,_identity(intent))


def _validate_lease(lease):
    if (type(lease) is not RecoveryLease or type(lease.token) is not UUID
            or type(lease.generation) is not int or lease.generation<1):
        raise ValueError('Internal recovery lease required')


def record_send_boundary(*,lease:RecoveryLease,expected_revision:int):
    """Write conservative possible-send marker; DOES NOT permit or send anything."""
    _validate_lease(lease)
    with _worker(lease.attempt_id,duty='provider_submission') as (conn,c):
        r,_,_=_live_context(conn,c)
        intent,event=repo.latest(conn,lease.attempt_id);repo.require_owner(conn,event,lease,expected_revision)
        if event['phase']!='INTENT_RECORDED':raise repo.RecoveryConflict('BOUNDARY_ALREADY_RECORDED_LOOKUP_ONLY')
        updated=repo.append(conn,lease.attempt_id,event,'SEND_BOUNDARY','SEND_UNKNOWN')
        return _view(conn,c,r,intent,updated)


def record_observation(*,lease:RecoveryLease,expected_revision:int,job:JobIdentity,observation:Observation):
    """Unsettled diagnostic report only. No results, provider, SQL or proof flag."""
    _validate_lease(lease)
    if type(job) is not JobIdentity or type(observation) is not Observation:raise ValueError('Typed job observation required')
    with _worker(lease.attempt_id) as (conn,c):
        return _record_observation_locked(conn,c,lease,expected_revision,job,observation)


def _record_observation_locked(conn,c,lease,expected_revision,job,observation):
    """Internal transaction composition; E2 atomically adds separate attestation."""
    r=repo.lock_identity(conn,c)
    if r['status']!='EXECUTING':raise repo.RecoveryConflict('RESERVATION_SETTLED_HELD')
    intent,event=repo.latest(conn,lease.attempt_id);repo.require_owner(conn,event,lease,expected_revision)
    if job!=_identity(intent):raise repo.RecoveryConflict('PROVIDER_JOB_SCOPE_MISMATCH')
    phase={'IN_PROGRESS':'PROVIDER_RUNNING','SUCCEEDED':'SUCCEEDED_OBSERVED','FAILED':'FAILED_OBSERVED','CANCELLED':'CANCELLED_OBSERVED'}.get(observation.value,event['phase'])
    updated=repo.append(conn,lease.attempt_id,event,'OBSERVATION',phase,observation=observation.value)
    return _view(conn,c,r,intent,updated)


def record_pinned_send_boundary_without_dispatch(
    *, lease: RecoveryLease, expected_revision: int,
) -> RecoveryView:
    """Atomically fence the exact immutable request before possible provider IO.

    The returned state is LOOKUP_REQUIRED; this function never returns SQL or
    the provider request, performs no IO, and is never an execution permit.
    Future transport must recheck a separately approved runtime release gate.
    """
    _validate_lease(lease)
    with _worker(lease.attempt_id,duty='provider_submission') as (conn, checkpoint):
        reservation, _, _ = _live_context(conn, checkpoint)
        intent, event = repo.latest(conn, lease.attempt_id)
        repo.require_owner(conn, event, lease, expected_revision)
        if event['phase'] != 'INTENT_RECORDED':
            raise repo.RecoveryConflict('BOUNDARY_ALREADY_RECORDED_LOOKUP_ONLY')
        pinned = repo.one(
            conn,
            """SELECT e.provider_job_id,e.request_sha256,e.policy_sha256,
                      p.expected_sha256,p.expected
               FROM warehouse_query_request_envelopes e
               JOIN warehouse_query_provider_policies p USING(attempt_id)
               WHERE e.attempt_id=%s""",
            (lease.attempt_id,),
        )
        if (not pinned
                or pinned['provider_job_id'] != intent['provider_job_id']
                or pinned['provider_job_id'] != checkpoint['provider_job_id']
                or pinned['policy_sha256'] != pinned['expected_sha256']
                or pinned['expected'].get('job_id') != intent['provider_job_id']
                or type(pinned['request_sha256']) is not str
                or len(pinned['request_sha256'].strip()) != 64):
            raise repo.RecoveryConflict('EXACT_PINNED_REQUEST_REQUIRED')
        # The send-intent row is held FOR UPDATE. This marker commits exactly
        # once and forces subsequent workers into lookup-only recovery.
        updated = repo.append(
            conn, lease.attempt_id, event, 'SEND_BOUNDARY', 'SEND_UNKNOWN',
        )
        return _view(conn, checkpoint, reservation, intent, updated)
