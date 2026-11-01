"""Offline contract probes for the integration plan; no real processing runs."""
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    # Stub SDK construction BEFORE importing services with eager global clients.
    from app import config
    class OfflineClient:
        def __getattr__(self, name):
            return lambda *a, **kw: pytest.fail('Storage must not be accessed: '+name)
    monkeypatch.setattr(config, 'get_boto3_session', lambda: SimpleNamespace(client=lambda _: OfflineClient()))
    import psycopg
    monkeypatch.setattr(psycopg, 'connect', lambda *a, **kw: pytest.fail('No database calls in plan probes'))


@pytest.fixture
def path(monkeypatch):
    from app.db import database, dataset_repository
    from app.services import processing_service as processing, delivery_execution_service as delivery
    from app.services import processing_context_service as context, append_application_service as incremental
    from app.services import dataset_processing_service as dataset, delivery_lifecycle_service as lifecycle
    monkeypatch.setattr(database.psycopg, 'connect', lambda *a, **kw: pytest.fail('No database calls in plan probes'))
    events = []
    def lock(name):
        @contextmanager
        def held(*args):
            events.append('enter '+name)
            try:
                yield
            finally:
                events.append('exit '+name)
        return held
    monkeypatch.setattr(dataset, 'dataset_lock', lock('dataset'))
    monkeypatch.setattr(lifecycle, 'lifecycle_lock', lock('lifecycle'))
    monkeypatch.setattr(delivery, 'delivery_lock', lock('delivery'))
    monkeypatch.setattr(lifecycle, 'require_active', lambda _: None)
    monkeypatch.setattr(context, 'owned_upload', lambda *a: (1, 2, 301, 3, 4, 'UPLOADED'))
    monkeypatch.setattr(context, 'owned_rule_context', lambda *a: None)
    monkeypatch.setattr(context, 'read_processing_context', lambda *a: {'status': 'observed'})
    monkeypatch.setattr(dataset_repository, 'get_upload_processing_association', lambda *a: (101,))
    monkeypatch.setattr(delivery, 'get_dataset_version_file_by_id', lambda _: (101, 201, 301, 'READY_FOR_SILVER'))
    monkeypatch.setattr(processing, 'run_silver_processing', lambda *a: events.append('SILVER'))
    monkeypatch.setattr(processing, 'run_gold_processing', lambda *a: events.append('GOLD'))
    monkeypatch.setattr(incremental, 'apply_incremental', lambda *a: events.append('APPLY'))
    return SimpleNamespace(events=events, processing=processing, delivery=delivery,
                           incremental=incremental, dataset=dataset)


@pytest.mark.parametrize('policy,stage,expected', [
    (False, None, ['SILVER', 'GOLD']), (True, None, ['SILVER', 'APPLY', 'GOLD']),
    (False, 'SILVER', ['SILVER']), (True, 'SILVER', ['SILVER', 'APPLY']),
    (False, 'GOLD', ['GOLD']), (True, 'GOLD', ['APPLY', 'GOLD']),
])
def test_continuation_is_not_a_uniform_single_stage(path, monkeypatch, policy, stage, expected):
    monkeypatch.setattr(path.incremental, 'selected_policy', lambda *a: {'policy_id': 7} if policy else None)
    assert path.delivery.continue_processing(1, {'user_id': 2}, 3, 4, stage=stage) == {'status': 'observed'}
    assert [e for e in path.events if e in {'SILVER', 'APPLY', 'GOLD'}] == expected
    assert path.events[:3 if policy else 2] == (
        ['enter dataset', 'enter lifecycle', 'enter delivery'] if policy else ['enter lifecycle', 'enter delivery'])
    assert path.events[-3 if policy else -2:] == (
        ['exit delivery', 'exit lifecycle', 'exit dataset'] if policy else ['exit delivery', 'exit lifecycle'])


@pytest.mark.parametrize('policy', [False, True])
def test_terminal_association_is_not_enough_to_skip_incremental_chain(path, monkeypatch, policy):
    monkeypatch.setattr(path.incremental, 'selected_policy', lambda *a: {'policy_id': 7} if policy else None)
    monkeypatch.setattr(path.delivery, 'get_dataset_version_file_by_id', lambda _: (101, 201, 301, 'SUCCESS'))
    path.delivery.continue_processing(1, {'user_id': 2}, 3, 4)
    assert [e for e in path.events if e in {'SILVER', 'APPLY', 'GOLD'}] == (['SILVER', 'APPLY', 'GOLD'] if policy else [])


def test_silver_reuse_checks_dq_rule_pin_without_reading_object_bytes(monkeypatch):
    # Exercise the real early-return path, without running a processor.
    from app.services import processing_service as processing, delivery_execution_service as delivery
    monkeypatch.setattr(delivery, 'delivery_lock', lambda _: nullcontext())
    monkeypatch.setattr(processing, 'get_dataset_version_file_by_id', lambda _: (101, 201, 301, 'READY_FOR_GOLD'))
    monkeypatch.setattr(processing, 'get_physical_file_by_id', lambda _: (301,))
    monkeypatch.setattr(processing, 'get_rule_approval_context', lambda _: (201, 7, 7, 7, True, True))
    monkeypatch.setattr(processing, 'get_approved_policy', lambda *a: ['approved snapshot'])
    monkeypatch.setattr(processing, 'get_latest_successful_dq_run_for_dataset_version_file',
        lambda _: (901, 301, 401, 7, 10, 9, 1, 'synthetic-silver', 'synthetic-quarantine'))
    monkeypatch.setattr(processing, 'process_silver', lambda **kw: pytest.fail('No processor on reuse'))
    result = processing.run_silver_processing(101, 'offline-only')
    assert result['already_processed'] and result['dq_run_id'] == 901


def test_gold_completeness_is_catalog_metadata_not_object_verification(monkeypatch):
    from app.services import processing_service as processing
    monkeypatch.setattr(processing, 'get_dataset_profiles', lambda _: ['synthetic profile'])
    monkeypatch.setattr(processing, 'build_gold_plan', lambda _: SimpleNamespace(artifacts=[]))
    monkeypatch.setattr(processing, 'get_gold_artifacts_for_run', lambda _: [(1, 1, 'BASE', 'base', 'synthetic-object', 10)])
    assert processing._is_gold_publication_complete(1, 301) is True


def test_post_stage_checkpoint_cannot_undo_an_inflight_side_effect(path, monkeypatch):
    from app.db import processing_job_repository as jobs
    from app.lambda_worker.execution_boundary import ExecutionBoundary, ExecutionStopped
    lease = jobs.JobLease(uuid4(), uuid4(), datetime.now(timezone.utc)+timedelta(seconds=120), 1)
    scope = jobs.JobExecutionScope(2, 1, 3, 4, 301, 'SILVER')
    valid = {'value': True}
    monkeypatch.setattr(jobs, 'get_execution_scope', lambda *a, **kw: scope if valid['value'] else None)
    monkeypatch.setattr(jobs, 'renew_job_lease', lambda *a, **kw: lease)
    effects = []
    control = ExecutionBoundary(lease, lambda: 300000)
    with pytest.raises(ExecutionStopped):
        with control.stage(required_ms=1000):
            # Represents a blocking call that does not consult the job token.
            valid['value'] = False
            effects.append('external effect after lease loss')
    assert effects == ['external effect after lease loss']
    assert path.events[-3:] == ['exit delivery', 'exit lifecycle', 'exit dataset']


def test_lock_preparation_can_exhaust_budget_before_stage_admission(path, monkeypatch):
    from app.db import processing_job_repository as jobs
    from app.lambda_worker.execution_boundary import ExecutionBoundary, ExecutionStopped
    lease = jobs.JobLease(uuid4(), uuid4(), datetime.now(timezone.utc)+timedelta(seconds=120), 1)
    scope = jobs.JobExecutionScope(2, 1, 3, 4, 301, 'SILVER')
    remaining = {'value': 300000}
    monkeypatch.setattr(jobs, 'get_execution_scope', lambda *a, **kw: scope)
    monkeypatch.setattr(jobs, 'renew_job_lease', lambda *a, **kw: lease)
    @contextmanager
    def delayed_lock(*args):
        remaining['value'] = 0
        yield
    monkeypatch.setattr(path.dataset, 'dataset_lock', delayed_lock)
    control = ExecutionBoundary(lease, lambda: remaining['value'])
    with pytest.raises(ExecutionStopped):
        with control.stage(required_ms=1000):
            pytest.fail('Preparation exhausted the budget; no stage may start')
