"""414D5: durable session-bound claim checkpoint. No provider and no live permit.

An EXECUTING reservation/checkpoint must only enter existing recovery workflows;
this function never sends a query, releases a claim or returns SQL/capabilities.
"""
from dataclasses import asdict, dataclass, replace
import hashlib
import re

from fastapi import HTTPException, Request
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.auth.config import require_secret
from app.auth.repository import principal
from app.auth.security import digest
from app.db.database import repository_transaction
from app.db import query_governance_repository as budgets
from app.db import warehouse_query_attempt_repository as attempts
from app.query_governance.policy import QueryPolicy
from app.services.query_governance_service import QueryGovernanceService
from app.services.warehouse_sql_trusted_recompilation_service import _recompile_in_transaction
from app.services.warehouse_sql_recovery_settlement_service import _expected_job_id


@dataclass(frozen=True)
class DispatchCheckpoint:
    attempt_handle: str
    state: str

    @property
    def execution_authorized(self):
        return False

    @property
    def provider_dispatched(self):
        return False


def _one(conn, sql, params=()):
    return conn.cursor(row_factory=dict_row).execute(sql, params).fetchone()


def _session_locked(conn, token):
    # Auth writers lock user before session (password reset, logout, disable).
    found=_one(conn, 'SELECT user_id FROM auth_sessions WHERE token_digest=%s', (digest(token),))
    if not found:
        raise HTTPException(401, 'Sign in to continue.')
    user=_one(conn, 'SELECT * FROM users WHERE user_id=%s FOR SHARE', (found['user_id'],))
    session=_one(conn, """SELECT *,LEAST(expires_at,last_seen_at+interval '30 minutes') AS session_until
        FROM auth_sessions WHERE token_digest=%s FOR SHARE""", (digest(token),))
    if (not user or user['account_status']!='ACTIVE' or not session
            or session['user_id']!=user['user_id'] or session['revoked_at'] is not None
            or not conn.execute('SELECT %s>clock_timestamp()', (session['session_until'],)).fetchone()[0]):
        raise HTTPException(401, 'Sign in to continue.')
    return principal(user),session


def _budget_locked(conn, workspace, reservation, row_limit, policy):
    # Snapshot already INCLUDES this live reservation: never charge it twice.
    usage=budgets._snapshot_locked(conn, workspace)
    r=reservation
    if (r['estimated_bytes']!=r['reserved_bytes']
            or r['estimate_confidence'] not in ('EXACT','UPPER_BOUND')
            or not 0<=r['reserved_bytes']<=r['maximum_bytes_billed']<=policy.auto_execute_bytes
            or r['output_rows']!=row_limit or not 1<=row_limit<=policy.maximum_output_rows
            or usage.committed_bytes_today+usage.reserved_bytes_today>policy.workspace_daily_bytes
            or usage.project_committed_bytes_month+usage.project_reserved_bytes_month>policy.project_monthly_bytes
            or usage.workspace_active_queries>policy.workspace_concurrency
            or usage.global_active_queries>policy.global_concurrency):
        raise ValueError('WAREHOUSE_DISPATCH_BUDGET_REVALIDATION_FAILED')


def checkpoint_authenticated_warehouse_dispatch(*, request: Request, workspace_id: int,
                                               attempt_handle: str) -> DispatchCheckpoint:
    """Single DB linearization point; result is deliberately NOT dispatch authority."""
    if not isinstance(request, Request) or type(workspace_id) is not int or workspace_id<1:
        raise ValueError('Authenticated request and positive workspace required')
    if type(attempt_handle) is not str or not re.fullmatch('[a-f0-9]{32}',attempt_handle):
        raise ValueError('Server-issued attempt handle required')
    try:
        settings=require_secret()
    except ValueError as exc:
        raise HTTPException(503, 'Warehouse session authentication unavailable') from exc
    if settings.mode!='session':
        raise HTTPException(503, 'Warehouse gateway requires session authentication')
    token=request.cookies.get(settings.session_cookie)
    if not token or len(token)>128:
        raise HTTPException(401, 'Sign in to continue.')
    policy=QueryPolicy.from_environment()  # No caller-supplied policy overrides.
    with repository_transaction() as conn:
        # Only this new boundary is bounded; existing consumers are unchanged.
        conn.execute("SET LOCAL lock_timeout='5s'")
        conn.execute("SET LOCAL statement_timeout='10s'")
        budgets._lock_scopes(conn,workspace_id)
        user,session=_session_locked(conn,token)
        workspace=_one(conn,'SELECT owner,status FROM workspaces WHERE workspace_id=%s FOR SHARE',(workspace_id,))
        if not workspace or workspace['status']!='ACTIVE' or workspace['owner']!=user['owner_key']:
            raise HTTPException(403, 'Workspace unavailable')
        governance=QueryGovernanceService(workspace_id,user,policy=policy)
        attempt=attempts.read_attempt(conn,workspace_id=workspace_id,owner_key=user['owner_key'],attempt_handle=attempt_handle)
        pins=conn.execute('SELECT resource_id FROM warehouse_query_attempt_publication_pins WHERE attempt_id=%s ORDER BY resource_id',(attempt['attempt_id'],)).fetchall()
        if len(pins)!=1:
            raise ValueError('ONE_MODEL_PUBLICATION_SCOPE_REQUIRED')
        for (resource,) in pins:
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(f'datarise-publication-retirement:{resource}',))
        conn.execute('SELECT 1 FROM warehouse_query_attempt_registry WHERE attempt_id=%s FOR UPDATE',(attempt['attempt_id'],))
        old=_one(conn,"""SELECT c.*,EXISTS(SELECT 1 FROM warehouse_query_dispatch_revocations v
            WHERE v.attempt_id=c.attempt_id) AS revoked,valid_until<=clock_timestamp() AS expired
            FROM warehouse_query_dispatch_checkpoints c WHERE attempt_id=%s""",(attempt['attempt_id'],))
        if old:
            state='REVOKED_RECOVERY_REQUIRED' if old['revoked'] else 'EXPIRED_RECOVERY_REQUIRED' if old['expired'] else 'RECOVERY_REQUIRED'
            return DispatchCheckpoint(attempt_handle,state)
        link=_one(conn,'SELECT reservation_id FROM warehouse_query_attempt_reservation_links WHERE attempt_id=%s',(attempt['attempt_id'],))
        if not link:
            raise ValueError('WAREHOUSE_QUERY_LINK_REQUIRED')
        reservation=_one(conn,'SELECT * FROM query_governance_reservations WHERE reservation_id=%s FOR UPDATE',(link['reservation_id'],))
        if reservation['status']=='EXECUTING':
            # Old rehearsal or ambiguous claim: never bless it retroactively.
            return DispatchCheckpoint(attempt_handle,'RECOVERY_REQUIRED')
        if reservation['status']!='ACTIVE':
            raise ValueError('WAREHOUSE_RESERVATION_NOT_ACTIVE')
        evidence=conn.cursor(row_factory=dict_row).execute("""SELECT p.resource_id,p.publication_id,p.publication_revision,
            to_jsonb(sm) AS serving_receipt FROM warehouse_query_attempt_publication_pins p
            JOIN warehouse_resources r USING(resource_id)
            JOIN warehouse_publication_heads h ON h.resource_id=p.resource_id
              AND h.publication_id=p.publication_id AND h.revision=p.publication_revision
            JOIN warehouse_serving_materializations sm ON sm.resource_id=p.resource_id
              AND sm.publication_id=p.publication_id AND sm.publication_revision=p.publication_revision
            WHERE p.attempt_id=%s FOR SHARE OF p,r,h,sm""",(attempt['attempt_id'],)).fetchall()
        if len(evidence)!=1:
            raise ValueError('WAREHOUSE_CURRENT_SERVING_PUBLICATION_REQUIRED')
        draft=_one(conn,'SELECT * FROM warehouse_query_attempt_logical_drafts WHERE attempt_id=%s FOR SHARE',(attempt['attempt_id'],))
        if not draft:
            raise ValueError('WAREHOUSE_LOGICAL_DRAFT_NOT_REGISTERED')
        # Freeze source membership/freshness through compilation and claim.
        conn.execute("""SELECT s.source_mapping_id FROM warehouse_model_sources s JOIN datasets d
            ON d.dataset_id=s.source_dataset_id WHERE s.warehouse_model_version_id=%s
            ORDER BY d.dataset_id FOR SHARE OF s,d""",(draft['warehouse_model_version_id'],)).fetchall()
        compiled,query=_recompile_in_transaction(conn,governance,attempt_handle)
        if compiled.reservation_id!=reservation['reservation_id']:
            raise ValueError('WAREHOUSE_RESERVATION_IDENTITY_MISMATCH')
        _budget_locked(conn,workspace_id,reservation,draft['row_limit'],policy)
        claimed=budgets.claim(conn,reservation_id=reservation['reservation_id'],owner_key=user['owner_key'])
        bound=replace(query,request_token=attempt['request_token'].strip())
        valid_until=min(session['session_until'],claimed['expires_at'])
        conn.execute("""INSERT INTO warehouse_query_dispatch_checkpoints(
            attempt_id,reservation_id,workspace_id,user_id,session_digest,
            compiled_intent_sha256,compiled_sql_sha256,provider_job_id,evidence,policy,valid_until)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (attempt['attempt_id'],claimed['reservation_id'],workspace_id,user['user_id'],session['token_digest'],
             query.intent_sha256,hashlib.sha256(query.sql.encode()).hexdigest(),_expected_job_id(bound,claimed['reservation_id']),
             Jsonb(dict(publications=evidence,physical_tables=list(query.physical_tables),logical_intent_sha256=attempt['logical_intent_sha256'].strip())),
             Jsonb(asdict(policy)),valid_until))
        return DispatchCheckpoint(attempt_handle,'CHECKPOINTED_NO_DISPATCH')
