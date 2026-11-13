"""Public metadata GET endpoints must not demand row-lock UPDATE permission."""
from pathlib import Path
from unittest.mock import patch
from contextlib import contextmanager
from app.services import product_preview_service as svc

class ReadConnection:
    def __init__(self):
        self.calls = []
    def execute(self, sql, params=None):
        self.calls.append(sql)
        return self
    def fetchone(self):
        return (None,)
    def fetchall(self):
        return []

@contextmanager
def transaction(conn):
    yield conn

def test_capabilities_reads_metadata_without_for_share():
    conn = ReadConnection()
    with patch.object(svc.database, 'repository_transaction', lambda: transaction(conn)):
        data = svc.capabilities(1, 2, {'user_id': 3, 'owner_key': 'owned'})
    assert data['warehouse'] == 'NOT_CONFIGURED'
    assert data['warehouse_models'] == []
    assert all('FOR SHARE' not in query for query in conn.calls)

def test_jobs_metadata_uses_select_only_scope():
    conn = ReadConnection()
    with patch.object(svc.database, 'repository_transaction', lambda: transaction(conn)):
        data = svc.jobs(1, 2, {'user_id': 3, 'owner_key': 'owned'})
    assert data['jobs'] == []
    assert all('FOR SHARE' not in query for query in conn.calls)

def test_public_capabilities_grants_are_read_only():
    root = Path(__file__).resolve().parents[1]
    text = (root/'deployment/operations/sql/grant_public_product_capability_read_api.sql').read_text()
    assert 'GRANT SELECT ON TABLE' in text
    assert 'public.processing_jobs' in text
    assert 'GRANT UPDATE' not in text
    assert 'GRANT INSERT' not in text
