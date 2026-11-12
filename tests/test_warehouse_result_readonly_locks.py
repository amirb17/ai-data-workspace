"""Read-only result eligibility does not acquire row UPDATE locks.

The shared query-governance advisory transaction remains mandatory. Stateful
recovery/accounting paths retain their original FOR UPDATE behavior by default.
"""
from unittest.mock import patch

from app.db import warehouse_query_send_repository as repo
from app.services import warehouse_sql_result_eligibility_service as eligibility


class FakeConnection:
    def __init__(self):
        self.sql=[]
    def execute(self, statement, params=()):
        self.sql.append(statement)
        class R:
            def fetchone(self): return (1,)
            def __iter__(self): return iter(())
        return R()


def test_identity_default_preserves_strong_row_locking():
    conn=FakeConnection()
    checkpoint={
        "attempt_id":101,"reservation_id":202,
        "workspace_id":18,"owner_key":"owner","request_token":"a"*64,
        "compiled_intent_sha256":"hash",
    }
    reservation={
        "reservation_id":202,"workspace_id":18,
        "owner_key":"owner","request_token":"a"*64,
        "intent_sha256":"hash","consumer":"SQL","provider":"BIGQUERY",
    }
    with patch.object(repo,"one",return_value=reservation) as one:
        repo.lock_identity(conn,checkpoint)
        assert "FOR UPDATE" in one.call_args.args[1]
    assert any("FOR UPDATE" in sql for sql in conn.sql)


def test_identity_read_only_mode_omits_all_row_update_locks():
    conn=FakeConnection()
    checkpoint={
        "attempt_id":101,"reservation_id":202,
        "workspace_id":18,"owner_key":"owner","request_token":"a"*64,
        "compiled_intent_sha256":"hash",
    }
    reservation={
        "reservation_id":202,"workspace_id":18,
        "owner_key":"owner","request_token":"a"*64,
        "intent_sha256":"hash","consumer":"SQL","provider":"BIGQUERY",
    }
    with patch.object(repo,"one",return_value=reservation) as one:
        repo.lock_identity(conn,checkpoint,lock=False)
        assert "FOR UPDATE" not in one.call_args.args[1]
    assert not any("FOR UPDATE" in sql for sql in conn.sql)
    assert any("pg_advisory_xact_lock" in sql for sql in conn.sql)


def test_result_eligibility_uses_read_only_id_and_send_intent():
    conn=FakeConnection()
    with patch.object(repo,"checkpoint",return_value={"attempt_id":101}),patch.object(
        repo,"lock_identity",return_value={"status":"ACTIVE"}
    ) as identity,patch.object(repo,"latest",return_value=(
        {"provider_job_id":"j"}, {"phase":"CREATED"}
    )) as latest:
        from pytest import raises
        with raises(eligibility.ResultEligibilityConflict,
                    match="SETTLED_SUCCESS_REQUIRED"):
            eligibility._inspect_settled_result_eligibility_locked(conn,101)
    assert identity.call_args.kwargs == {"lock":False}
    assert latest.call_args.kwargs == {"lock":False}
