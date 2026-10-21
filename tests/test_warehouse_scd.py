"""Pure local SCD matrices; no database, cloud, source processing or global clock."""
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, localcontext
import subprocess
import sys
from uuid import UUID

import pytest

from app.schemas.warehouse_modeling import DimensionConfiguration, SourceSchema
from app.warehouse.keys import model_surrogate_key
from app.warehouse.scd1 import plan_scd1
from app.warehouse.scd2 import plan_scd2
from app.warehouse.scd_hashing import attribute_hash, changed, state_digest
from app.warehouse.scd_invariants import validate_timeline, version_at
from app.warehouse.scd_reference import apply_plan
from app.warehouse.scd_types import (Acceptance, AcceptedOrder, Action, Authority, Code, Decision,
                                     DimensionEvent, Mode, ModelContext, ScdError, SourceContractPin)

NS = UUID('00000000-0000-0000-0000-000000000001')
JAN = datetime(2026, 1, 1, tzinfo=timezone.utc)
JUN = datetime(2026, 6, 1, tzinfo=timezone.utc)
OCT = datetime(2026, 10, 1, tzinfo=timezone.utc)


def context(scd=2, timed=True, date_domain=False, **config):
    policy = dict(column='effective', interpretation='UTC') if timed else None
    configuration = DimensionConfiguration(model_type='DIMENSION', business_keys=('id',),
        scd_type='SCD_TYPE_'+str(scd), tracked_attributes=('city',), nonhistorical_attributes=('label',),
        effective_time=policy, **config)
    schema = SourceSchema(columns=tuple(dict(name=n, data_type=t, nullable=False) for n, t in (
        ('id', 'STRING'), ('city', 'STRING'), ('label', 'STRING'), ('effective', 'DATE' if date_domain else 'TIMESTAMP'))))
    return ModelContext(1, 1, NS, configuration, schema, SourceContractPin(1, 1, 1, 1))


def event(c, at=JAN, city='Pune', label='One', **kwargs):
    return DimensionEvent.from_mapping(c, dict(id='C001', city=city, label=label, effective=at),
                                       acceptance=kwargs.pop('acceptance', Acceptance.ACCEPTED), **kwargs)


def first(c):
    planner = plan_scd1 if c.configuration.scd_type == 'SCD_TYPE_1' else plan_scd2
    plan = planner(c, None, event(c))
    return apply_plan(None, plan).state


@pytest.mark.parametrize('mode', list(Mode))
@pytest.mark.parametrize('at,city,expected', [
    (JAN, 'Pune', Decision.ALREADY_APPLIED), (JUN, 'Pune', Decision.UNCHANGED),
    (JUN, 'Mumbai', Decision.UPDATE), (JAN, 'Mumbai', Decision.CONFLICT),
    (JAN-timedelta(days=1), 'Mumbai', Decision.STALE_REJECTED),
])
def test_scd1_decision_matrix(mode, at, city, expected):
    c = context(1); state = first(c)
    authority = Authority(mode, state_digest(state) if mode == Mode.CORRECTION else None)
    if mode == Mode.CORRECTION and city != 'Pune':
        expected = Decision.UPDATE
    elif mode == Mode.BACKFILL and at < JAN:
        expected = Decision.HISTORICAL_REPAIR_REQUIRED
    plan = plan_scd1(c, state, event(c, at, city), authority)
    assert plan.decision == expected
    result = apply_plan(state, plan).state
    assert result.member_sk == state.member_sk
    assert len(result.versions) == 1 and result.versions[0].version_sk is None
    assert result.observed_time >= state.observed_time


def test_scd1_unchanged_watermark_blocks_intermediate_stale_event():
    c = context(1); state = first(c)
    plan = plan_scd1(c, state, event(c, OCT))
    assert plan.decision == Decision.UNCHANGED and plan.patches[0].action == Action.OBSERVE
    updated = apply_plan(state, plan).state
    assert updated.versions == state.versions and updated.observed_time == OCT
    assert plan_scd1(c, updated, event(c, JUN, 'Mumbai')).decision == Decision.STALE_REJECTED


def test_scd1_pinned_correction_preserves_high_watermark():
    c = context(1); state = first(c)
    state = apply_plan(state, plan_scd1(c, state, event(c, OCT, 'Mumbai'))).state
    for authority in (Authority(Mode.CORRECTION), Authority(Mode.CORRECTION, '0'*64)):
        with pytest.raises(ScdError) as error:
            plan_scd1(c, state, event(c, JUN, 'Delhi'), authority)
        assert error.value.code == Code.PRECONDITION_FAILED
    plan = plan_scd1(c, state, event(c, JUN, 'Delhi'), Authority(Mode.CORRECTION, state_digest(state)))
    result = apply_plan(state, plan).state
    assert dict(result.versions[0].tracked)['city'] == 'Delhi' and result.observed_time == OCT
    assert plan_scd1(c, result, event(c, JUN, 'Pune')).decision == Decision.STALE_REJECTED


def test_scd1_without_time_requires_trusted_order_for_changed_values():
    c = context(1, timed=False); state = first(c)
    assert state.observed_time is None
    assert plan_scd1(c, state, event(c, city='Mumbai')).code == Code.ORDERING_REQUIRED
    assert plan_scd1(c, state, event(c)).decision == Decision.ALREADY_APPLIED
    order = AcceptedOrder(NS, 10)
    state = apply_plan(None, plan_scd1(c, None, event(c, order=order))).state
    for position, expected in [(9, Decision.STALE_REJECTED), (10, Decision.CONFLICT), (11, Decision.UPDATE)]:
        assert plan_scd1(c, state, event(c, city='Mumbai', order=AcceptedOrder(NS, position))).decision == expected
    assert plan_scd1(c, state, event(c, city='Mumbai', order=AcceptedOrder(UUID(int=2), 99))).code == Code.ORDERING_REQUIRED


@pytest.mark.parametrize('mode', list(Mode))
@pytest.mark.parametrize('at,city,expected', [
    (JAN, 'Pune', Decision.ALREADY_APPLIED), (JUN, 'Pune', Decision.UNCHANGED),
    (JUN, 'Mumbai', Decision.CLOSE_AND_INSERT), (JAN, 'Mumbai', Decision.CONFLICT),
    (JAN-timedelta(days=1), 'Mumbai', Decision.HISTORICAL_REPAIR_REQUIRED),
])
def test_scd2_decision_matrix(mode, at, city, expected):
    c = context(); state = first(c)
    authority = Authority(mode, state_digest(state) if mode == Mode.CORRECTION else None)
    if mode == Mode.CORRECTION and at == JAN and city != 'Pune':
        expected = Decision.CORRECT_CURRENT
    plan = plan_scd2(c, state, event(c, at, city), authority)
    assert plan.decision == expected
    validate_timeline(apply_plan(state, plan).state)


def test_scd2_forward_chain_boundary_keys_and_golden_11b():
    c = context(); state = first(c); original = state
    for at, city in [(JUN, 'Mumbai'), (OCT, 'Bangalore')]:
        plan = plan_scd2(c, state, event(c, at, city))
        assert [p.action for p in plan.patches] == [Action.CLOSE_VERSION, Action.INSERT_VERSION]
        old = state; state = apply_plan(state, plan).state
        assert apply_plan(state, plan).decision == Decision.ALREADY_APPLIED
        assert old.versions[-1].valid_to is None  # Input never mutated.
    assert [v.valid_from for v in state.versions] == [JAN, JUN, OCT]
    assert [v.valid_to for v in state.versions] == [JUN, OCT, None]
    assert sum(v.is_current for v in state.versions) == 1
    assert len({v.version_sk for v in state.versions}) == 3
    assert {v.member_sk for v in state.versions} == {original.member_sk}
    assert version_at(state, JUN-timedelta(microseconds=1)).valid_from == JAN
    assert version_at(state, JUN).valid_from == JUN
    assert version_at(state, JAN-timedelta(days=1)) is None
    assert original.member_sk == 'b60ec522cb560ecc822384d9754a2ec9c6d283e7dda51abc98ad60f242dfc511'
    assert state.versions[1].version_sk == 'ab51609380916238099b5418f8ce8aaac3845f04728de20983a924aa3a0ef86d'
    assert state.versions[1].version_sk == model_surrogate_key(NS, c.configuration, dict(event(c, JUN).values))
    assert plan_scd2(c, state, event(c, JUN, 'Mumbai')).decision == Decision.ALREADY_APPLIED


def test_nonhistorical_patch_and_metadata_do_not_create_history():
    c = context(); state = first(c)
    plan = plan_scd2(c, state, event(c, JUN, label='Two'))
    assert plan.decision == Decision.UPDATE_NONHISTORICAL
    assert plan.changed_tracked == () and plan.changed_nonhistorical == ('label',)
    result = apply_plan(state, plan).state
    assert len(result.versions) == 1 and result.versions[0].version_sk == state.versions[0].version_sk
    assert result.versions[0].tracked_hash == state.versions[0].tracked_hash
    assert state.versions[0].nonhistorical == (('label', 'One'),)
    row = dict(event(c, JUN, label='Two').values, _datarise_attempt_id=99, s3_path='ignored')
    assert DimensionEvent.from_mapping(c, row, acceptance=Acceptance.ACCEPTED) == event(c, JUN, label='Two')


def test_unchanged_watermark_and_late_repair_do_not_split_history():
    c = context(); state = first(c)
    state = apply_plan(state, plan_scd2(c, state, event(c, OCT))).state
    for authority in (Authority(), Authority(Mode.BACKFILL), Authority(Mode.CORRECTION, state_digest(state))):
        plan = plan_scd2(c, state, event(c, JUN, 'Delhi'), authority)
        assert plan.decision == Decision.HISTORICAL_REPAIR_REQUIRED and not plan.patches
        assert plan.result == state
    assert plan_scd2(c, state, event(c, JAN, 'Delhi')).decision == Decision.CONFLICT
    assert plan_scd2(c, state, event(c)).decision == Decision.ALREADY_APPLIED


def test_same_boundary_correction_keeps_version_identity():
    c = context(); state = first(c)
    plan = plan_scd2(c, state, event(c, JAN, 'Delhi'), Authority(Mode.CORRECTION, state_digest(state)))
    result = apply_plan(state, plan).state
    assert plan.decision == Decision.CORRECT_CURRENT
    assert len(result.versions) == 1 and result.versions[0].valid_from == JAN
    assert result.versions[0].version_sk == state.versions[0].version_sk
    assert apply_plan(result, plan).decision == Decision.ALREADY_APPLIED
    assert plan_scd2(c, result, event(c, JAN, 'Delhi'),
                     Authority(Mode.CORRECTION, state_digest(state))).decision == Decision.ALREADY_APPLIED


@pytest.mark.parametrize('scd', [1, 2])
@pytest.mark.parametrize('acceptance', [Acceptance.REJECTED, Acceptance.QUARANTINED])
@pytest.mark.parametrize('mode', list(Mode))
def test_source_rejection_cannot_be_overridden(scd, acceptance, mode):
    c = context(scd); state = first(c)
    authority = Authority(mode, state_digest(state) if mode == Mode.CORRECTION else None)
    planner = plan_scd1 if scd == 1 else plan_scd2
    plan = planner(c, state, event(c, JUN, 'Delhi', acceptance=acceptance), authority)
    assert plan.decision == Decision.SOURCE_REJECTED and not plan.patches
    assert apply_plan(state, plan).state == state


def test_source_identity_is_distinct_and_reuse_conflicts():
    c = context(); e = event(c, event_id=UUID(int=10))
    plan = plan_scd2(c, None, e); state = apply_plan(None, plan).state
    assert plan.event_id == UUID(int=10) and str(plan.event_id) != plan.member_sk
    assert plan_scd2(c, state, e).decision == Decision.ALREADY_APPLIED
    assert plan_scd2(c, state, event(c, JUN, 'Mumbai', event_id=UUID(int=10))).code == Code.EVENT_ID_REUSED


@pytest.mark.parametrize('corruption', [
    'two_open', 'overlap', 'duplicate_from', 'zero_interval', 'negative_interval',
    'closed_current', 'open_not_current', 'different_member', 'duplicate_version',
    'bad_version_key', 'bad_hash', 'unsorted', 'mixed_time', 'bad_member', 'bad_observation',
])
def test_timeline_corruption_is_rejected(corruption):
    c = context(); state = first(c)
    state = apply_plan(state, plan_scd2(c, state, event(c, JUN, 'Mumbai'))).state
    a, b = state.versions
    if corruption == 'two_open': a = replace(a, valid_to=None, is_current=True)
    if corruption == 'overlap': a = replace(a, valid_to=OCT)
    if corruption == 'duplicate_from': b = replace(b, valid_from=JAN)
    if corruption == 'zero_interval': a = replace(a, valid_to=JAN)
    if corruption == 'negative_interval': a = replace(a, valid_to=JAN-timedelta(days=1))
    if corruption == 'closed_current': a = replace(a, is_current=True)
    if corruption == 'open_not_current': b = replace(b, is_current=False)
    if corruption == 'different_member': b = replace(b, member_sk='0'*64)
    if corruption == 'duplicate_version': b = replace(b, version_sk=a.version_sk)
    if corruption == 'bad_version_key': b = replace(b, version_sk='0'*64)
    if corruption == 'bad_hash': b = replace(b, tracked_hash='0'*64)
    if corruption == 'mixed_time': b = replace(b, valid_from=date(2026,6,1))
    bad = replace(state, versions=(b, a) if corruption == 'unsorted' else (a, b))
    if corruption == 'bad_member': bad = replace(bad, member_sk='0'*64)
    if corruption == 'bad_observation': bad = replace(bad, observed_time=JAN)
    with pytest.raises(ScdError): validate_timeline(bad)
    with pytest.raises(ScdError): plan_scd2(c, bad, event(c, OCT))


@pytest.mark.parametrize('domain', ['DATE', 'TIMESTAMP'])
def test_time_domains_are_resolved_and_distinct(domain):
    c = context(date_domain=domain == 'DATE')
    start, end = (date(2026,1,1), date(2026,6,1)) if domain == 'DATE' else (JAN, JUN)
    state = apply_plan(None, plan_scd2(c, None, event(c, start))).state
    result = apply_plan(state, plan_scd2(c, state, event(c, end, 'Mumbai'))).state
    assert version_at(result, end).valid_from == end
    for invalid in (datetime(2026,1,1), None, '2026-01-01', JAN if domain == 'DATE' else date(2026,1,1)):
        with pytest.raises(ScdError): plan_scd2(c, None, event(c, invalid))


def test_timezone_offset_equivalence_and_source_timezone_never_resolves_naive():
    c = context(); india = JAN.astimezone(timezone(timedelta(hours=5, minutes=30)))
    assert plan_scd2(c, None, event(c, india)).result == plan_scd2(c, None, event(c, JAN)).result
    config = c.configuration.model_copy(update={'effective_time': c.configuration.effective_time.model_copy(
        update={'interpretation':'SOURCE_TIMEZONE', 'timezone':'Asia/Kolkata'})})
    local = replace(c, configuration=config)
    with pytest.raises(ScdError): plan_scd2(local, None, event(local, datetime(2026,1,1)))


def test_preconditions_atomicity_and_model_pin_mismatch():
    c = context(); state = first(c)
    june = plan_scd2(c, state, event(c, JUN, 'Mumbai'))
    october = plan_scd2(c, state, event(c, OCT, 'Delhi'))
    advanced = apply_plan(state, october).state
    with pytest.raises(ScdError) as error: apply_plan(advanced, june)
    assert error.value.code == Code.PRECONDITION_FAILED
    assert advanced == october.result and state.versions[0].valid_to is None
    with pytest.raises(ScdError): apply_plan(state, replace(june, patches=june.patches[:1]))
    for bad_context in (replace(c, model_id=2), replace(c, model_version_id=2), replace(c, namespace=UUID(int=2)),
                        replace(c, source_contract=SourceContractPin(2, 1, 1, 1))):
        with pytest.raises(ScdError): plan_scd2(bad_context, state, event(bad_context, JUN))
    with pytest.raises(ScdError): plan_scd1(c, state, event(c))


def test_deterministic_plans_and_immutable_inputs():
    c = context(); row = dict(id='C001', city='Pune', label='One', effective=JAN)
    e = DimensionEvent.from_mapping(c, row, acceptance=Acceptance.ACCEPTED)
    other = DimensionEvent.from_mapping(c, dict(reversed(list(row.items()))), acceptance=Acceptance.ACCEPTED)
    plan = plan_scd2(c, None, e)
    assert plan == plan_scd2(c, None, other) == plan_scd2(c, None, e)
    row['city'] = 'Changed after construction'
    assert plan.result.versions[0].tracked == (('city', 'Pune'),)
    with pytest.raises(FrozenInstanceError): plan.result.member_sk = 'altered'
    code = "from tests.test_warehouse_scd import *; print(state_digest(plan_scd2(context(),None,event(context())).result))"
    assert subprocess.check_output([sys.executable, '-c', code], text=True).strip() == state_digest(plan.result)


@pytest.mark.parametrize('value', [1.0, float('nan'), Decimal('NaN'), [], {}, b'raw'])
def test_unstable_or_untyped_attributes_fail_safely(value):
    c = context()
    with pytest.raises(ScdError) as error: plan_scd2(c, None, event(c, city=value))
    assert error.value.code == Code.INVALID_INPUT
    assert str(error.value) == Code.INVALID_INPUT.value


def test_exact_hash_golden_and_field_scope():
    assert attribute_hash((('city', 'Pune'),)) == '43b46f6ad726157a60d90afdfa6fd217faa3e5ff9ee322e86025762bb2be2afc'
    assert attribute_hash((('a', 1), ('b', True))) == attribute_hash((('b', True), ('a', 1)))
    assert len({attribute_hash((('v', v),)) for v in [1, True, '1', Decimal('1')]}) == 4
    assert changed((('v', 1),), (('v', True),)) == ('v',)
    with localcontext() as ctx:
        ctx.prec = 2
        assert attribute_hash((('v', Decimal('123.4500')),)) == attribute_hash((('v', Decimal('123.45')),))
    c = context(); one = plan_scd2(c, None, event(c)).result.versions[0]
    e = DimensionEvent.from_mapping(c, dict(id='C999', city='Pune', label='Different', effective=JAN), acceptance=Acceptance.ACCEPTED)
    two = plan_scd2(c, None, e).result.versions[0]
    assert one.tracked_hash == two.tracked_hash and one.member_sk != two.member_sk


@pytest.mark.parametrize('values,expected', [
    ((('text', 'e\u0301客户'),), 'e7094f95605914ca7d7732d6ffb796ec8cb886a4da388603291fc2d1e2018a83'),
    ((('n', Decimal('12345678901234567890.123400')),), 'db585c9171718fffa0de0eb1532b16dbb032eb8536d1ddb7c67b4973ee999796'),
    ((('day', date(2026,1,1)), ('time', JUN)), '5413355b9ceecaf42c427db1a9e74070612462c44d8719b0b650866a938f0b8e'),
    ((('b', True), ('i', 1), ('null', None)), 'f555f4a778e1b10f7f496e5ffacd68cb3f04fa921e8c7ddc6688d966c02660fb'),
])
def test_literal_attribute_hash_vectors(values, expected):
    assert attribute_hash(values) == expected


def test_adapter_rejects_relabelled_or_mutated_patches():
    c = context(); state = first(c)
    plan = plan_scd2(c, state, event(c, JUN, 'Mumbai'))
    bad_close = replace(plan.patches[0], action=Action.UPDATE_NONHISTORICAL)
    with pytest.raises(ScdError): apply_plan(state, replace(plan, patches=(bad_close, plan.patches[1])))
    bad_close = replace(plan.patches[0], after=replace(plan.patches[0].after, nonhistorical=(('label', 'Altered'),)))
    with pytest.raises(ScdError): apply_plan(state, replace(plan, patches=(bad_close, plan.patches[1])))
    advanced = apply_plan(state, plan).state
    observed = plan_scd2(c, advanced, event(c, OCT, 'Mumbai'))
    with pytest.raises(ScdError): apply_plan(advanced, replace(observed, patches=()))


def test_nullable_attribute_support_and_explicit_schema_type_enforcement():
    c = context()
    columns = tuple(x.model_copy(update={'nullable':True}) if x.name == 'city' else x for x in c.source_schema.columns)
    c = replace(c, source_schema=c.source_schema.model_copy(update={'columns':columns}))
    state = apply_plan(None, plan_scd2(c, None, event(c, city=None))).state
    assert state.versions[0].tracked == (('city', None),)
    with pytest.raises(ScdError): plan_scd2(c, None, event(c, city=1))
    with pytest.raises(ScdError): plan_scd2(c, None, replace(event(c), values=(('id','C001'),)))


def test_no_implicit_inference_or_absence_execution():
    c = context(missing_member_policy='INFER_MEMBER', snapshot_absence_policy='DEACTIVATE_ON_COMPLETE')
    plan = plan_scd2(c, None, event(c))
    assert plan.decision == Decision.INSERT_INITIAL_VERSION and len(plan.result.versions) == 1
    assert plan.result.versions[0].valid_from == JAN  # Actual accepted member, not inferred/prehistory.


def test_context_and_authority_are_typed_and_fail_closed():
    c = context()
    for change in ({'key_algorithm_version':2}, {'model_id':False}, {'model_version_id':0}):
        with pytest.raises(ScdError): replace(c, **change)
    with pytest.raises(ScdError): plan_scd2(c, None, event(c), Authority('CORRECTION'))
    with pytest.raises(ScdError): plan_scd2(c, None, replace(event(c), acceptance=True))


def test_closed_history_is_not_implicitly_reopened_or_corrected():
    c = context(); state = first(c)
    closed = replace(state, versions=(replace(state.versions[0], valid_to=JUN, is_current=False),))
    validate_timeline(closed)
    for at in (JAN, JUN, OCT):
        for mode in Mode:
            plan = plan_scd2(c, closed, event(c, at, 'Mumbai'),
                             Authority(mode, state_digest(closed) if mode == Mode.CORRECTION else None))
            assert plan.decision == Decision.HISTORICAL_REPAIR_REQUIRED and not plan.patches


def test_observation_patch_cannot_target_wrong_version_or_regress_watermark():
    c = context(); state = first(c)
    state = apply_plan(state, plan_scd2(c, state, event(c, JUN))).state
    plan = plan_scd2(c, state, event(c, OCT))
    with pytest.raises(ScdError):
        apply_plan(state, replace(plan, patches=(replace(plan.patches[0], version_sk='0'*64),)))
    with pytest.raises(ScdError):
        apply_plan(state, replace(plan, result=replace(plan.result, observed_time=JAN)))


def test_initial_pinned_correction_and_untimed_nonhistorical_scd1_update():
    c = context(1, timed=False)
    e = event(c, order=AcceptedOrder(NS, 0))
    plan = plan_scd1(c, None, e, Authority(Mode.CORRECTION, state_digest(None)))
    assert plan.decision == Decision.INSERT
    state = apply_plan(None, plan).state
    newer = event(c, label='Two', order=AcceptedOrder(NS, 1))
    plan = plan_scd1(c, state, newer)
    assert plan.decision == Decision.UPDATE and plan.changed_tracked == ()
    assert plan.changed_nonhistorical == ('label',)
    assert apply_plan(state, plan).state.member_sk == state.member_sk


def test_all_forward_chains_keep_invariants_and_retry_stability():
    c = context(); state = None
    for i in range(12):
        e = event(c, JAN+timedelta(days=i), city=('Pune', 'Mumbai', 'Delhi')[i % 3], event_id=UUID(int=i+1))
        plan = plan_scd2(c, state, e)
        state = apply_plan(state, plan).state
        assert plan_scd2(c, state, e).decision == Decision.ALREADY_APPLIED
        assert apply_plan(state, plan).state == state
        validate_timeline(state)
    assert len(state.versions) == 12
