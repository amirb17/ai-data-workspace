"""414E3 authenticated expected policy and backend-only, non-dispatch verification."""
from dataclasses import asdict, dataclass
import re
from types import MappingProxyType

from fastapi import HTTPException, Request
from psycopg.types.json import Jsonb
from psycopg.rows import dict_row

from app.auth.config import require_secret
from app.db import warehouse_query_attempt_repository as attempts
from app.db import warehouse_query_send_repository as repo
from app.query_governance.policy import QueryPolicy
from app.services import warehouse_sql_send_recovery_service as recovery
from app.services import warehouse_sql_recovery_attestation_service as attestation
from app.services.warehouse_sql_dispatch_checkpoint_service import _session_locked
from app.warehouse.query_provider_policy import ProviderPolicy, verify_exact_metadata
from app.warehouse.query_recovery_attestation import ExpectedJob, digest, _lookup_metadata
from app.warehouse.query_job_request import build_reviewed_query_job_request

# Workspace bindings are trusted startup configuration, never request parameters.
_POLICIES = MappingProxyType({})


class PolicyConflict(ValueError):
    pass


@dataclass(frozen=True)
class PolicyPreparation(recovery.NonDispatch):
    attempt_handle: str
    classification: str = 'POLICY_PINNED_NO_DISPATCH'


def _runtime(workspace):
    policy = _POLICIES.get(workspace)
    if type(policy) is not ProviderPolicy:
        raise attestation.LookupUnavailable('Reviewed workspace provider policy unavailable')
    runtime = attestation._runtime_for(policy.project, policy.location)
    client = runtime.transport._client
    if (getattr(client, 'project', None), getattr(client, 'location', None)) != (policy.project, policy.location):
        raise PolicyConflict('SERVER_CLIENT_SCOPE_MISMATCH')
    lookup = runtime.policy
    if (lookup.creator_email != policy.creator_email
            or lookup.principal_subject != policy.principal_subject
            or lookup.maximum_timeout_ms != policy.timeout_ms):
        raise PolicyConflict('SERVER_LOOKUP_POLICY_MISMATCH')
    return policy, runtime


def _derive(conn, c, r, intent):
    policy, runtime = _runtime(c['workspace_id'])
    if asdict(QueryPolicy.from_environment()) != c['policy']:
        raise PolicyConflict('GOVERNANCE_POLICY_DRIFT')
    dataset = f"dr_dev_w{c['workspace_id']}"
    tables = c['evidence'].get('physical_tables')
    identity = f"dr_query_w{c['workspace_id']}_r{r['reservation_id']}_{c['request_token'].strip()[:32]}"
    if (intent['provider_project'] != policy.project or intent['provider_location'] != policy.location
            or intent['provider_job_id'] != identity or c['provider_job_id'] != identity
            or intent['compiled_intent_sha256'] != c['compiled_intent_sha256']
            or type(tables) is not list or len(tables) != 1
            or any(type(t) is not str or t.split('.')[:2] != [policy.project, dataset] for t in tables)):
        raise PolicyConflict('SERVER_PROVIDER_SCOPE_MISMATCH')
    pins = conn.cursor(row_factory=dict_row).execute("""
        SELECT p.resource_id,p.publication_id,p.publication_revision,to_jsonb(sm) AS serving_receipt
        FROM warehouse_query_attempt_publication_pins p
        JOIN warehouse_serving_materializations sm ON sm.resource_id=p.resource_id
          AND sm.publication_id=p.publication_id AND sm.publication_revision=p.publication_revision
        WHERE p.attempt_id=%s ORDER BY p.resource_id""", (c['attempt_id'],)).fetchall()
    if len(pins) != 1 or pins != c['evidence'].get('publications'):
        raise PolicyConflict('EXACT_PUBLICATION_RECEIPT_REQUIRED')
    ceiling = min(r['reserved_bytes'], r['maximum_bytes_billed'], c['policy']['auto_execute_bytes'], c['policy']['hard_bytes'])
    core = ExpectedJob(policy.project, policy.location, identity, c['workspace_id'], r['reservation_id'],
        c['compiled_intent_sha256'].strip(), c['compiled_sql_sha256'].strip(), tuple(tables), ceiling,
        int(r['execution_started_at'].timestamp()*1000))
    expected = dict(contract_version=1, project=policy.project, dataset=dataset, location=policy.location,
        job_id=identity, sql_sha256=core.sql_sha256, compiled_intent_sha256=core.intent_sha256,
        tables=tables, labels=dict(datarise='query', workspace=str(c['workspace_id']),
            reservation=str(r['reservation_id']), intent=core.intent_sha256[:63]),
        maximum_bytes_billed=min(ceiling, policy.maximum_bytes), job_timeout_ms=policy.timeout_ms,
        publication_sha256=digest(pins), governance_policy_sha256=digest(c['policy']),
        provider_policy=asdict(policy), lookup_policy=asdict(runtime.policy))
    return expected, core, runtime


def prepare_authenticated_provider_policy(*, request: Request, workspace_id: int, attempt_handle: str) -> PolicyPreparation:
    if (not isinstance(request, Request) or type(workspace_id) is not int or workspace_id < 1
            or type(attempt_handle) is not str or not re.fullmatch('[a-f0-9]{32}', attempt_handle)):
        raise ValueError('Verified session and server handle required')
    try:
        settings = require_secret()
    except ValueError:
        raise HTTPException(503, 'Warehouse session unavailable') from None
    if settings.mode != 'session':
        raise HTTPException(503, 'Warehouse session required')
    token = request.cookies.get(settings.session_cookie)
    if not token or len(token) > 128:
        raise HTTPException(401, 'Sign in to continue.')
    with repo.transaction(workspace_id) as conn:
        user, session = _session_locked(conn, token)
        workspace = repo.one(conn, 'SELECT owner,status FROM workspaces WHERE workspace_id=%s FOR SHARE', (workspace_id,))
        if not workspace or workspace['owner'] != user['owner_key'] or workspace['status'] != 'ACTIVE':
            raise HTTPException(403, 'Workspace unavailable')
        a = attempts.read_attempt(conn, workspace_id=workspace_id, owner_key=user['owner_key'], attempt_handle=attempt_handle)
        c = repo.checkpoint(conn, a['attempt_id'])
        if c['user_id'] != user['user_id'] or c['session_digest'] != session['token_digest']:
            raise HTTPException(403, 'Original authenticated checkpoint required')
        r, _, _ = recovery._live_context(conn, c)
        intent, _ = repo.latest(conn, c['attempt_id'])
        expected, _, _ = _derive(conn, c, r, intent)
        hashed = digest(expected)
        old = repo.one(conn, 'SELECT * FROM warehouse_query_provider_policies WHERE attempt_id=%s', (c['attempt_id'],))
        if old:
            if old['expected'] != expected or old['expected_sha256'] != hashed:
                raise PolicyConflict('IMMUTABLE_PROVIDER_POLICY_DRIFT')
        else:
            conn.execute('INSERT INTO warehouse_query_provider_policies(attempt_id,expected,expected_sha256) VALUES(%s,%s,%s)',
                         (c['attempt_id'], Jsonb(expected), hashed))
        return PolicyPreparation(attempt_handle)


def _snapshot(conn, c, lease, revision):
    core = attestation._snapshot(conn, c, lease, revision)
    r = repo.one(conn, 'SELECT * FROM query_governance_reservations WHERE reservation_id=%s', (c['reservation_id'],))
    intent, _ = repo.latest(conn, c['attempt_id'])
    expected, derived_core, runtime = _derive(conn, c, r, intent)
    old = repo.one(conn, 'SELECT * FROM warehouse_query_provider_policies WHERE attempt_id=%s', (c['attempt_id'],))
    if (not old or old['expected'] != expected or old['expected_sha256'] != digest(expected) or core != derived_core):
        raise PolicyConflict('IMMUTABLE_PROVIDER_POLICY_REQUIRED_OR_DRIFTED')
    return expected, core, runtime


def lookup_with_provider_policy(*, lease: recovery.RecoveryLease, expected_revision: int) -> attestation.RecoveryAttestation:
    """Trusted bookkeeping only; accepts no principal, SQL, provider or policy DTO."""
    recovery._validate_lease(lease)
    with recovery._worker(lease.attempt_id) as (conn, c):
        expected, core, runtime = _snapshot(conn, c, lease, expected_revision)
    report = _lookup_metadata(runtime, core,
        lambda raw, trusted_core, lookup: verify_exact_metadata(raw, trusted_core, lookup, expected))
    with recovery._worker(lease.attempt_id) as (conn, c):
        current, current_core, _ = _snapshot(conn, c, lease, expected_revision)
        if current != expected or current_core != core:
            raise PolicyConflict('PROVIDER_POLICY_CHANGED_DURING_LOOKUP')
        result = recovery._record_observation_locked(conn, c, lease, expected_revision,
            recovery.JobIdentity(core.project, core.location, core.job_id), recovery.Observation(report.observation))
        attestation._persist(conn, c['attempt_id'], result.revision, report, digest(expected))
        return attestation.RecoveryAttestation(result.revision, report.observation, report.verdict,
                                             report.reason, report.billing_complete, result)


@dataclass(frozen=True)
class PreparedQueryEnvelope(recovery.NonDispatch):
    """Diagnostic fingerprint only. Never exposes SQL or a provider send permit."""
    attempt_handle: str
    job_id: str
    request_sha256: str
    classification: str = 'ENVELOPE_REVIEWED_NO_DISPATCH'


def _inspect_query_envelope(
    *, request: Request, workspace_id: int, attempt_handle: str,
    persist: bool,
) -> PreparedQueryEnvelope:
    """Recheck current authenticated ownership and compile the exact job request.

    This function does not send or return the provider request. Only the
    server-owned digest and deterministic job identity leave this boundary.
    Any existing send boundary, revoked/expired claim, or drift fails closed.
    """
    # Reuse the full original session and provider policy pinning boundary.
    prepare_authenticated_provider_policy(
        request=request, workspace_id=workspace_id, attempt_handle=attempt_handle,
    )
    settings = require_secret()
    token = request.cookies.get(settings.session_cookie)
    with repo.transaction(workspace_id) as conn:
        user, session = _session_locked(conn, token)
        workspace = repo.one(
            conn, 'SELECT owner,status FROM workspaces WHERE workspace_id=%s FOR SHARE',
            (workspace_id,),
        )
        if not workspace or workspace['owner'] != user['owner_key'] or workspace['status'] != 'ACTIVE':
            raise HTTPException(403, 'Workspace unavailable')
        attempt = attempts.read_attempt(
            conn, workspace_id=workspace_id, owner_key=user['owner_key'],
            attempt_handle=attempt_handle,
        )
        checkpoint = repo.checkpoint(conn, attempt['attempt_id'])
        if checkpoint['user_id'] != user['user_id'] or checkpoint['session_digest'] != session['token_digest']:
            raise HTTPException(403, 'Original authenticated checkpoint required')
        reservation, query, _ = recovery._live_context(conn, checkpoint)
        intent, event = repo.latest(conn, checkpoint['attempt_id'])
        if (event['phase'] != 'INTENT_RECORDED'
                or event['event'] not in ('CREATED', 'LEASE_ACQUIRED')
                or event['observation'] is not None):
            raise PolicyConflict('EXISTING_SEND_STATE_LOOKUP_ONLY')
        expected, _, _ = _derive(conn, checkpoint, reservation, intent)
        stored = repo.one(
            conn, 'SELECT expected,expected_sha256 FROM warehouse_query_provider_policies WHERE attempt_id=%s',
            (checkpoint['attempt_id'],),
        )
        if not stored or stored['expected'] != expected or stored['expected_sha256'] != digest(expected):
            raise PolicyConflict('IMMUTABLE_PROVIDER_POLICY_REQUIRED_OR_DRIFTED')
        job = build_reviewed_query_job_request(expected=expected, compiled_sql=query.sql)
        request_sha256 = digest(job.body)
        if persist:
            # Exact same transaction as the identity/policy revalidation.
            # No provider SDK, network or durable dispatch permit is involved.
            conn.execute(
                """INSERT INTO warehouse_query_request_envelopes
                    (attempt_id,provider_job_id,request_sha256,policy_sha256)
                   VALUES(%s,%s,%s,%s) ON CONFLICT(attempt_id) DO NOTHING""",
                (checkpoint['attempt_id'], job.job_id, request_sha256, digest(expected)),
            )
            pinned = repo.one(
                conn,
                """SELECT provider_job_id,request_sha256,policy_sha256
                   FROM warehouse_query_request_envelopes WHERE attempt_id=%s""",
                (checkpoint['attempt_id'],),
            )
            if (not pinned or pinned['provider_job_id'] != job.job_id
                    or pinned['request_sha256'] != request_sha256
                    or pinned['policy_sha256'] != digest(expected)):
                raise PolicyConflict('IMMUTABLE_QUERY_ENVELOPE_DRIFT')
        # Return only an inspection digest; no SQL or executable provider body.
        return PreparedQueryEnvelope(
            attempt_handle=attempt_handle, job_id=job.job_id,
            request_sha256=request_sha256,
        )


def inspect_authenticated_query_envelope_without_dispatch(
    *, request: Request, workspace_id: int, attempt_handle: str,
) -> PreparedQueryEnvelope:
    """Backward-compatible read-only inspection; never writes envelope rows."""
    return _inspect_query_envelope(
        request=request, workspace_id=workspace_id,
        attempt_handle=attempt_handle, persist=False,
    )


def pin_authenticated_query_envelope_without_dispatch(
    *, request: Request, workspace_id: int, attempt_handle: str,
) -> PreparedQueryEnvelope:
    """Persist exact server-derived request hash before any possible send.

    Uses migration 0058; no query submission, send boundary, or permit.
    """
    return _inspect_query_envelope(
        request=request, workspace_id=workspace_id,
        attempt_handle=attempt_handle, persist=True,
    )


def lookup_pinned_submission_without_resend(
    *, lease: recovery.RecoveryLease, expected_revision: int,
) -> attestation.RecoveryAttestation:
    """F2: jobs.get-only recovery after committed, pinned SEND_BOUNDARY.

    The immutable envelope and provider policy must agree with the original
    job ID. Missing boundary or fingerprint fails before any provider GET.
    Revoked or expired original sessions can still be inspected for recovery;
    they never authorize another submission. No result fetch or settlement.
    """
    recovery._validate_lease(lease)
    with recovery._worker(lease.attempt_id) as (conn, checkpoint):
        expected, core, _ = _snapshot(conn, checkpoint, lease, expected_revision)
        _, latest = repo.latest(conn, lease.attempt_id)
        repo.require_owner(conn, latest, lease, expected_revision)
        if latest['phase'] not in ('SEND_UNKNOWN', 'PROVIDER_RUNNING'):
            raise repo.RecoveryConflict('SEND_BOUNDARY_REQUIRED_LOOKUP_ONLY')
        if not repo.one(
            conn,
            """SELECT 1 FROM warehouse_query_send_journal
               WHERE attempt_id=%s AND event='SEND_BOUNDARY' LIMIT 1""",
            (lease.attempt_id,),
        ):
            raise repo.RecoveryConflict('SEND_BOUNDARY_REQUIRED_LOOKUP_ONLY')
        pinned = repo.one(
            conn,
            """SELECT provider_job_id,request_sha256,policy_sha256
               FROM warehouse_query_request_envelopes WHERE attempt_id=%s""",
            (lease.attempt_id,),
        )
        if (not pinned or pinned['provider_job_id'] != core.job_id
                or pinned['policy_sha256'] != digest(expected)
                or type(pinned['request_sha256']) is not str
                or not re.fullmatch(r'[a-f0-9]{64}', pinned['request_sha256'].strip())):
            raise PolicyConflict('IMMUTABLE_SUBMISSION_ENVELOPE_REQUIRED')
    # Existing two-phase attestation does one jobs.get with no database lock
    # held; on reacquisition it compares current policy and CAS journal state.
    return lookup_with_provider_policy(
        lease=lease, expected_revision=expected_revision,
    )
