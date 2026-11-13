"""Offline contract for fenced stage preparation; no job completion."""
from contextlib import contextmanager
from uuid import uuid4

import pytest

from app.db.processing_job_repository import JobExecutionScope
from app.processing_worker.bronze_boundary import prepare_bronze
from app.storage.versioned_artifacts import MemoryVersionedClient


SCOPE = JobExecutionScope(1, 9, 3, 7, 11, 'BRONZE')


class Boundary:
    def __init__(self, scope=SCOPE):
        self.scope = scope
        self.calls = 0

    @contextmanager
    def stage(self, *, required_ms):
        assert required_ms == 30000
        yield self.scope

    def checkpoint(self, *, required_ms):
        assert required_ms == 0
        self.calls += 1
        return self.scope


def test_prepares_pinned_bronze_only_under_validated_scope():
    client = MemoryVersionedClient()
    source = client.seed('raw/test/data.csv', b'id,amount\n001,5.00\n')
    boundary = Boundary()
    result = prepare_bronze(boundary, lambda scope: source if scope == SCOPE else None,
                            client, 'private-bucket', uuid4())
    assert result.scope == SCOPE
    assert result.candidate.source == source
    assert result.candidate.rows == 1
    assert boundary.calls > 3
    assert all(k.startswith('artifact-candidates/') for op,k,*_ in client.calls if op == 'put')


def test_rejects_non_bronze_job_before_resolving_source():
    client = MemoryVersionedClient()
    other = JobExecutionScope(1, 9, 3, 7, 11, 'GOLD')
    called = []
    with pytest.raises(PermissionError):
        prepare_bronze(Boundary(other), lambda scope: called.append(scope), client,
                       'private-bucket', uuid4())
    assert not called
    assert not client.calls


def test_rejects_unregistered_source_before_artifact_writes():
    client = MemoryVersionedClient()
    with pytest.raises(PermissionError):
        prepare_bronze(Boundary(), lambda scope: None, client,
                       'private-bucket', uuid4())
    assert not client.calls


def test_scope_revocation_stops_before_any_candidate_writes():
    client = MemoryVersionedClient()
    source = client.seed('raw/test/data.csv', b'id\n1\n')
    boundary = Boundary()
    boundary.checkpoint = lambda required_ms: None
    with pytest.raises(PermissionError, match='authorization changed'):
        prepare_bronze(boundary, lambda scope: source, client,
                       'private-bucket', uuid4())
    assert not any(call[0] == 'put' for call in client.calls)


def test_unknown_or_oversized_budget_fails_closed():
    client = MemoryVersionedClient()
    with pytest.raises(ValueError):
        prepare_bronze(Boundary(), lambda scope: None, client,
                       'private-bucket', uuid4(), stage_budget_ms=120_000)
