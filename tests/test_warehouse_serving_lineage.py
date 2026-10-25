"""Malformed immutable metadata cannot select unrelated or incomplete candidates."""
from copy import deepcopy

import pytest

from app.services import warehouse_provider_service as service
from app.warehouse.providers.base import Binding, ProviderError
from app.warehouse.serving_materialization import materialization_identity


def lineage(monkeypatch, count=2):
    binding = Binding('synthetic-project', 1, 2, 3, 4, 5)
    scope = dict(workspace_id=1, warehouse_model_id=2, warehouse_model_version_id=3, resource_id=4)
    pubs = {i: dict(publication_id=i, revision=i, operation_id=i, resource_id=4,
                   previous_publication_id=i-1 if i>1 else None) for i in range(1, count+1)}
    intents = {i: dict(scope, operation_id=i, generation=5,
        body=dict(prior_head=i-1 if i>1 else None, prior_revision=i-1)) for i in pubs}
    ops = {i: dict(scope, environment='development') for i in pubs}
    monkeypatch.setattr(service.db, 'one', lambda conn, sql, args: pubs.get(args[0])
                        if pubs.get(args[0], {}).get('resource_id') == args[1] else None)
    monkeypatch.setattr(service.db, 'intent', lambda conn, op: intents.get(op))
    monkeypatch.setattr(service.metadata, 'operation', lambda conn, w, op: ops.get(op))
    return service.ProviderService(1,2,3,{}), binding, pubs, intents, ops


@pytest.mark.parametrize('corruption', ['missing_predecessor', 'missing_intent', 'resource', 'generation',
    'workspace', 'model', 'version', 'operation_scope', 'environment', 'revision', 'cycle', 'prior_pin'])
def test_lineage_rejects_missing_or_cross_scope_metadata(monkeypatch, corruption):
    provider, binding, pubs, intents, ops = lineage(monkeypatch)
    head = deepcopy(pubs[2])
    if corruption == 'missing_predecessor': del pubs[1]
    if corruption == 'missing_intent': del intents[1]
    if corruption == 'resource': pubs[1]['resource_id'] = 99
    if corruption == 'generation': intents[1]['generation'] = 99
    if corruption == 'workspace': intents[1]['workspace_id'] = 99
    if corruption == 'model': intents[1]['warehouse_model_id'] = 99
    if corruption == 'version': intents[1]['warehouse_model_version_id'] = 99
    if corruption == 'operation_scope': ops[1]['resource_id'] = 99
    if corruption == 'environment': ops[1]['environment'] = 'production'
    if corruption == 'revision': pubs[1]['revision'] = 9
    if corruption == 'cycle':
        pubs[1]['previous_publication_id'] = 2
        intents[1]['body']['prior_head'] = 2
    if corruption == 'prior_pin': intents[1]['body']['prior_revision'] = 5
    with pytest.raises(ProviderError): provider._published_lineage(None, binding, head)


def test_exact_lineage_depth_and_deterministic_order(monkeypatch):
    provider, binding, pubs, intents, ops = lineage(monkeypatch, 100)
    assert [p['publication_id'] for p, _ in provider._published_lineage(None, binding, pubs[100])] == list(range(100,0,-1))
    provider, binding, pubs, intents, ops = lineage(monkeypatch, 101)
    with pytest.raises(ValueError, match='compaction/rebuild'): provider._published_lineage(None, binding, pubs[101])


@pytest.mark.parametrize('pub,revision', [(0,1),(-1,1),(True,1),(1,0),(1,-1),(1,False),(1,'2')])
def test_publication_name_requires_positive_native_integers(pub, revision):
    with pytest.raises(ValueError): Binding('synthetic-project',1,2,3,4,5).publication_table(pub, revision)


def test_publication_name_is_exact_and_generation_pinned():
    b = Binding('synthetic-project',1,20,30,40,1)
    assert b.publication_physical(50,7) == 'synthetic-project.dr_dev_w1.m20_v30_g1_p50_r7'
    assert b.publication_table(54,8) != b.publication_table(50,7)


def test_complete_overlay_enforces_aggregate_byte_budget_before_accumulation():
    import json
    from types import SimpleNamespace
    from app.warehouse.providers import model_adapter
    from app.warehouse.providers.base import Policy
    from app.warehouse.providers.planning import compile_dimension
    from tests.test_warehouse_bigquery import evidence
    from tests.test_warehouse_scd import context
    c = context()
    a = compile_dimension(c, [evidence(c)])
    b = compile_dimension(c, [evidence(c, id='C002')])
    states = (a.after[0], b.after[0])
    limit = max(len(json.dumps(model_adapter.rows_from_states(c, (s,))).encode()) for s in states) + 1
    target = SimpleNamespace(binding=Binding('synthetic-project',1,1,1,1,1),
        policy=Policy('synthetic-project', enabled=True, maximum_payload_bytes=limit),
        read_published_candidate=lambda *args: states)
    captured = ((dict(operation_id=1), dict(application_token='a'*64, intent_sha256='b'*64,
                body=dict(manifests=[], expected=a.result.model_dump()))),)
    with pytest.raises(ProviderError, match='COST_LIMIT_EXCEEDED'):
        service.ProviderService(1,1,1,{})._complete_published_state(target, c, captured)
