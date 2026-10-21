"""Forward SCD2 and pinned current-boundary corrections; late repair deferred."""
from dataclasses import replace

from app.warehouse.scd_common import (blocked, finish, make_version, prepare, source_or_retry)
from app.warehouse.scd_hashing import attribute_hash, changed
from app.warehouse.scd_types import Action, Authority, Code, Decision, Mode, Patch


def plan_scd2(context, current, event, authority=Authority()):
    key, instant, tracked, nonhistorical = prepare(context, current, event, authority, 'SCD_TYPE_2')
    early = source_or_retry(context, current, event, key)
    if early:
        return early
    if current is None:
        row = make_version(context, event, key, instant, tracked, nonhistorical)
        return finish(context, current, event, key, instant, (row,),
                      (Patch(Action.INSERT_VERSION, key, row.version_sk, after=row),),
                      Decision.INSERT_INITIAL_VERSION, tracked, nonhistorical)
    old = current.versions[-1]
    dt, dn = changed(old.tracked, tracked), changed(old.nonhistorical, nonhistorical)
    # Only an exact materialized historical boundary/payload proves an old retry.
    if instant < current.observed_time:
        if any(v.valid_from == instant and not changed(v.tracked, tracked)
               and not changed(v.nonhistorical, nonhistorical) for v in current.versions):
            return blocked(context, current, event, key, Decision.ALREADY_APPLIED, Code.OK)
    correction = authority.mode == Mode.CORRECTION and instant == old.valid_from and old.is_current
    if not old.is_current:
        return blocked(context, current, event, key, Decision.HISTORICAL_REPAIR_REQUIRED, Code.HISTORICAL_REPAIR_REQUIRED)
    if (instant == old.valid_from or instant == current.observed_time) and dt and not correction:
        return blocked(context, current, event, key, Decision.CONFLICT, Code.SAME_TIME_CONFLICT)
    if instant < current.observed_time and not correction:
        return blocked(context, current, event, key, Decision.HISTORICAL_REPAIR_REQUIRED, Code.HISTORICAL_REPAIR_REQUIRED)
    if correction and dt:
        row = replace(old, tracked=tracked, nonhistorical=nonhistorical, tracked_hash=attribute_hash(tracked))
        return finish(context, current, event, key, instant, current.versions[:-1] + (row,),
                      (Patch(Action.CORRECT_CURRENT, key, old.version_sk, old, row),),
                      Decision.CORRECT_CURRENT, tracked, nonhistorical)
    if not dt:
        if dn:
            row = replace(old, nonhistorical=nonhistorical)
            return finish(context, current, event, key, instant, current.versions[:-1] + (row,),
                          (Patch(Action.UPDATE_NONHISTORICAL, key, old.version_sk, old, row),),
                          Decision.UPDATE_NONHISTORICAL, tracked, nonhistorical)
        return finish(context, current, event, key, instant, current.versions, (), Decision.UNCHANGED, tracked, nonhistorical)
    closed = replace(old, valid_to=instant, is_current=False)
    row = make_version(context, event, key, instant, tracked, nonhistorical)
    return finish(context, current, event, key, instant, current.versions[:-1] + (closed, row),
                  (Patch(Action.CLOSE_VERSION, key, old.version_sk, old, closed),
                   Patch(Action.INSERT_VERSION, key, row.version_sk, after=row)),
                  Decision.CLOSE_AND_INSERT, tracked, nonhistorical)
