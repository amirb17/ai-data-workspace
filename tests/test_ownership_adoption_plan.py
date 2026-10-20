"""Gate planning, inverse mapping and replay without database writes or migration execution."""
from copy import deepcopy
import pytest
from app.services.ownership_adoption_plan import AdoptionConflict, prepare_manifest, plan, simulate


def snapshot():
    return {'target': {'user_id': 34965, 'owner_key': 'usr:target', 'email_verified': True, 'account_status': 'ACTIVE'},
            'workspaces': [{'workspace_id': 4, 'owner': 'other'}, {'workspace_id': 7, 'owner': 'dev:old'}],
            'datasets': [{'dataset_id': 5, 'workspace_id': 4, 'owner': 'other'},
                         {'dataset_id': 8, 'workspace_id': 7, 'owner': 'dev:old'}],
            'invariants': {'datasets': {'count': 2, 'sha256': 'state-and-content-fingerprint'}}}


def test_exact_scope_and_deterministic_order_independent_identity():
    before = snapshot(); manifest = prepare_manifest(before, 'dev:old', 34965)
    reordered = deepcopy(before); reordered['datasets'].reverse(); reordered['workspaces'].reverse()
    assert prepare_manifest(reordered, 'dev:old', 34965) == manifest
    assert manifest['resource_scope'] == {'7': [8]}
    assert [(r['table'], r['id']) for r in plan(manifest, before)['updates']] == [('workspaces', 7), ('datasets', 8)]


def test_no_state_mutation_and_successful_replay_with_zero_duplicates():
    before = snapshot(); original = deepcopy(before); manifest = prepare_manifest(before, 'dev:old', 34965)
    after, events, first = simulate(manifest, before)
    assert before == original and first['status'] == 'WOULD_ADOPT'
    assert after['invariants'] == before['invariants'] and after['workspaces'][0] == before['workspaces'][0]
    replay = plan(manifest, after, events)
    assert replay == {'status': 'ALREADY_ADOPTED', 'updates': [], 'provenance_inserts': []}
    after['invariants']['datasets']['count'] = 3
    assert plan(manifest, after, events)['status'] == 'ALREADY_ADOPTED', 'Replay cannot duplicate a completed transfer after legitimate later writes'


def test_exact_inverse_and_append_only_reversal_replay():
    before = snapshot(); manifest = prepare_manifest(before, 'dev:old', 34965)
    after, events, _ = simulate(manifest, before)
    restored, reversed_events, inverse = simulate(manifest, after, events, 'REVERSE')
    assert restored == before and len(reversed_events) == 2 and reversed_events[0] == events[0]
    assert all(row['before'] == 'usr:target' and row['after'] == 'dev:old' for row in inverse['updates'])
    assert plan(manifest, restored, reversed_events, 'REVERSE')['status'] == 'ALREADY_REVERSED'
    with pytest.raises(AdoptionConflict): plan(manifest, restored, reversed_events)


@pytest.mark.parametrize('change', ['partial_owner', 'membership', 'business_state', 'target', 'new_source_resource'])
def test_changed_or_partial_scope_fails_closed(change):
    current = snapshot(); manifest = prepare_manifest(current, 'dev:old', 34965)
    if change == 'partial_owner': current['datasets'][1]['owner'] = 'usr:target'
    elif change == 'membership': current['datasets'][1]['workspace_id'] = 4
    elif change == 'business_state': current['invariants']['datasets']['sha256'] = 'changed'
    elif change == 'target': current['target']['email_verified'] = False
    else: current['datasets'].append({'dataset_id': 9, 'workspace_id': 7, 'owner': 'dev:old'})
    with pytest.raises(AdoptionConflict): plan(manifest, current)


def test_rollback_refuses_intervening_business_writes():
    current = snapshot(); manifest = prepare_manifest(current, 'dev:old', 34965)
    after, events, _ = simulate(manifest, current)
    after['invariants']['datasets']['sha256'] = 'new-data'
    with pytest.raises(AdoptionConflict): plan(manifest, after, events, 'REVERSE')


def test_missing_or_conflicting_provenance_never_grants_adoption():
    current = snapshot(); manifest = prepare_manifest(current, 'dev:old', 34965)
    after, events, _ = simulate(manifest, current)
    with pytest.raises(AdoptionConflict): plan(manifest, after)
    with pytest.raises(AdoptionConflict): plan(manifest, after, [*events, *events])
    events[0]['source_principal'] = 'foreign'
    with pytest.raises(AdoptionConflict): plan(manifest, after, events)


def test_target_conflict_and_unowned_child_are_not_silently_adopted():
    current = snapshot(); current['workspaces'][0]['owner'] = 'usr:target'
    with pytest.raises(AdoptionConflict): prepare_manifest(current, 'dev:old', 34965)
    current = snapshot(); current['datasets'][1]['owner'] = 'other'
    with pytest.raises(AdoptionConflict): prepare_manifest(current, 'dev:old', 34965)


def test_manifest_tampering_and_unexecuted_rollback_fail_closed():
    current = snapshot(); manifest = prepare_manifest(current, 'dev:old', 34965)
    with pytest.raises(AdoptionConflict): plan(manifest, current, operation='REVERSE')
    manifest['resource_scope']['4'] = [5]
    with pytest.raises(AdoptionConflict): plan(manifest, current)
