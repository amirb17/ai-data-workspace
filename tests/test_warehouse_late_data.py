"""Bounded pure late-data matrices, no cloud/DB/production fact mutation."""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from itertools import permutations
import subprocess
import sys
from uuid import UUID

import pytest

from tests.test_warehouse_scd import context, event, first, JAN, JUN, OCT, NS
from app.warehouse.keys import surrogate_key
from app.warehouse.fact_resolution import resolve_fact, resolution_digest
from app.warehouse.fact_reresolution import (affected_ranges, plan_reresolution, apply_reresolution, reresolution_digest)
from app.warehouse.inferred_members import (plan_inference, apply_inference, inferred_digest,
                                            plan_completion, apply_completion)
from app.warehouse.scd2 import plan_scd2
from app.warehouse.scd_hashing import state_digest
from app.warehouse.scd_invariants import validate_timeline, version_at
from app.warehouse.scd_reference import apply_plan
from app.warehouse.scd_repair import plan_repair, repair_digest
from app.warehouse.scd_types import (Acceptance, Action, Authority, BeforeHistoryPolicy, Code, Decision,
                                     FactReference, InferenceDecision, InferredStatus, Mode, RebindDecision,
                                     ResolutionStatus, ScdError, SourceContractPin, TimeRange)

MAR = datetime(2026, 3, 1, tzinfo=timezone.utc)
APR = datetime(2026, 4, 1, tzinfo=timezone.utc)
MAY = datetime(2026, 5, 1, tzinfo=timezone.utc)


def history(c=None):
    c = c or context()
    state = first(c)
    return apply_plan(state, plan_scd2(c, state, event(c, JUN, 'Mumbai'))).state


def fact(c, at=APR, key='C001', identity=1, **kwargs):
    return FactReference.from_mapping(
        # Use existing deterministic fact identity rules; do not add another fact key algorithm.
        surrogate_key(NS, 'FACT', ['order_id'], {'order_id':identity}), UUID(int=10), c,
        {'id':key}, at, acceptance=kwargs.pop('acceptance', Acceptance.ACCEPTED),
        source_contract=SourceContractPin(2, 2, 2, 1), fact_event_id=UUID(int=identity), **kwargs)


def repair(state, at, city, authority=Authority(), **kwargs):
    plan = plan_repair(state.context, state, event(state.context, at, city, **kwargs), authority)
    return plan, apply_plan(state, plan.transition).state


def test_middle_split_preserves_member_predecessor_successor_and_exact_bounds():
    state = history(); old, future = state.versions
    plan, result = repair(state, MAR, 'Delhi')
    assert plan.transition.decision == Decision.HISTORICAL_SPLIT
    assert [p.action for p in plan.transition.patches] == [Action.SHORTEN_INTERVAL, Action.INSERT_HISTORICAL]
    assert [v.valid_from for v in result.versions] == [JAN, MAR, JUN]
    assert [v.valid_to for v in result.versions] == [MAR, JUN, None]
    assert result.versions[0].version_sk == old.version_sk and result.versions[2] == future
    assert {v.member_sk for v in result.versions} == {state.member_sk}
    assert result.versions[1].version_sk == surrogate_key(NS, 'DIMENSION_VERSION', ['id'], {'id':'C001'}, valid_from=MAR)
    assert result.observed_time == state.observed_time and result.last_event_digest == state.last_event_digest
    assert plan.affected_ranges == (TimeRange(MAR, JUN),)
    assert plan.transition.changed_tracked == ('city',) and not plan.transition.changed_nonhistorical
    assert apply_plan(result, plan.transition).decision == Decision.ALREADY_APPLIED
    assert plan_repair(state.context, result, event(state.context, MAR, 'Delhi')).transition.decision == Decision.ALREADY_APPLIED
    assert state.versions[0].valid_to == JUN


def test_before_first_extends_only_from_actual_evidence_and_explicit_rejection():
    c = context(); original = history(c)
    state = replace(original, versions=(original.versions[-1],))
    plan, result = repair(state, MAR, 'Delhi')
    assert plan.transition.decision == Decision.HISTORICAL_INSERT
    assert plan.earliest_authoritative_valid_from == MAR
    assert result.versions[0].valid_from == MAR and result.versions[0].valid_to == JUN
    assert version_at(result, JAN) is None
    denied = plan_repair(c, state, event(c, MAR, 'Delhi'), before_history=BeforeHistoryPolicy.REJECT)
    assert denied.transition.decision == Decision.BEFORE_HISTORY_REJECTED and not denied.transition.patches


def test_gap_insert_does_not_stretch_predecessor_or_invent_earlier_coverage():
    state = history(); a, b = state.versions
    state = replace(state, versions=(replace(a, valid_to=MAR), replace(b, valid_from=MAY,
        version_sk=surrogate_key(NS,'DIMENSION_VERSION',['id'],{'id':'C001'},valid_from=MAY))))
    validate_timeline(state)
    plan, result = repair(state, APR, 'Delhi')
    assert plan.transition.decision == Decision.GAP_INSERT
    assert result.versions[0] == state.versions[0] and result.versions[-1] == state.versions[-1]
    assert version_at(result, MAR) is None
    assert version_at(result, APR).valid_to == MAY
    assert resolve_fact(fact(state.context, MAR), result).status == ResolutionStatus.NO_VERSION_AT_EVENT_TIME


@pytest.mark.parametrize('mode', list(Mode))
@pytest.mark.parametrize('at', [JAN, MAR, JUN])
@pytest.mark.parametrize('payload', ['same', 'tracked', 'nonhistorical'])
def test_repair_relation_payload_authority_matrix(mode, at, payload):
    state = history(); c = state.context
    city = 'Mumbai' if at == JUN else 'Pune'
    label = 'One'
    if payload == 'tracked': city = 'Delhi'
    if payload == 'nonhistorical': label = 'Two'
    authority = Authority(mode, state_digest(state) if mode == Mode.CORRECTION else None)
    plan = plan_repair(c, state, event(c, at, city, label), authority)
    if at == MAR:
        expected = {'same':Decision.UNCHANGED,'tracked':Decision.HISTORICAL_SPLIT,
                    'nonhistorical':Decision.DEFERRED_NONHISTORICAL}[payload]
    elif payload == 'same': expected = Decision.ALREADY_APPLIED
    elif mode == Mode.CORRECTION: expected = Decision.HISTORICAL_REPLACE
    elif payload == 'nonhistorical': expected = Decision.DEFERRED_NONHISTORICAL
    else: expected = Decision.CONFLICT
    assert plan.transition.decision == expected
    result = apply_plan(state, plan.transition).state
    validate_timeline(result)
    if expected == Decision.HISTORICAL_REPLACE:
        assert [(v.version_sk,v.valid_from,v.valid_to) for v in result.versions] == [(v.version_sk,v.valid_from,v.valid_to) for v in state.versions]
        retry = plan_repair(c, result, event(c, at, city, label), authority)
        assert retry.transition.decision == Decision.ALREADY_APPLIED


@pytest.mark.parametrize('sequence', list(permutations([(MAR,'Bangalore'),(APR,'Delhi'),(MAY,'Hyderabad')])))
def test_independent_late_repairs_are_arrival_order_independent(sequence):
    state = history()
    for at, city in sequence:
        _, state = repair(state, at, city)
    reference = history()
    for at, city in [(MAR,'Bangalore'),(APR,'Delhi'),(MAY,'Hyderabad')]: _, reference = repair(reference, at, city)
    assert state == reference and state_digest(state) == state_digest(reference)
    for at, expected in [(JAN,'Pune'),(MAR,'Bangalore'),(MAR+timedelta(days=14),'Bangalore'),
                         (APR,'Delhi'),(APR+timedelta(days=14),'Delhi'),(MAY,'Hyderabad'),
                         (MAY+timedelta(days=14),'Hyderabad'),(JUN,'Mumbai')]:
        resolved = resolve_fact(fact(state.context, at), state)
        assert resolved.version_sk == version_at(state, at).version_sk
        assert dict(version_at(state, at).tracked)['city'] == expected


def test_historical_correction_is_pinned_and_preserves_version_identity():
    state = history(); c = state.context
    for expected in (None, '0'*64):
        plan = plan_repair(c, state, event(c, JAN, 'Delhi'), Authority(Mode.CORRECTION, expected))
        assert plan.transition.decision == Decision.REPAIR_NOT_AUTHORIZED
    plan, result = repair(state, JAN, 'Delhi', Authority(Mode.CORRECTION, state_digest(state)))
    assert plan.transition.decision == Decision.HISTORICAL_REPLACE
    assert result.versions[-1] == state.versions[-1]
    assert result.versions[0].version_sk == state.versions[0].version_sk
    assert result.versions[0].valid_to == JUN


@pytest.mark.parametrize('acceptance', [Acceptance.REJECTED, Acceptance.QUARANTINED])
@pytest.mark.parametrize('mode', list(Mode))
def test_rejected_dimension_evidence_is_not_resurrected(acceptance, mode):
    state = history(); c = state.context
    plan = plan_repair(c,state,event(c,MAR,'Delhi',acceptance=acceptance),Authority(mode,state_digest(state)))
    assert plan.transition.decision == Decision.SOURCE_REJECTED and not plan.transition.patches


def test_current_observation_coverage_and_closed_tail_fail_conservatively():
    c = context(); state = first(c)
    state = apply_plan(state,plan_scd2(c,state,event(c,OCT))).state
    plan = plan_repair(c,state,event(c,MAR,'Delhi'))
    assert plan.transition.code == Code.OBSERVATION_COVERAGE_REQUIRED and not plan.transition.patches
    closed = replace(state, versions=(replace(state.versions[0],valid_to=JUN,is_current=False),))
    plan = plan_repair(c,closed,event(c,OCT,'Delhi'))
    assert plan.transition.decision == Decision.REPAIR_NOT_AUTHORIZED


def test_corrupt_history_and_partial_or_stale_repair_plan_fail_atomically():
    state = history(); c = state.context
    corrupt = replace(state, versions=(replace(state.versions[0],valid_to=OCT),state.versions[1]))
    with pytest.raises(ScdError): plan_repair(c,corrupt,event(c,MAR,'Delhi'))
    plan, result = repair(state,MAR,'Delhi')
    with pytest.raises(ScdError): apply_plan(state,replace(plan.transition,patches=plan.transition.patches[:1]))
    other, other_result = repair(state,APR,'Bangalore')
    with pytest.raises(ScdError): apply_plan(other_result,plan.transition)
    assert state.versions[0].valid_to == JUN


@pytest.mark.parametrize('policy', ['REJECT','INFER_MEMBER'])
@pytest.mark.parametrize('key', ['C999',None])
@pytest.mark.parametrize('scd', [1,2])
def test_inference_policy_keys_and_scd_matrix(policy,key,scd):
    c = context(scd,missing_member_policy=policy)
    f = fact(c,key=key)
    plan = plan_inference(f)
    permitted = policy == 'INFER_MEMBER' and key is not None
    assert plan.decision == (InferenceDecision.CREATED if permitted else InferenceDecision.REJECTED)
    if permitted:
        member = apply_inference(None,plan)
        assert member.status == InferredStatus.INFERRED
        assert member.member_sk == surrogate_key(NS,'DIMENSION_MEMBER',['id'],{'id':key})
        assert not hasattr(member,'valid_from') and not hasattr(member,'version_sk')
        assert apply_inference(member,plan) == member
        assert plan_inference(f,member).decision == InferenceDecision.ALREADY_APPLIED
        ref = resolve_fact(f,inferred=member)
        assert ref.status == ResolutionStatus.INFERRED_REFERENCE and ref.version_sk is None


@pytest.mark.parametrize('scd',[1,2])
def test_inferred_completion_has_no_fake_prehistory_and_supports_rebinding(scd):
    c = context(scd,missing_member_policy='INFER_MEMBER'); f = fact(c,JUN)
    inferred = apply_inference(None,plan_inference(f))
    before = resolve_fact(f,inferred=inferred)
    completion = plan_completion(inferred,event(c,JAN))
    completed, real = apply_completion(inferred,None,completion)
    assert completed.status == InferredStatus.RESOLVED and completed.member_sk == real.member_sk == inferred.member_sk
    assert apply_completion(completed,real,completion) == (completed,real)
    assert plan_completion(completed,event(c,JAN),real).decision == InferenceDecision.ALREADY_APPLIED
    resolved = resolve_fact(f,real,completed)
    assert resolved.status == (ResolutionStatus.RESOLVED if scd == 2 else ResolutionStatus.CURRENT_MEMBER_ONLY)
    if scd == 2:
        assert real.versions[0].valid_from == JAN
        assert resolve_fact(fact(c,JAN-timedelta(days=1)),real,completed).status == ResolutionStatus.NO_VERSION_AT_EVENT_TIME
    plan = plan_reresolution(c,None,real,(before,),before_inferred=inferred,after_inferred=completed)
    assert plan.entries[0].decision == RebindDecision.REBIND
    assert apply_reresolution((before,),plan)[0] == resolved
    assert apply_reresolution((resolved,),plan)[0] == resolved


@pytest.mark.parametrize('acceptance',[Acceptance.REJECTED,Acceptance.QUARANTINED])
def test_rejected_fact_cannot_create_placeholder_or_resolve(acceptance):
    c = context(missing_member_policy='INFER_MEMBER'); f = fact(c,acceptance=acceptance)
    assert plan_inference(f).decision == InferenceDecision.SOURCE_REJECTED
    assert resolve_fact(f,history(c)).status == ResolutionStatus.SOURCE_REJECTED


@pytest.mark.parametrize('acceptance',[Acceptance.REJECTED,Acceptance.QUARANTINED])
@pytest.mark.parametrize('mode',list(Mode))
def test_rejected_real_event_cannot_complete_inferred_member(acceptance,mode):
    c = context(missing_member_policy='INFER_MEMBER')
    member = apply_inference(None,plan_inference(fact(c)))
    plan = plan_completion(member,event(c,acceptance=acceptance),authority=Authority(mode,state_digest(None)))
    assert plan.decision == InferenceDecision.SOURCE_REJECTED
    assert apply_completion(member,None,plan) == (member,None)


@pytest.mark.parametrize('at,expected_start',[
    (JAN-timedelta(microseconds=1),None),(JAN,JAN),(JUN-timedelta(microseconds=1),JAN),(JUN,JUN),(OCT,JUN),
])
def test_asof_exact_half_open_late_fact_matrix(at,expected_start):
    state = history(); result = resolve_fact(fact(state.context,at),state)
    if expected_start is None:
        assert result.status == ResolutionStatus.NO_VERSION_AT_EVENT_TIME and result.version_sk is None
    else:
        assert result.status == ResolutionStatus.RESOLVED
        assert result.version_sk == next(v.version_sk for v in state.versions if v.valid_from == expected_start)


def test_known_gap_never_infers_or_uses_nearest_or_current_version():
    c = context(missing_member_policy='INFER_MEMBER'); state = history(c)
    state = replace(state,versions=(replace(state.versions[0],valid_to=MAR),state.versions[1]))
    for at in [APR,JAN-timedelta(days=1)]:
        result = resolve_fact(fact(c,at),state)
        assert result.status == ResolutionStatus.NO_VERSION_AT_EVENT_TIME and result.version_sk is None
        assert plan_inference(fact(c,at),authoritative=state).decision == InferenceDecision.REJECTED


def test_overlapping_history_cannot_fanout_and_missing_member_never_succeeds():
    state = history(); c = state.context
    bad = replace(state,versions=(replace(state.versions[0],valid_to=OCT),state.versions[1]))
    result = resolve_fact(fact(c,JUN),bad)
    assert result.status == ResolutionStatus.AMBIGUOUS_HISTORY and result.version_sk is None
    assert resolve_fact(fact(c)).status == ResolutionStatus.MEMBER_NOT_FOUND
    assert resolve_fact(fact(c,key=None)).status == ResolutionStatus.INVALID_BUSINESS_KEY


def test_invalid_key_failure_digest_and_explicit_missing_key():
    c = context()
    for keys in [(('id',None),),(('id',1.2),),(('id',[]),),()]:
        f = replace(fact(c),business_keys=keys)
        result = resolve_fact(f)
        assert result.status == ResolutionStatus.INVALID_BUSINESS_KEY
        assert len(resolution_digest(result)) == 64


def test_re_resolution_rebinds_only_affected_facts_and_correction_retains_keys():
    before = history(); c = before.context
    repair_plan, after = repair(before,MAR,'Delhi')
    refs = tuple(resolve_fact(fact(c,at,identity=i+1),before) for i,at in enumerate([JAN,APR,JUN,OCT]))
    plan = plan_reresolution(c,before,after,refs)
    assert plan.affected_ranges == repair_plan.affected_ranges == (TimeRange(MAR,JUN),)
    by_time = {e.before.fact.event_time:e for e in plan.entries}
    assert by_time[APR].decision == RebindDecision.REBIND
    for at in [JAN,JUN,OCT]: assert by_time[at].before == by_time[at].after
    assert by_time[APR].after.version_sk == after.versions[1].version_sk
    assert apply_reresolution(refs,plan) == apply_reresolution(apply_reresolution(refs,plan),plan)
    correction, corrected = repair(before,JAN,'Changed',Authority(Mode.CORRECTION,state_digest(before)))
    plan = plan_reresolution(c,before,corrected,refs)
    assert all(e.decision == RebindDecision.UNCHANGED for e in plan.entries)
    assert all(e.before.version_sk == e.after.version_sk for e in plan.entries)


def test_reresolution_determinism_preconditions_tampering_and_no_partial_apply():
    before = history(); c = before.context; _,after = repair(before,MAR,'Delhi')
    refs = tuple(resolve_fact(fact(c,at,identity=i+1),before) for i,at in enumerate([JAN,APR]))
    plan = plan_reresolution(c,before,after,refs)
    assert plan == plan_reresolution(c,before,after,tuple(reversed(refs)))
    assert reresolution_digest(plan) == reresolution_digest(plan_reresolution(c,before,after,refs))
    bad = replace(refs[0],version_sk='0'*64)
    with pytest.raises(ScdError): plan_reresolution(c,before,after,(bad,refs[1]))
    with pytest.raises(ScdError): apply_reresolution((bad,refs[1]),plan)
    with pytest.raises(ScdError): apply_reresolution(refs,replace(plan,affected_ranges=()))
    with pytest.raises(ScdError): plan_reresolution(c,before,after,(refs[0],refs[0]))


def test_context_source_namespace_and_model_versions_cannot_be_converted():
    state = history(); c = state.context
    for other in [replace(c,model_id=2),replace(c,model_version_id=2),replace(c,namespace=UUID(int=2)),
                  replace(c,source_contract=SourceContractPin(1,1,2,1))]:
        with pytest.raises(ScdError): resolve_fact(fact(other),state)
        with pytest.raises(ScdError): plan_repair(other,state,event(other,MAR,'Delhi'))
    with pytest.raises(ScdError): resolve_fact(fact(c,key='C999'),state)


def test_date_domain_and_naive_time_rejection():
    c = context(date_domain=True)
    a = date(2026,1,1); b = date(2026,6,1); mid = date(2026,3,1)
    state = apply_plan(None,plan_scd2(c,None,event(c,a))).state
    state = apply_plan(state,plan_scd2(c,state,event(c,b,'Mumbai'))).state
    plan = plan_repair(c,state,event(c,mid,'Delhi'))
    result = apply_plan(state,plan.transition).state
    assert resolve_fact(fact(c,mid),result).version_sk == result.versions[1].version_sk
    for invalid in [JAN,datetime(2026,3,1),'2026-03-01']:
        with pytest.raises(ScdError): resolve_fact(fact(c,invalid),result)


def test_digests_fresh_process_and_existing_golden_identity():
    state = history(); plan,result = repair(state,MAR,'Delhi')
    assert result.member_sk == 'b60ec522cb560ecc822384d9754a2ec9c6d283e7dda51abc98ad60f242dfc511'
    assert result.versions[-1].version_sk == 'ab51609380916238099b5418f8ce8aaac3845f04728de20983a924aa3a0ef86d'
    f = fact(state.context,APR); resolution = resolve_fact(f,result)
    code = 'from tests.test_warehouse_late_data import *; p,s=repair(history(),MAR,"Delhi"); print(repair_digest(p)); print(resolution_digest(resolve_fact(fact(s.context,APR),s)))'
    output = subprocess.check_output([sys.executable,'-c',code],text=True).splitlines()
    assert output == [repair_digest(plan),resolution_digest(resolution)]
    assert repair_digest(plan) == '7a83e19e13ae7c8aa14ffbaf1eb6754192db4d15344f89cb2bc9ed2e0120d26e'
    assert state_digest(result) == 'aabf96cd8d8b06b0ad6682edf52d951bdbcba98a21543d52336652b4a4ed1e76'
    assert resolution_digest(resolution) == '4302717e3e85027f96c1f245c7e3e136725a5486f53c56b90da43df09f44f2fe'
    assert inferred_digest(plan_inference(fact(context(missing_member_policy='INFER_MEMBER'),key='C999')).result) == '3decb5b423f50510022ea7ccac425513d469f35dbba72446cbbd49ab5a6c6c29'


def test_normal_forward_events_delegate_to_11c_without_losing_observation_policy():
    state = history(); c = state.context
    for city,label,decision in [('Mumbai','One',Decision.UNCHANGED),('Mumbai','Two',Decision.UPDATE_NONHISTORICAL),
                                ('Delhi','One',Decision.CLOSE_AND_INSERT)]:
        e = event(c,OCT,city,label)
        plan = plan_repair(c,state,e)
        assert plan.transition == plan_scd2(c,state,e)
        assert plan.transition.decision == decision
    assert not plan_repair(c,state,event(c,OCT,'Mumbai')).affected_ranges


def test_inferred_reference_is_time_independent_but_fact_time_is_retained():
    c = context(missing_member_policy='INFER_MEMBER')
    one = plan_inference(fact(c,JAN))
    two = plan_inference(fact(c,OCT,identity=2))
    assert one.result == two.result and inferred_digest(one.result) == inferred_digest(two.result)
    assert resolve_fact(fact(c,JAN),inferred=one.result).fact.event_time == JAN
    assert resolve_fact(fact(c,OCT),inferred=one.result).fact.event_time == OCT


def test_completed_member_does_not_authorize_pre_effective_fact_history():
    c = context(missing_member_policy='INFER_MEMBER')
    early_fact = fact(c,JAN)
    placeholder = apply_inference(None,plan_inference(early_fact))
    completion = plan_completion(placeholder,event(c,JUN,'Mumbai'))
    completed,state = apply_completion(placeholder,None,completion)
    assert resolve_fact(early_fact,state,completed).status == ResolutionStatus.NO_VERSION_AT_EVENT_TIME
    assert resolve_fact(fact(c,JUN),state,completed).status == ResolutionStatus.RESOLVED
    assert state.versions[0].valid_from == JUN
    with pytest.raises(ScdError): apply_completion(completed,state,replace(completion,result=placeholder))
    with pytest.raises(ScdError): apply_inference(placeholder,replace(plan_inference(early_fact),result=completed))
    with pytest.raises(ScdError): apply_inference(placeholder,replace(plan_inference(early_fact),result=None))


def test_reference_rebinding_rejects_partial_mixture_and_forged_after_result():
    before = history(); c = before.context; _,after = repair(before,MAR,'Delhi')
    refs = tuple(resolve_fact(fact(c,at,identity=i+1),before) for i,at in enumerate([APR,MAY]))
    plan = plan_reresolution(c,before,after,refs)
    final = apply_reresolution(refs,plan)
    with pytest.raises(ScdError): apply_reresolution((refs[0],final[1]),plan)
    entry = plan.entries[0]
    bad = replace(plan,entries=(replace(entry,after=replace(entry.after,version_sk='0'*64)),plan.entries[1]))
    with pytest.raises(ScdError): apply_reresolution(refs,bad)
    assert refs[0].version_sk == before.versions[0].version_sk


def test_relationships_are_independently_composable_without_duplicate_fact_loading():
    state = history(); c = state.context
    one = fact(c); two = replace(one,relationship_id=UUID(int=11))
    a,b = resolve_fact(one,state),resolve_fact(two,state)
    assert a.version_sk == b.version_sk and resolution_digest(a) != resolution_digest(b)
    _,after = repair(state,MAR,'Delhi')
    plan = plan_reresolution(c,state,after,(a,b))
    assert len(plan.entries) == 2 and len({e.before.fact.fact_sk for e in plan.entries}) == 1


def test_repair_and_fact_input_are_not_mutated_and_naive_timestamp_is_not_interpreted():
    state = history(); c = state.context
    values = dict(event(c,MAR,'Delhi').values)
    e = type(event(c)).from_mapping(c,values,acceptance=Acceptance.ACCEPTED)
    plan = plan_repair(c,state,e)
    values['city'] = 'Altered'
    assert dict(plan.transition.result.versions[1].tracked)['city'] == 'Delhi'
    with pytest.raises(ScdError): plan_repair(c,state,event(c,datetime(2026,3,1),'Delhi'))
    with pytest.raises(ScdError): resolve_fact(fact(c,datetime(2026,3,1)),state)


def test_empty_reresolution_set_still_validates_all_context_and_member_pins():
    c = context(missing_member_policy='INFER_MEMBER'); state = history(c)
    foreign = context(missing_member_policy='INFER_MEMBER')
    foreign = replace(foreign,model_version_id=2)
    placeholder = plan_inference(fact(foreign)).result
    with pytest.raises(ScdError): plan_reresolution(c,state,state,(),before_inferred=placeholder)
    wrong_member = plan_inference(fact(c,key='C999')).result
    with pytest.raises(ScdError): plan_reresolution(c,None,state,(),before_inferred=wrong_member)


def test_snapshot_absence_policy_remains_inert_during_late_repair():
    c = context(snapshot_absence_policy='DEACTIVATE_ON_COMPLETE')
    state = history(c); _,repaired = repair(state,MAR,'Delhi')
    assert repaired.versions[-1] == state.versions[-1] and repaired.versions[-1].is_current


def test_repair_after_inference_and_before_first_backfill_does_not_duplicate_member():
    c = context(missing_member_policy='INFER_MEMBER')
    placeholder = apply_inference(None,plan_inference(fact(c)))
    completed,state = apply_completion(placeholder,None,plan_completion(placeholder,event(c,JUN,'Mumbai')))
    plan = plan_repair(c,state,event(c,MAR,'Delhi'),Authority(Mode.BACKFILL))
    result = apply_plan(state,plan.transition).state
    assert len(result.versions) == 2 and {v.member_sk for v in result.versions} == {completed.member_sk}
    assert resolve_fact(fact(c,APR),result,completed).version_sk == result.versions[0].version_sk
