"""Pure keyed UPSERT; immutable candidates and private hash-only change ledger."""
from collections import defaultdict, Counter
from datetime import date, datetime, timezone
import pandas as pd
from app.processing.append_engine import LINEAGE, scalar, canonical
from app.processing.row_identity import business_key_hash, row_content_hash, normalize_value

ORIGIN = ['_datarise_origin_upload_id', '_datarise_origin_application_id',
          '_datarise_origin_policy_id', '_datarise_origin_rule_version',
          '_datarise_inserted_at', '_datarise_updated_at']
UPSERT_LINEAGE = LINEAGE + ORIGIN
OUTCOME_COUNT = {'INSERTED':'inserted_rows', 'UPDATED':'updated_rows', 'UNCHANGED':'unchanged_rows',
                 'DUPLICATE':'duplicate_rows', 'REJECTED':'incremental_rejected_rows', 'STALE':'stale_rows'}


def identity(row, columns, keys):
    typed = {c: scalar(row[c]) for c in columns}
    if any(isinstance(typed[k], float) and abs(typed[k]) >= 2**53 for k in keys):
        raise ValueError('Ambiguous numeric key precision')
    return typed, business_key_hash(typed, keys), row_content_hash(typed, columns), canonical(typed, keys), canonical(typed, sorted(columns))


def event_value(value):
    value = scalar(value)
    tag = normalize_value(value)
    if not isinstance(value, (date, datetime)):
        raise ValueError('Typed date/time required')
    return tag[0], value.astimezone(timezone.utc) if isinstance(value, datetime) else value


def state_index(frame, policy):
    columns = [c['name'] for c in policy['schema_columns']]
    keys = policy['business_keys']
    if not keys or any(k not in columns for k in keys):
        raise ValueError('An explicit compatible business key is required')
    result = {}
    for position, row in enumerate(frame.to_dict('records')):
        typed, key, content, key_text, content_text = identity(row, columns, keys)
        if key in result:
            raise ValueError('Trusted state contains duplicate/ambiguous business keys')
        if policy['event_time_column']:
            event_value(typed[policy['event_time_column']])
        result[key] = (position, row, content, key_text, content_text)
    return result


def upsert_rows(current, incoming, policy, application):
    if policy['load_strategy'] != 'UPSERT' or policy['normalization_version'] != 1:
        raise ValueError('Only version 1 keyed UPSERT is executable')
    columns = [c['name'] for c in policy['schema_columns']]
    keys = policy['business_keys']
    existing = state_index(current, policy)
    records = current.to_dict('records')
    groups = defaultdict(list)
    ledger = []
    changed_at = application['started_at'].isoformat()
    lineage = dict(zip(LINEAGE, [application['upload_request_id'], application['application_id'],
                               application['policy_id'], application['applied_rule_version']]))

    def outcome(index, kind, key=None, content=None, before=None, reason=None):
        ledger.append({'silver_row':index, 'outcome':kind, 'key_hash':key,
                       'incoming_content_hash':content, 'previous_content_hash':before,
                       'application_id':application['application_id'], 'upload_request_id':application['upload_request_id'],
                       'policy_id':application['policy_id'], 'rule_version':application['applied_rule_version'],
                       'changed_at':changed_at, 'reason':reason})

    for index, row in enumerate(incoming[columns].to_dict('records')):
        try:
            typed, key, content, key_text, content_text = identity(row, columns, keys)
            if policy['event_time_column']:
                event_value(typed[policy['event_time_column']])
            groups[key].append((index, row, typed, content, key_text, content_text))
        except (ValueError, TypeError, OverflowError):
            outcome(index, 'REJECTED', reason='INVALID_IDENTITY_OR_EVENT_TIME')

    for key, entries in groups.items():
        old = existing.get(key)
        before = old[2] if old else None
        if len({(e[3],e[4],e[5]) for e in entries}) != 1 or (old and old[3] != entries[0][4]):
            for e in entries:
                outcome(e[0], 'REJECTED', key, e[3], before, 'CONFLICTING_INCOMING_KEY')
            continue
        index, row, typed, content, _, content_text = entries[0]
        kind = 'INSERTED' if not old else 'UNCHANGED' if old[2] == content and old[4] == content_text else 'UPDATED'
        reason = None
        if old and policy['event_time_column']:
            incoming_time = event_value(typed[policy['event_time_column']])
            current_time = event_value(old[1][policy['event_time_column']])
            if incoming_time[0] != current_time[0]:
                kind, reason = 'REJECTED', 'INCOMPATIBLE_EVENT_TIME'
            elif incoming_time[1] < current_time[1]:
                kind, reason = 'STALE', 'OLDER_EVENT_TIME'
            elif incoming_time[1] == current_time[1] and kind == 'UPDATED':
                kind, reason = 'REJECTED', 'EQUAL_EVENT_TIME_CONFLICT'
        # Identical repeated rows perform one semantic operation, even if it is stale/rejected.
        outcome(index, kind, key, content, before, reason)
        for e in entries[1:]:
            outcome(e[0], 'DUPLICATE', key, content, before)
        if kind in ('INSERTED', 'UPDATED'):
            origin = {k:old[1][k] for k in ORIGIN} if old else dict(zip(ORIGIN[:4], lineage.values())) | {'_datarise_inserted_at':changed_at}
            origin['_datarise_updated_at'] = changed_at
            new_row = row | lineage | origin
            if old:
                records[old[0]] = new_row
            else:
                records.append(new_row)

    candidate = pd.DataFrame(records, columns=columns+UPSERT_LINEAGE)
    # Keep the effective business schema even when every incoming row is excluded.
    for column in columns:
        candidate[column] = candidate[column].astype(incoming[column].dtype)
    tally = Counter(item['outcome'] for item in ledger)
    counts = {field:tally[kind] for kind,field in OUTCOME_COUNT.items()}
    counts['conflict_rows'] = sum(item['outcome']=='REJECTED' and item['reason']!='INVALID_IDENTITY_OR_EVENT_TIME' for item in ledger)
    candidate_index=validate_upsert_candidate(candidate, policy, application, counts, ledger)
    validate_upsert_transition(existing,candidate_index,ledger,application)
    if len(candidate) != len(current)+counts['inserted_rows']:
        raise ValueError('UPSERT state count invariant failed')
    return candidate, counts, ledger


def validate_upsert_candidate(frame, policy, application, counts, ledger):
    index = state_index(frame, policy)
    if frame[UPSERT_LINEAGE].isna().any().any():
        raise ValueError('UPSERT lineage incomplete')
    if sorted(item['silver_row'] for item in ledger) != list(range(application['valid_rows'])):
        raise ValueError('UPSERT ledger must cover each valid input exactly once')
    tally = Counter(item['outcome'] for item in ledger)
    if any(counts[field] != tally[kind] for kind,field in OUTCOME_COUNT.items()):
        raise ValueError('UPSERT outcome accounting differs')
    if counts['conflict_rows'] != sum(i['outcome']=='REJECTED' and i['reason']!='INVALID_IDENTITY_OR_EVENT_TIME' for i in ledger):
        raise ValueError('UPSERT conflict accounting differs')
    for item in ledger:
        if any(item[f] != application[a] for f,a in [('application_id','application_id'),('upload_request_id','upload_request_id'),('policy_id','policy_id'),('rule_version','applied_rule_version')]):
            raise ValueError('UPSERT ledger ownership differs')
        if item['outcome'] in ('INSERTED','UPDATED','UNCHANGED'):
            row = index.get(item['key_hash'])
            if not row or row[2] != item['incoming_content_hash']:
                raise ValueError('UPSERT candidate does not reflect its change ledger')
            if item['outcome'] != 'UNCHANGED' and any(row[1][c] != application[a] for c,a in zip(LINEAGE,['upload_request_id','application_id','policy_id','applied_rule_version'])):
                raise ValueError('UPSERT changed-record lineage differs')
    return index


def validate_upsert_transition(previous,result,ledger,application):
    """Validate old/new representation and original lineage before immutable serialization."""
    inserted={r['key_hash'] for r in ledger if r['outcome']=='INSERTED'}
    updated={r['key_hash'] for r in ledger if r['outcome']=='UPDATED'}
    if set(result)!=set(previous)|inserted:
        raise ValueError('UPSERT must preserve prior keys and add only inserted keys')
    for key,old in previous.items():
        new=result[key]
        if any(new[1][c]!=old[1][c] for c in ORIGIN[:-1]):
            raise ValueError('UPSERT original insertion lineage changed')
        if key not in updated and (new[2:]!=old[2:] or any(new[1][c]!=old[1][c] for c in UPSERT_LINEAGE)):
            raise ValueError('UPSERT changed a retained/unchanged record')
    timestamp=application['started_at'].isoformat()
    for key in inserted|updated:
        row=result[key][1]
        if row['_datarise_updated_at']!=timestamp:
            raise ValueError('UPSERT change timestamp differs')
        if key in inserted and (row['_datarise_inserted_at']!=timestamp or any(row[c]!=application[a] for c,a in zip(ORIGIN[:4],['upload_request_id','application_id','policy_id','applied_rule_version']))):
            raise ValueError('UPSERT insertion lineage differs')
