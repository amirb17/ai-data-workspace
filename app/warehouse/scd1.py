"""SCD1 current-value planning; no history/replay execution."""
from dataclasses import replace

from app.warehouse.scd_common import (blocked, finish, make_version, ordering, prepare, source_or_retry)
from app.warehouse.scd_hashing import attribute_hash, changed
from app.warehouse.scd_types import Action, Authority, Code, Decision, Mode, Patch


def plan_scd1(context, current, event, authority=Authority()):
    key, instant, tracked, nonhistorical = prepare(context, current, event, authority, 'SCD_TYPE_1')
    early = source_or_retry(context, current, event, key)
    if early:
        return early
    if current is None:
        row = make_version(context, event, key, instant, tracked, nonhistorical)
        return finish(context, current, event, key, instant, (row,),
                      (Patch(Action.INSERT_MEMBER, key, None, after=row),), Decision.INSERT, tracked, nonhistorical)
    old = current.versions[0]
    differs = bool(changed(old.tracked, tracked) or changed(old.nonhistorical, nonhistorical))
    order = ordering(current, event, instant)
    if order is None and authority.mode != Mode.CORRECTION:
        if differs:
            return blocked(context, current, event, key, Decision.CONFLICT, Code.ORDERING_REQUIRED)
        # No invented ordering/provenance advancement when order is unprovable.
        return blocked(context, current, event, key, Decision.UNCHANGED, Code.OK)
    if authority.mode != Mode.CORRECTION:
        if order < 0:
            decision = Decision.HISTORICAL_REPAIR_REQUIRED if authority.mode == Mode.BACKFILL else Decision.STALE_REJECTED
            code = Code.HISTORICAL_REPAIR_REQUIRED if authority.mode == Mode.BACKFILL else Code.STALE_SCD1_EVENT
            return blocked(context, current, event, key, decision, code)
        if order == 0 and differs:
            return blocked(context, current, event, key, Decision.CONFLICT, Code.SAME_TIME_CONFLICT)
    if not differs:
        return finish(context, current, event, key, instant, current.versions, (), Decision.UNCHANGED, tracked, nonhistorical)
    row = replace(old, tracked=tracked, nonhistorical=nonhistorical, tracked_hash=attribute_hash(tracked))
    return finish(context, current, event, key, instant, (row,),
                  (Patch(Action.UPDATE_MEMBER, key, None, old, row),), Decision.UPDATE, tracked, nonhistorical)
