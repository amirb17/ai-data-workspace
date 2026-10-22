"""Bounded accepted evidence to literal 11C/11D dimension results; no SQL rules."""
from dataclasses import dataclass
from uuid import UUID

from app.warehouse.accepted_changes import digest, verify_manifest
from app.warehouse.keys import surrogate_key
from app.warehouse.scd1 import plan_scd1
from app.warehouse.scd_repair import plan_repair
from app.warehouse.scd_reference import apply_plan
from app.warehouse.scd_hashing import state_digest
from app.warehouse.scd_invariants import validate_timeline
from app.warehouse.scd_types import (Acceptance, AcceptedOrder, Authority, DimensionEvent,
                                     Mode, ModelContext, MemberState, TransitionPlan)
from app.warehouse.reliability import Result
from app.warehouse.providers.base import ErrorCode, ProviderError
from app.warehouse.providers.types import scalar


@dataclass(frozen=True)
class LogicalPlan:
    context: ModelContext
    manifests: tuple[dict, ...]
    before: tuple[MemberState, ...]
    after: tuple[MemberState, ...]
    transitions: tuple[TransitionPlan, ...]

    @property
    def result(self):
        events = [e for m in self.manifests for e in m['events']]
        counts = {name.lower(): sum(e['outcome'] == name for e in events)
                  for name in ('INSERTED', 'UPDATED', 'UNCHANGED', 'REACTIVATED', 'DEACTIVATED')}
        return Result(manifests=len(self.manifests), events=len(events), rejected=0,
                      checksum=result_checksum(self.after), **counts)

    @property
    def checksum(self):
        return digest(['datarise-bigquery-plan', 1, self.result.model_dump(mode='json'),
                       [state_digest(s) for s in self.before],
                       [[m['manifest_id'], m['checksum']] for m in self.manifests],
                       [[p.member_sk, p.expected_state_digest,
                         [x.action.value for x in p.patches]] for p in self.transitions]])


def result_checksum(states):
    return digest(['datarise-bigquery-result', 1,
                   [[s.member_sk, state_digest(s)] for s in sorted(states, key=lambda s: s.member_sk)]])


def compile_dimension(context, manifests, before=()):
    if context.configuration.model_type != 'DIMENSION':
        raise ValueError('FACT measure/reference roles are not modeled in V1; physical FACT execution is unavailable')
    if not 1 <= len(manifests) <= 1000:
        raise ValueError('Bounded accepted manifests required')
    manifests = tuple(sorted((verify_manifest(m) for m in manifests), key=lambda m: m['position']))
    if len({m['manifest_id'] for m in manifests}) != len(manifests):
        raise ValueError('Duplicate manifest')
    if sum(m['event_count'] for m in manifests) > 1000:
        raise ValueError('Bounded accepted events required')
    states = {}
    for state in before:
        validate_timeline(state)
        if state.context != context or state.member_sk in states:
            raise ProviderError(ErrorCode.TARGET_INCONSISTENT)
        states[state.member_sk] = state
    transitions, touched = [], set()
    for m in manifests:
        pin = m['pin']
        if (m['configuration_sha256'] != digest(context.configuration.model_dump(mode='json'))
                or m['model_version_id'] != context.model_version_id
                or (pin['dataset_id'], pin['dataset_version_id'], pin['policy_id'], pin['rule_version']) != (
                    context.source_contract.dataset_id, context.source_contract.dataset_version_id,
                    context.source_contract.policy_id, context.source_contract.rule_version)):
            raise ProviderError(ErrorCode.SCHEMA_MISMATCH)
        seen = set()
        for e in m['events']:
            if e['outcome'] == 'DEACTIVATED':
                # V1 dimension contract has no physical active flag. Never invent
                # one or turn source absence into an authoritative SCD version.
                raise ValueError('Dimension deactivation requires an approved active-state model extension')
            values = {n: scalar(v) for n, v in e['payload']}
            key = surrogate_key(context.namespace, 'DIMENSION_MEMBER', context.configuration.business_keys, values)
            if key in seen:
                raise ValueError('Multiple same-member events lack an intra-manifest accepted order')
            seen.add(key)
            touched.add(key)
            previous = states.get(key)
            mode = {'CORRECTION': Mode.CORRECTION, 'BACKFILL': Mode.BACKFILL}.get(pin['delivery_kind'], Mode.NORMAL)
            authority = Authority(mode, state_digest(previous) if mode == Mode.CORRECTION else None)
            ev = DimensionEvent.from_mapping(context, values, acceptance=Acceptance.ACCEPTED,
                event_id=UUID(e['event_id'][:32]), order=AcceptedOrder(context.namespace, m['position']))
            plan = (plan_scd1(context, previous, ev, authority)
                    if context.configuration.scd_type == 'SCD_TYPE_1'
                    else plan_repair(context, previous, ev, authority).transition)
            if plan.code.value != 'OK':
                raise ValueError('Accepted evidence is not eligible for this dimension transition: ' + plan.code.value)
            after = apply_plan(previous, plan).state
            if after is None:
                raise ProviderError(ErrorCode.TARGET_INCONSISTENT)
            states[key] = after
            transitions.append(plan)
    if set(states) - touched:
        raise ValueError('Prior state must be bounded to affected members')
    return LogicalPlan(context, manifests, tuple(sorted(before, key=lambda s: s.member_sk)),
                       tuple(states[k] for k in sorted(states)), tuple(transitions))
