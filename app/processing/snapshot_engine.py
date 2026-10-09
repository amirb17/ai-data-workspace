"""Keyed SNAPSHOT adapter over UPSERT identity/comparison, without hard deletion."""
from collections import Counter
from datetime import datetime, timezone
import pandas as pd
from app.processing.append_engine import LINEAGE
from app.processing.upsert_engine import (
    upsert_rows, UPSERT_LINEAGE, ORIGIN, OUTCOME_COUNT, state_index, event_value,
)

ACTIVITY = ['_datarise_active', '_datarise_effective_at',
            '_datarise_deactivated_at', '_datarise_deactivated_upload_id',
            '_datarise_deactivated_application_id', '_datarise_deactivated_policy_id',
            '_datarise_deactivated_rule_version', '_datarise_reactivated_at',
            '_datarise_reactivated_upload_id', '_datarise_reactivated_application_id',
            '_datarise_reactivated_policy_id', '_datarise_reactivated_rule_version']
SNAPSHOT_LINEAGE = UPSERT_LINEAGE + ACTIVITY
SNAPSHOT_OUTCOMES = OUTCOME_COUNT | {'REACTIVATED':'reactivated_rows'}


def timestamp(value):
    if value is None or pd.isna(value):
        return None
    result = datetime.fromisoformat(str(value).replace('Z', '+00:00')) if not isinstance(value, datetime) else value
    if result.utcoffset() is None:
        raise ValueError('Explicit snapshot timezone required')
    return result.astimezone(timezone.utc)


def snapshot_rows(current, incoming, policy, application):
    if policy['load_strategy'] != 'SNAPSHOT' or policy['normalization_version'] != 1:
        raise ValueError('Version 1 SNAPSHOT required')
    coverage = application.get('snapshot_coverage')
    effective = timestamp(application.get('snapshot_effective_at'))
    boundary = timestamp(application.get('previous_snapshot_boundary_at'))
    if coverage not in ('COMPLETE','PARTIAL') or (coverage == 'COMPLETE' and policy['snapshot_coverage'] != 'COMPLETE'):
        raise ValueError('Explicit authorized coverage required')
    if (policy['event_time_column'] or boundary) and effective is None:
        raise ValueError('Business-time snapshot requires explicit effective time')
    if application.get('delivery_kind') not in ('NORMAL','CORRECTION','BACKFILL'):
        raise ValueError('Explicit delivery kind required')
    columns = [c['name'] for c in policy['schema_columns']]
    previous = state_index(current, policy)
    if len(current) and any((timestamp(r[1]['_datarise_effective_at']) is None) != (effective is None) for r in previous.values()):
        raise ValueError('Changing snapshot ordering requires explicit migration')
    stale = effective is not None and boundary is not None and effective < boundary
    equal = effective is not None and effective == boundary
    correction = equal and application['delivery_kind'] == 'CORRECTION'
    compare_policy = policy | {'load_strategy':'UPSERT'}
    candidate, _, ledger = upsert_rows(current[columns+UPSERT_LINEAGE], incoming, compare_policy, application,
                                       allow_equal_correction=correction)
    result = state_index(candidate, policy)
    rows = {key: row[1].copy() | {c: previous[key][1][c] if key in previous else None for c in ACTIVITY} for key,row in result.items()}
    changed_at = application['started_at'].isoformat()
    effective_text = effective.isoformat() if effective else None
    for item in ledger:
        kind, key = item['outcome'], item['key_hash']
        if kind in ('DUPLICATE','REJECTED'):
            continue
        old = previous.get(key)
        if effective and policy['event_time_column']:
            event = event_value(incoming.iloc[item['silver_row']][policy['event_time_column']])
            limit = effective.date() if event[0]=='date' else effective
            if event[1] > limit:
                item['outcome'], item['reason'] = 'REJECTED', 'INVALID_IDENTITY_OR_EVENT_TIME'
                if old: rows[key] = old[1].copy()
                else: rows.pop(key, None)
                continue
        # Snapshot boundary protects even previously absent keys from old deliveries.
        if stale:
            item['outcome'], item['reason'] = 'STALE', 'OLDER_SNAPSHOT'
        elif equal and not correction and (kind in ('INSERTED','UPDATED') or (old and not old[1]['_datarise_active'])):
            item['outcome'], item['reason'] = 'REJECTED', 'EQUAL_SNAPSHOT_TIME_CONFLICT'
        elif old and effective and timestamp(old[1]['_datarise_effective_at']) > effective:
            item['outcome'], item['reason'] = 'STALE', 'OLDER_RECORD_SNAPSHOT'
        if item['outcome'] in ('STALE','REJECTED'):
            if old: rows[key] = old[1].copy()
            else: rows.pop(key, None)
            continue
        row = rows[key]
        row.update({c: old[1][c] if old else None for c in ACTIVITY})
        row['_datarise_active'] = True
        if old and not old[1]['_datarise_active']:
            item['outcome'] = 'REACTIVATED'
            row.update(dict(zip(LINEAGE, [application['upload_request_id'], application['application_id'], application['policy_id'], application['applied_rule_version']])))
            row['_datarise_reactivated_at'] = changed_at
            for suffix, field in [('upload_id','upload_request_id'),('application_id','application_id'),('policy_id','policy_id'),('rule_version','applied_rule_version')]:
                row['_datarise_reactivated_'+suffix] = application[field]
        if item['outcome'] in ('INSERTED','UPDATED','REACTIVATED') or (old and effective and effective > timestamp(old[1]['_datarise_effective_at'])):
            row['_datarise_effective_at'] = effective_text

    withheld = (application['rejected_rows'] or any(i['outcome']=='REJECTED' for i in ledger))
    # A correction is targeted: never deactivate unrelated records in the same period.
    deactivate = coverage == 'COMPLETE' and not stale and not equal and not withheld and application['delivery_kind']=='NORMAL'
    deactivations = []
    present = {i['key_hash'] for i in ledger if i['key_hash'] is not None}
    for key,old in previous.items():
        if key not in rows: rows[key] = old[1].copy()
        if deactivate and key not in present and old[1]['_datarise_active']:
            row = rows[key]
            row['_datarise_active'] = False
            row['_datarise_effective_at'] = effective_text
            row['_datarise_deactivated_at'] = changed_at
            for suffix, field in [('upload_id','upload_request_id'),('application_id','application_id'),('policy_id','policy_id'),('rule_version','applied_rule_version')]:
                row['_datarise_deactivated_'+suffix] = application[field]
            deactivations.append(key)
    candidate = pd.DataFrame(list(rows.values()), columns=columns+SNAPSHOT_LINEAGE)
    for c in columns: candidate[c] = candidate[c].astype(incoming[c].dtype)
    candidate['_datarise_active'] = candidate['_datarise_active'].astype(bool)
    # Nullable integer lineage must survive Parquet without float precision loss.
    for c in ACTIVITY:
        if c.endswith(('_id','_version')): candidate[c] = pd.array(candidate[c], dtype='Int64')
    tally = Counter(i['outcome'] for i in ledger)
    counts = {field:tally[kind] for kind,field in SNAPSHOT_OUTCOMES.items()}
    counts['conflict_rows'] = sum(i['outcome']=='REJECTED' and i['reason']!='INVALID_IDENTITY_OR_EVENT_TIME' for i in ledger)
    counts.update(deactivated_rows=len(deactivations), active_rows=int(candidate['_datarise_active'].sum()),
                  inactive_rows=int((~candidate['_datarise_active']).sum()))
    outcome = 'STALE' if stale else 'EQUAL_TIME_CONFLICT' if equal and not correction and counts['conflict_rows'] else 'DEACTIVATION_WITHHELD' if coverage=='COMPLETE' and (withheld or application['delivery_kind']!='NORMAL') else 'APPLIED'
    next_boundary = max(t for t in (boundary,effective) if t is not None) if boundary or effective else None
    application['snapshot_boundary_at'] = next_boundary
    application['snapshot_outcome'] = outcome
    validate_snapshot_candidate(candidate, policy, application, counts, ledger)
    validate_snapshot_transition(current, candidate, policy, application, counts, ledger)
    return candidate, counts, ledger


def validate_snapshot_candidate(frame, policy, application, counts, ledger):
    index = state_index(frame, policy)
    if frame[UPSERT_LINEAGE].isna().any().any() or frame['_datarise_active'].isna().any() or any(type(v) is not bool for v in frame['_datarise_active'].tolist()):
        raise ValueError('Snapshot lifecycle/lineage incomplete')
    if sorted(i['silver_row'] for i in ledger) != list(range(application['valid_rows'])):
        raise ValueError('Snapshot ledger coverage differs')
    tally = Counter(i['outcome'] for i in ledger)
    if any(counts[field] != tally[kind] for kind,field in SNAPSHOT_OUTCOMES.items()):
        raise ValueError('Snapshot outcome accounting differs')
    if counts['conflict_rows'] != sum(i['outcome']=='REJECTED' and i['reason']!='INVALID_IDENTITY_OR_EVENT_TIME' for i in ledger):
        raise ValueError('Snapshot conflict accounting differs')
    if counts['active_rows'] != int(frame['_datarise_active'].sum()) or counts['inactive_rows'] != len(frame)-counts['active_rows']:
        raise ValueError('Snapshot active/inactive accounting differs')
    for item in ledger:
        if any(item[f]!=application[a] for f,a in [('application_id','application_id'),('upload_request_id','upload_request_id'),('policy_id','policy_id'),('rule_version','applied_rule_version')]):
            raise ValueError('Snapshot ledger ownership differs')
        if item['outcome'] in ('INSERTED','UPDATED','UNCHANGED','REACTIVATED'):
            row = index.get(item['key_hash'])
            if not row or row[2]!=item['incoming_content_hash'] or not row[1]['_datarise_active']:
                raise ValueError('Snapshot candidate/ledger differs')
    for _,row,_,_,_ in index.values():
        timestamp(row['_datarise_effective_at'])
        for action in ('deactivated','reactivated'):
            fields=[f'_datarise_{action}_{suffix}' for suffix in ('at','upload_id','application_id','policy_id','rule_version')]
            missing=[pd.isna(row[c]) for c in fields]
            if any(missing) and not all(missing): raise ValueError('Snapshot lifecycle lineage incomplete')
        if not row['_datarise_active'] and pd.isna(row['_datarise_deactivated_application_id']):
            raise ValueError('Inactive record requires deactivation lineage')
    return index


def validate_snapshot_transition(current, candidate, policy, application, counts, ledger):
    previous, result = state_index(current,policy), state_index(candidate,policy)
    inserted={i['key_hash'] for i in ledger if i['outcome']=='INSERTED'}
    changed={i['key_hash'] for i in ledger if i['outcome'] in ('UPDATED','REACTIVATED')}
    present={i['key_hash'] for i in ledger}
    if set(result)!=set(previous)|inserted: raise ValueError('Snapshot must preserve all historical keys')
    for key in inserted|changed:
        row=result[key][1]
        if any(row[c]!=application[field] for c,field in zip(LINEAGE,['upload_request_id','application_id','policy_id','applied_rule_version'])):
            raise ValueError('Snapshot changed-record lineage differs')
        if key in inserted and (row['_datarise_inserted_at']!=application['started_at'].isoformat() or any(row[c]!=application[field] for c,field in zip(ORIGIN[:4],['upload_request_id','application_id','policy_id','applied_rule_version']))):
            raise ValueError('Snapshot insertion lineage differs')
    deactivated, reactivated = [], []
    for key,old in previous.items():
        new=result[key]
        if any(new[1][c]!=old[1][c] for c in ORIGIN[:-1]): raise ValueError('Snapshot original lineage changed')
        if key not in changed and new[2:]!=old[2:]: raise ValueError('Snapshot changed a retained business record')
        if key not in changed and any(new[1][c]!=old[1][c] for c in UPSERT_LINEAGE):
            raise ValueError('Snapshot changed retained record update lineage')
        if old[1]['_datarise_active'] and not new[1]['_datarise_active']:
            if application['snapshot_coverage']!='COMPLETE' or key in present or application['snapshot_outcome']!='APPLIED':
                raise ValueError('Unauthorized missing-record deactivation')
            deactivated.append(key)
        if not old[1]['_datarise_active'] and new[1]['_datarise_active']: reactivated.append(key)
        if timestamp(new[1]['_datarise_effective_at']) and timestamp(old[1]['_datarise_effective_at']) and timestamp(new[1]['_datarise_effective_at'])<timestamp(old[1]['_datarise_effective_at']):
            raise ValueError('Snapshot rewound record effective time')
    if len(deactivated)!=counts['deactivated_rows'] or len(reactivated)!=counts['reactivated_rows']:
        raise ValueError('Snapshot lifecycle accounting differs')
    for action,keys in [('deactivated',deactivated),('reactivated',reactivated)]:
        for key in keys:
            row=result[key][1]
            if row[f'_datarise_{action}_at']!=application['started_at'].isoformat() or any(row[f'_datarise_{action}_{suffix}']!=application[field] for suffix,field in [('upload_id','upload_request_id'),('application_id','application_id'),('policy_id','policy_id'),('rule_version','applied_rule_version')]):
                raise ValueError('Snapshot lifecycle application lineage differs')
    for key,old in previous.items():
        row=result[key][1]
        for action,allowed in [('deactivated',deactivated),('reactivated',reactivated)]:
            if key in allowed: continue
            for suffix in ('at','upload_id','application_id','policy_id','rule_version'):
                c=f'_datarise_{action}_{suffix}'
                if pd.isna(row[c]) and pd.isna(old[1][c]): continue
                if pd.isna(row[c]) or pd.isna(old[1][c]) or row[c]!=old[1][c]:
                    raise ValueError('Snapshot altered historical lifecycle lineage')
