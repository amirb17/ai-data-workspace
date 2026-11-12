"""Reproduce a warehouse query admission transaction and always ROLLBACK.

This deliberately uses the current canonical local PostgreSQL schema and the
same server-owned model binding, current auth session, query estimate, reservation, and result
contract writes as the FastAPI admission handler. It NEVER commits an attempt,
opens a session, calls jobs.insert, fetches result rows or edits cloud IAM.

The output is a safe failure stage, exception class and SQLSTATE only.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import sys

from dotenv import load_dotenv


class _DiagnosticStop(Exception):
    pass


def run():
    from psycopg import connect
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
    from app.auth.repository import principal
    from app.db import database
    from app.db import query_governance_repository as budgets
    from app.db import warehouse_query_attempt_repository as attempts
    from app.query_governance.policy import QueryPolicy
    from app.services import warehouse_query_workflow_service as workflow
    from app.services import warehouse_sql_stored_draft_service as drafts
    from app.services import warehouse_sql_provider_policy_service as policy
    from app.services.query_governance_service import QueryGovernanceService
    from app.warehouse.query_recovery_attestation import digest
    from app.warehouse.named_query_results import (
        result_identity, validate_fixed_schema, STORAGE_BYTES,
        WORKSPACE_STORAGE_BYTES, projection_schema,
    )
    from tools.warehouse_local_live_api import (
        _assert_local_development, _bind_canonical_local_database, make_runtime,
    )

    root = Path(__file__).resolve().parents[1] / '.env'
    fallback = Path('D:/Project/ai-data-workspace/.env')
    if root.is_file():
        load_dotenv(root)
    elif fallback.is_file():
        load_dotenv(fallback)
    os.environ.setdefault('APP_ENV','development')
    os.environ.setdefault('AUTH_MODE','session')
    os.environ.setdefault('DATARISE_PUBLIC_LAUNCH_MODE','off')
    _assert_local_development()
    _bind_canonical_local_database()

    stage = 'connection'
    conn = connect(database.DATABASE_URL, row_factory=dict_row, autocommit=False)
    result = {'database':'OK', 'stage':'NOT_STARTED',
              'persisted':False, 'provider_job_submitted':False}
    try:
        with conn:
            try:
                stage = 'workspace_owner'
                scope = conn.execute(
                    "SELECT owner FROM public.workspaces "
                    "WHERE workspace_id=18 AND status='ACTIVE'"
                ).fetchone()
                if not scope:
                    raise ValueError('WORKSPACE_NOT_ACTIVE')
                user_row = conn.execute(
                    'SELECT * FROM public.users '
                    'WHERE owner_key=%s AND account_status=%s '
                    'ORDER BY user_id LIMIT 1',
                    (scope['owner'], 'ACTIVE'),
                ).fetchone()
                if not user_row:
                    raise ValueError('WORKSPACE_USER_NOT_ACTIVE')
                user = principal(user_row)
                # Use a currently live server-side session digest, not a
                # fabricated digest that the authenticated trigger must reject.
                # Never return or print the digest or token.
                stage = 'current_authenticated_session'
                active_session = conn.execute(
                    """SELECT token_digest FROM public.auth_sessions
                       WHERE user_id=%s AND revoked_at IS NULL
                         AND expires_at>clock_timestamp()
                         AND last_seen_at>clock_timestamp()-interval '30 minutes'
                       ORDER BY last_seen_at DESC LIMIT 1""",
                    (user['user_id'],),
                ).fetchone()
                if not active_session:
                    raise ValueError('CURRENT_AUTHENTICATED_SESSION_NOT_FOUND')
                session_digest = active_session['token_digest']
                sql = 'SELECT "customer_id", "city", "active" FROM model_1 LIMIT 100'
                stage = 'resolve_and_preflight'
                resolved = workflow._resolve(conn,user,18,1,sql)
                runtime = make_runtime()
                estimate = workflow._preflight(runtime,resolved,18)
                draft,binding,query,_=resolved

                # All writes below are on one transaction and are rolled back
                # unconditionally. This is NOT a durable request or cloud send.
                stage = 'register_attempt'
                attempt,_ = attempts.register_attempt(
                    conn,workspace_id=18,owner_key=user['owner_key'],
                    attempt_handle=secrets.token_hex(16),
                    logical_intent_sha256=drafts.logical_draft_digest(draft),
                )
                stage = 'register_logical_draft'
                governance=QueryGovernanceService(18,user)
                drafts._register_in_transaction(
                    conn,governance,attempt['attempt_handle'].strip(),1,sql,
                )
                stage = 'reserve_budget'
                reserved=budgets.reserve(
                    conn,workspace_id=18,owner_key=user['owner_key'],
                    consumer='SQL',provider='BIGQUERY',
                    request_token=attempt['request_token'].strip(),
                    intent_sha256=query.intent_sha256,
                    estimate=estimate,requested_rows=draft.row_limit,
                    policy=QueryPolicy.from_environment(),
                )
                if reserved['reservation'] is None:
                    raise ValueError('BUDGET_RESERVATION_DENIED')
                reservation=reserved['reservation']
                stage = 'pin_publication'
                for pin in binding.models:
                    conn.execute(
                        'INSERT INTO warehouse_query_attempt_publication_pins'
                        '(attempt_id,resource_id,publication_id,publication_revision) '
                        'VALUES(%s,%s,%s,%s)',
                        (attempt['attempt_id'],pin.resource_id,
                         pin.publication_id,pin.publication_revision),
                    )
                stage = 'link_reservation'
                conn.execute(
                    'INSERT INTO warehouse_query_attempt_reservation_links'
                    '(attempt_id,reservation_id) VALUES(%s,%s)',
                    (attempt['attempt_id'],reservation['reservation_id']),
                )
                stage = 'validate_result_schema'
                named=policy._RESULT_POLICIES.get(18)
                if named is None:
                    raise ValueError('NAMED_RESULT_POLICY_REQUIRED')
                schema=projection_schema(binding.columns)
                validate_fixed_schema(schema,draft.row_limit)
                stage = 'register_api_request'
                key=secrets.token_hex(20)
                conn.execute(
                    'INSERT INTO warehouse_query_api_requests'
                    '(workspace_id,user_id,key_sha256,request_sha256,'
                    'session_digest,attempt_id,contract_version)'
                    'VALUES(%s,%s,%s,%s,%s,%s,2)',
                    (18,user['user_id'],hashlib.sha256(key.encode()).hexdigest(),
                     digest({'model':1,'sql':sql}),
                     session_digest,attempt['attempt_id']),
                )
                stage = 'insert_named_result_contract'
                job=f"dr_query_w18_r{reservation['reservation_id']}_{attempt['request_token'].strip()[:32]}"
                read_until_ms=conn.execute(
                    'SELECT floor(extract(epoch FROM clock_timestamp())*1000)::bigint+%s',
                    (named.read_seconds*1000,),
                ).fetchone()[0]
                contract=dict(
                    version=1,
                    destination=result_identity(policy._runtime(18)[0].project,18,job),
                    schema=[list(col) for col in schema],
                    maximum_rows=draft.row_limit,
                    maximum_storage_bytes=STORAGE_BYTES,
                    workspace_storage_bytes=WORKSPACE_STORAGE_BYTES,
                    read_until_ms=read_until_ms,
                    policy={
                        'reader_principal':named.reader_principal,
                        'revision':named.revision,
                        'read_seconds':named.read_seconds,
                    },
                )
                conn.execute(
                    'INSERT INTO warehouse_query_result_contracts'
                    '(attempt_id,workspace_id,provider_job_id,sql_sha256,'
                    'result,read_valid_until) '
                    'VALUES(%s,%s,%s,%s,%s,to_timestamp(%s/1000.0))',
                    (attempt['attempt_id'],18,job,
                     hashlib.sha256(query.sql.encode()).hexdigest(),
                     Jsonb(contract),read_until_ms),
                )
                result['stage']='ALL_ADMISSION_WRITES_PASSED'
                raise _DiagnosticStop()
            except _DiagnosticStop:
                pass
            except Exception as exc:
                result['stage']=stage
                result['exception_class']=type(exc).__name__
                state=getattr(exc,'sqlstate',None)
                if isinstance(state,str) and len(state)==5 and state.isalnum():
                    result['sqlstate']=state
                else:
                    result['sqlstate']='NONE'
                # Only disclose enumerated trigger errors, never unreviewed
                # PostgreSQL exception text, credentials, or data.
                allowlisted = {
                    'Current named API admission required': 'NAMED_ADMISSION_NOT_CURRENT',
                    'Bounded exact named result identity required': 'NAMED_CONTRACT_IDENTITY_MISMATCH',
                    'Permanent result storage budget exhausted': 'NAMED_STORAGE_CAP_EXHAUSTED',
                    'Current authenticated API request binding required': 'AUTH_SESSION_BINDING_NOT_CURRENT',
                }
                diag = getattr(exc, 'diag', None)
                result['reason'] = allowlisted.get(
                    getattr(diag, 'message_primary', None), 'OTHER'
                )
            finally:
                conn.rollback()
        return result
    finally:
        if not conn.closed:
            conn.close()


def main():
    try:
        print(json.dumps(run(),indent=2,sort_keys=True))
    except Exception as exc:
        print(json.dumps({
            'stage':'SETUP', 'exception_class':type(exc).__name__,
            'persisted':False,'provider_job_submitted':False,
        },indent=2,sort_keys=True),file=sys.stderr)
        raise SystemExit(2)


if __name__=='__main__':
    main()
