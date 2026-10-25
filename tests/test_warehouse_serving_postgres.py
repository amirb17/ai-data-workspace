"""Complete publication materialization through real disposable PostgreSQL + fake BQ."""
from copy import deepcopy
from datetime import timedelta

import pytest
from fastapi import HTTPException

from tests.test_warehouse_foundation import pg
from tests.test_warehouse_modeling import modeling_pg
from tests.test_warehouse_provider_postgres import fact_provider_pg, provider_pg, work, publish
from tests.test_metric_warehouse_binding_postgres import metric_plan
from tests.test_warehouse_reliability import NOW
from app.db import warehouse_reliability_repository as db
from app.db.database import _transaction_connection
from app.services.warehouse_provider_service import ProviderService
from app.services.metric_warehouse_binding_service import resolve_metric_warehouse_sources
from app.warehouse.providers.base import ProviderError, Binding
from app.warehouse.providers import model_adapter


def row(identity, value=1, at=NOW):
    return dict(id=identity, line=value, segment=f'Value{value}', label='L', effective=at, float_id=1.5)


def publication(f, records, *, key='first', at=NOW):
    intent, claim, plan = work(f, records, key=key, effective=at)
    response = f.provider.execute(intent['operation_id'], claim['run_id'], claim['fence'])
    return publish(f, intent, claim, response)


def resolved(f):
    with f.pg['connect']() as conn:
        return resolve_metric_warehouse_sources(conn, metric_plan(f.pg['workspace'], f.pg['dataset'], f.state))[f.pg['dataset']]


def serving_rows(f, receipt):
    binding = f.targets[-1].binding
    name = binding.publication_physical(receipt['publication_id'], receipt['publication_revision'])
    return name, f.cloud.rows[name]


@pytest.mark.parametrize('fixture', ['fact_provider_pg', 'provider_pg'])
def test_full_state_incremental_preserves_unaffected_identity_and_old_publication(fixture, request):
    f = request.getfixturevalue(fixture)
    publication(f, [row('A'), row('B')])
    with pytest.raises(ValueError, match='WAREHOUSE_SERVING_NOT_READY'): resolved(f)
    first = f.provider.materialize_current_publication(f.resource)
    old_name, old_rows = serving_rows(f, first)
    frozen = deepcopy(old_rows)
    assert first == f.provider.materialize_current_publication(f.resource)
    old_source = resolved(f)
    assert old_source.physical_table == old_name
    later = NOW + timedelta(days=180)
    publication(f, [row('B', 2, later), row('C', 1, later)], key='second', at=later)
    with pytest.raises(ValueError, match='WAREHOUSE_SERVING_NOT_READY'): resolved(f)
    second = f.provider.materialize_current_publication(f.resource)
    new_name, new_rows = serving_rows(f, second)
    assert old_name != new_name and f.cloud.rows[old_name] == frozen
    assert old_source.physical_table == old_name  # An already compiled source stays pinned.
    assert resolved(f).physical_table == new_name
    with f.service.transaction() as (conn, source, config, settings):
        _, typed = f.provider._resolve(conn, config, settings, f.resource)
    before = model_adapter.states(typed, old_rows)
    after = model_adapter.states(typed, new_rows)
    keys = lambda s: dict(s.identity if fixture == 'fact_provider_pg' else s.business_keys)['id']
    a, b = {keys(s): s for s in before}, {keys(s): s for s in after}
    assert set(a) == {'A', 'B'} and set(b) == {'A', 'B', 'C'}
    assert a['A'] == b['A']
    if fixture == 'fact_provider_pg':
        assert a['B'].fact_sk == b['B'].fact_sk
        assert dict(b['B'].measures)['line'] == 2
    else:
        assert a['B'].member_sk == b['B'].member_sk
        assert len(b['B'].versions) == 2
        assert a['B'].versions[0].version_sk == b['B'].versions[0].version_sk
    with f.pg['connect']() as conn:
        assert conn.execute('SELECT count(*) FROM warehouse_serving_materializations').fetchone()[0] == 2
    assert len([x for x in f.cloud.calls if x[0] == 'load' and x[1] == old_name]) == 1


def test_end_to_end_lost_load_response_recovers_one_receipt_one_load(fact_provider_pg):
    f = fact_provider_pg
    publication(f, [row('A'), row('B')])
    f.cloud.lose_load_response = True
    with pytest.raises(ProviderError) as caught: f.provider.materialize_current_publication(f.resource)
    assert caught.value.uncertain
    with f.pg['connect']() as conn:
        assert conn.execute('SELECT count(*) FROM warehouse_serving_materializations').fetchone()[0] == 0
    with pytest.raises(ValueError, match='WAREHOUSE_SERVING_NOT_READY'): resolved(f)
    receipt = f.provider.materialize_current_publication(f.resource)
    name, rows = serving_rows(f, receipt)
    assert resolved(f).physical_table == name
    assert len(rows) == 2
    assert len([x for x in f.cloud.calls if x[0] == 'load' and x[1] == name]) == 1
    assert receipt == f.provider.materialize_current_publication(f.resource)


def test_head_advancement_during_cloud_work_cannot_record_stale_readiness(fact_provider_pg):
    f = fact_provider_pg
    old = publication(f, [row('A')])
    factory = f.provider.target_factory
    advanced = False
    def wrapped(policy, binding):
        target = factory(policy, binding)
        materialize = target.materialize_serving
        def race(request):
            nonlocal advanced
            assert _transaction_connection.get() is None
            result = materialize(request)
            if not advanced:
                advanced = True
                later = NOW + timedelta(days=180)
                publication(f, [row('B', at=later)], key='second', at=later)
            return result
        target.materialize_serving = race
        read = target.read_published_candidate
        def outside(*args):
            if getattr(args[0], 'manifest_evidence', None) is not None:
                assert _transaction_connection.get() is None
            return read(*args)
        target.read_published_candidate = outside
        return target
    f.provider.target_factory = wrapped
    with pytest.raises(ProviderError): f.provider.materialize_current_publication(f.resource)
    with f.pg['connect']() as conn:
        assert conn.execute('SELECT count(*) FROM warehouse_serving_materializations').fetchone()[0] == 0
    assert any(f'_p{old["publication_id"]}_r1' in name for name in f.cloud.tables)
    with pytest.raises(ValueError, match='WAREHOUSE_SERVING_NOT_READY'): resolved(f)
    receipt = f.provider.materialize_current_publication(f.resource)
    assert receipt['publication_revision'] == 2
    assert resolved(f).publication_revision == 2


def test_ownership_is_rechecked_after_physical_success(fact_provider_pg):
    f = fact_provider_pg
    publication(f, [row('A')])
    factory = f.provider.target_factory
    def wrapped(policy, binding):
        target = factory(policy, binding)
        materialize = target.materialize_serving
        def transfer(request):
            result = materialize(request)
            with f.pg['connect']() as conn:
                conn.execute('UPDATE workspaces SET owner=%s WHERE workspace_id=%s',
                             (f.pg['other']['owner_key'], f.pg['workspace']))
            return result
        target.materialize_serving = transfer
        return target
    f.provider.target_factory = wrapped
    with pytest.raises(HTTPException): f.provider.materialize_current_publication(f.resource)
    with f.pg['connect']() as conn:
        assert conn.execute('SELECT count(*) FROM warehouse_serving_materializations').fetchone()[0] == 0


@pytest.mark.parametrize('conflict', [False, True])
def test_concurrent_receipt_conflict_recovers_only_exact_evidence(fact_provider_pg, monkeypatch, conflict):
    f = fact_provider_pg
    publication(f, [row('A')])
    receipt = f.provider.materialize_current_publication(f.resource)
    evidence = {k: receipt[k] for k in db.SERVING_EVIDENCE_FIELDS}
    if conflict: evidence['serving_sha256'] = 'f'*64
    original = db.serving_materialization
    calls = 0
    def stale_first(*args):
        nonlocal calls
        calls += 1
        return None if calls == 1 else original(*args)
    monkeypatch.setattr(db, 'serving_materialization', stale_first)
    with f.pg['connect']() as conn:
        conn.execute('SELECT 1')  # Duplicate insert rolls back only its savepoint.
        if conflict:
            with pytest.raises(ProviderError): db.record_serving_materialization(conn, evidence)
        else:
            assert db.record_serving_materialization(conn, evidence) == receipt
        assert conn.execute('SELECT count(*) FROM warehouse_serving_materializations').fetchone()[0] == 1
