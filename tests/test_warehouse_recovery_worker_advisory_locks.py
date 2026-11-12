"""Recovery worker inspect/acquire use append-only journal with advisory serialization.

No DB or provider access is required for these focused offline tests.
"""
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from uuid import UUID

from app.db import warehouse_query_send_repository as repo
from app.services import warehouse_sql_send_recovery_service as recovery


CHECKPOINT={"attempt_id":7,"workspace_id":18,
            "valid_until":datetime.now(timezone.utc)+timedelta(hours=1)}
RESERVATION={"status":"EXECUTING"}
INTENT={"provider_project":"datarise-511817",
        "provider_location":"asia-south1","provider_job_id":"job_7"}
EVENT={"revision":4,"phase":"INTENT_RECORDED","observation":None,
       "lease_until":None,"lease_generation":1}


class Conn:
    def execute(self, statement, params=()):
        if "SELECT EXISTS" in statement:
            value=False
        elif "SELECT %s<=clock_timestamp()" in statement:
            value=False
        else:
            value=datetime.now(timezone.utc)+timedelta(seconds=30)
        class Result:
            def fetchone(self):
                return (value,)
        return Result()


@contextmanager
def worker(attempt_id):
    assert attempt_id==7
    yield Conn(),CHECKPOINT


def test_recovery_inspection_avoids_metadata_update_locks():
    with patch.object(recovery,"_worker",worker),patch.object(
        repo,"lock_identity",return_value=RESERVATION
    ) as identity,patch.object(repo,"latest",return_value=(INTENT,EVENT)) as latest:
        view=recovery.inspect_recovery(attempt_id=7)
    assert view.attempt_id==7
    assert identity.call_args.kwargs=={"lock":False}
    assert latest.call_args.kwargs=={"lock":False}


def test_recovery_acquisition_is_fenced_by_revision_and_append_only():
    appended=[]
    def append(conn,attempt,prior,event,phase,*,token,until):
        appended.append((attempt,prior["revision"],event,phase))
        return {
            "revision":5,"lease_generation":2
        }
    with patch.object(recovery,"_worker",worker),patch.object(
        repo,"lock_identity",return_value=RESERVATION
    ) as identity,patch.object(repo,"latest",return_value=(INTENT,EVENT)) as latest,patch.object(
        repo,"append",side_effect=append):
        lease=recovery.acquire_recovery(
            attempt_id=7,expected_revision=4,lease_seconds=30)
    assert isinstance(lease.token,UUID)
    assert lease.revision==5 and lease.generation==2
    assert appended==[(7,4,"LEASE_ACQUIRED","INTENT_RECORDED")]
    assert identity.call_args.kwargs=={"lock":False}
    assert latest.call_args.kwargs=={"lock":False}
