"""Pure APPEND semantics. No storage, DB, inference, updates or duplicate dropping."""
import json
from collections import defaultdict
from datetime import datetime, date
import pandas as pd
from app.processing.row_identity import business_key_hash, row_content_hash, normalize_value
from app.processing.schema_fingerprint import normalize_dtype


LINEAGE = ['_datarise_upload_id','_datarise_application_id','_datarise_policy_id','_datarise_rule_version']


def scalar(value):
    if value is None or pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if hasattr(value,'item') and not isinstance(value,(str,date,datetime)):
        return value.item()
    return value


def effective_schema(df, columns):
    return [{'name':c,'data_type':normalize_dtype(df[c].dtype)} for c in columns]


def canonical(row, columns):
    return json.dumps([[c,normalize_value(scalar(row[c]))] for c in columns],ensure_ascii=False,separators=(',',':'))


def append_rows(current, incoming, policy, application):
    columns = [c['name'] for c in policy['schema_columns']]
    keys = policy['business_keys']
    if policy['load_strategy'] != 'APPEND' or policy['normalization_version'] != 1:
        raise ValueError('Only version 1 APPEND is executable')
    if any(c not in incoming.columns for c in columns):
        raise ValueError('Silver business schema incomplete')
    existing = {}
    for row in current[columns].to_dict('records'):
        if keys:
            typed={c:scalar(row[c]) for c in columns}
            kh=business_key_hash(typed,keys)
            signature=(canonical(typed,keys),canonical(typed,sorted(columns)))
            if kh in existing and existing[kh]!=signature:
                raise ValueError('Trusted state has ambiguous key identity')
            existing[kh]=signature
    groups=defaultdict(list); rejected=[]; accepted=[]; duplicates=0
    for index,row in enumerate(incoming[columns].to_dict('records')):
        try:
            typed={c:scalar(row[c]) for c in columns}
            event=policy['event_time_column']
            if event:
                value=typed[event]
                if not isinstance(value,(datetime,date)):
                    raise ValueError('Event time must be a typed date/time')
                normalize_value(value)  # Naive timestamps rejected; timezone never guessed.
            content=row_content_hash(typed,columns)
            if keys:
                # Reject unsafe large float keys; do not invent lost integer precision.
                if any(isinstance(typed[k],float) and abs(typed[k])>=2**53 for k in keys):
                    raise ValueError('Ambiguous numeric key precision')
                kh=business_key_hash(typed,keys)
                groups[kh].append((index,content,canonical(typed,keys),canonical(typed,sorted(columns))))
            else:
                accepted.append(index)
        except (ValueError,TypeError,OverflowError):
            rejected.append({'silver_row':index,'reason':'INVALID_IDENTITY_OR_EVENT_TIME'})
    for kh,entries in groups.items():
        signatures={(e[1],e[2],e[3]) for e in entries}
        if len(signatures)!=1:
            rejected.extend({'silver_row':e[0],'reason':'CONFLICTING_INCOMING_KEY'} for e in entries)
        elif kh in existing:
            if existing[kh] == (entries[0][2],entries[0][3]):
                duplicates+=len(entries)
            else:
                rejected.extend({'silver_row':e[0],'reason':'EXISTING_KEY_CONTENT_CONFLICT'} for e in entries)
        else:
            accepted.append(entries[0][0]); duplicates+=len(entries)-1
    added=incoming.iloc[sorted(accepted)][columns].copy()
    for column,value in zip(LINEAGE,[application['upload_request_id'],application['application_id'],application['policy_id'],application['applied_rule_version']]):
        added[column]=value
    result=added.reset_index(drop=True) if current.empty else pd.concat([current,added],ignore_index=True)
    if len(incoming)!=len(added)+duplicates+len(rejected):
        raise ValueError('APPEND outcome accounting failed')
    return result, {'inserted_rows':len(added),'duplicate_rows':duplicates,'incremental_rejected_rows':len(rejected)},rejected
