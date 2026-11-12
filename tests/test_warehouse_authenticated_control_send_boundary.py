"""Control owns live send authorization, provider owns only exact cloud send.

Pure unit tests for the control-side transaction. No cloud or database calls.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from starlette.requests import Request

from app.services import warehouse_sql_send_recovery_service as recovery
from app.db import warehouse_query_send_repository as repo


JOB = recovery.JobIdentity(
    "datarise-511817", "asia-south1", "dr_query_w18_r9_"+"a"*32,
)
LEASE = recovery.RecoveryLease(
    7, 4, 2, uuid4(), datetime.now(timezone.utc)+timedelta(seconds=60), JOB,
)
REQ = Request({"type":"http", "headers":[(b"cookie",b"auth=testtoken")]})
PIN = {
    "provider_job_id":JOB.job_id,
    "request_sha256":"b"*64,
    "policy_sha256":"c"*64,
    "expected_sha256":"c"*64,
    "expected":{"job_id":JOB.job_id},
}


@contextmanager
def control_transaction(workspace_id=None, *, duty="query_control"):
    assert workspace_id is None
    assert duty=="query_control"
    yield object()


def _run(lease, *, pinned=PIN):
    user={"user_id":3,"owner_key":"owner"}
    session={"token_digest":"digest"}
    checkpoint={
        "attempt_id":7,"workspace_id":18,"user_id":3,
        "session_digest":"digest","provider_job_id":JOB.job_id,
    }
    intent={
        "provider_project":JOB.project,
        "provider_location":JOB.location,
        "provider_job_id":JOB.job_id,
    }
    with patch.object(recovery,"require_secret",return_value=SimpleNamespace(
        mode="session",session_cookie="auth")),patch.object(
        repo,"transaction",control_transaction),patch.object(
        recovery.budgets,"_lock_scopes"),patch.object(
        recovery,"_session_locked",return_value=(user,session)
    ),patch.object(recovery.attempts,"read_attempt",return_value={
        "attempt_id":7
    }),patch.object(repo,"checkpoint",return_value=checkpoint),patch.object(
        recovery,"_live_context",return_value=({"status":"EXECUTING"},None,None)
    ),patch.object(repo,"latest",return_value=(intent,{
        "phase":"INTENT_RECORDED"
    })),patch.object(repo,"require_owner") as owner,patch.object(
        repo,"one",side_effect=[
            {"owner":"owner","status":"ACTIVE"},pinned
        ]
    ),patch.object(repo,"append",return_value={"revision":5}) as append,patch.object(
        recovery,"_view",return_value="REVIEWED_CONTROL_MARKER"
    ):
        if lease.job!=JOB:
            with pytest.raises(repo.RecoveryConflict,
                               match="ORIGINAL_PROVIDER_JOB_LEASE_REQUIRED"):
                recovery.record_authenticated_control_send_boundary_without_dispatch(
                    request=REQ,workspace_id=18,attempt_handle="a"*32,
                    lease=lease,expected_revision=4,
                )
            append.assert_not_called()
            return
        returned=recovery.record_authenticated_control_send_boundary_without_dispatch(
            request=REQ,workspace_id=18,attempt_handle="a"*32,
            lease=lease,expected_revision=4,
        )
        owner.assert_called_once()
        assert append.call_args.args[3:] == ("SEND_BOUNDARY","SEND_UNKNOWN")
        assert returned=="REVIEWED_CONTROL_MARKER"


def test_authenticated_query_control_commits_exact_fenced_boundary():
    _run(LEASE)


def test_wrong_provider_job_lease_cannot_commit_send_boundary():
    wrong=recovery.RecoveryLease(
        LEASE.attempt_id,LEASE.revision,LEASE.generation,LEASE.token,
        LEASE.lease_until,
        recovery.JobIdentity(JOB.project,JOB.location,"wrong"),
    )
    _run(wrong)
