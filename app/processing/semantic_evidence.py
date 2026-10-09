"""Deterministic evidence only. No domain, entity, key approval or AI inference."""
import ipaddress
import math
import re
import unicodedata
from decimal import Decimal
from datetime import date, datetime
from numbers import Integral
import pandas as pd
from app.processing.schema_fingerprint import normalize_dtype

ALGORITHM_VERSION = 1
TOP_K = 5
CATEGORICAL_LIMIT = 100
MAX_PATTERN_LENGTH = 512
PATTERNS = {
    'email_like': re.compile(r'[^\s@]+@[^\s@]+\.[^\s@]+'),
    'uuid_like': re.compile(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}'),
    'phone_like': re.compile(r'\+?[0-9][0-9 ()-]{6,20}[0-9]'),
    'url_like': re.compile(r'https?://[^\s]+'),
    'numeric_string': re.compile(r'[+-]?\d+(?:\.\d+)?'),
    'code_like': re.compile(r'(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9_-]{2,32}'),
}


def name_features(name):
    value = unicodedata.normalize('NFKC',name)
    value = re.sub(r'([A-Z]+)([A-Z][a-z])',r'\1_\2',value)
    value = re.sub(r'([a-z0-9])([A-Z])',r'\1_\2',value)
    tokens = re.findall(r'[^\W_]+',value.lower(),re.UNICODE)
    return '_'.join(tokens), tokens


def number(value):
    # Reject nonfinite results instead of serializing invalid JSON or invented zeros.
    if value is None: return None
    if isinstance(value,Integral):
        integer=int(value)
        return integer if abs(integer)<=2**53-1 else str(integer)
    if isinstance(value,Decimal): return str(value) if value.is_finite() else None
    try:
        numeric=float(value)
        return numeric if math.isfinite(numeric) else None
    except (OverflowError,TypeError,ValueError): return None


def column_evidence(name, series, policy, required):
    normalized,tokens = name_features(name)
    present=series.dropna(); rows=len(series); nonnull=len(present)
    distinct=int(present.nunique()); unique=distinct/nonnull if nonnull else None
    dtype=normalize_dtype(series.dtype)
    if dtype=='TEXT' and nonnull and present.map(lambda x:isinstance(x,Decimal)).all(): dtype='DECIMAL'
    if dtype=='TEXT' and nonnull and present.map(lambda x:isinstance(x,(date,datetime))).all(): dtype='DATETIME'
    key=name in policy['business_keys']; event=name==policy.get('event_time_column')
    signals={'approved_business_key':key,'name_has_id_token':'id' in tokens or 'uuid' in tokens,
             'non_null_uniqueness_ratio':unique,'null_ratio':(rows-nonnull)/rows if rows else None,
             'repeated_value_count':nonnull-distinct,'canonical_type':dtype}
    candidate=key or bool(nonnull and unique>=.98 and (rows-nonnull)==0 and signals['name_has_id_token'])
    evidence={'original_name':name,'normalized_name':normalized,'tokens':tokens,'canonical_type':dtype,
        'physical_type':str(series.dtype),'nullable':nonnull<rows if rows else None,'nullable_basis':'observed_current_state',
        'approved_required':name in required,'row_count':rows,'null_count':rows-nonnull,'non_null_count':nonnull,
        'null_ratio':signals['null_ratio'],'distinct_count':distinct,'distinct_ratio':unique,'cardinality_method':'EXACT',
        'identifier_candidate':candidate,'identifier_score_components':signals,
        'authoritative_business_key':key,'business_key_position':policy['business_keys'].index(name)+1 if key else None,
        'authoritative_event_time':event,'datetime_candidate':dtype=='DATETIME',
        'numeric_statistics':None,'datetime_statistics':None,'string_statistics':None,'boolean_distribution':None,
        'categorical_statistics':None,'pattern_hints':[],'pattern_scan':None,'sensitivity_hints':[],'min_value':None,'max_value':None}
    sensitive_name=bool(set(tokens)&{'email','phone','ip','ssn','passport','card','account'})
    if sensitive_name:
        evidence['sensitivity_hints'].append({'hint':'sensitive_name_token','confidence':'LOW','basis':'column_name_only'})
    if 'name' in tokens:
        evidence['sensitivity_hints'].append({'hint':'personal_name_possible','confidence':'LOW','basis':'column_name_only'})
    if dtype=='TEXT':
        lengths=present.map(lambda v:len(str(v)))
        evidence['string_statistics']={'min_length':int(lengths.min()) if nonnull else None,
            'max_length':int(lengths.max()) if nonnull else None,'average_length':number(lengths.mean()) if nonnull else None}
        pattern_values=present.loc[lengths<=MAX_PATTERN_LENGTH] if nonnull else present
        evidence['pattern_scan']={'evaluated_count':len(pattern_values),'skipped_long_values':nonnull-len(pattern_values),'max_characters':MAX_PATTERN_LENGTH}
        for hint,pattern in PATTERNS.items():
            count=int(pattern_values.map(lambda v: bool(pattern.fullmatch(str(v))) and (hint!='phone_like' or 7<=len(re.sub(r'\D','',str(v)))<=15)).astype(bool).sum())
            if count:
                evidence['pattern_hints'].append({'pattern':hint,'count':count,'ratio':count/nonnull})
                if hint in ('email_like','phone_like'):
                    evidence['sensitivity_hints'].append({'hint':hint,'confidence':'PATTERN_ONLY','basis':'structural_match'})
        def ip_like(v):
            try: ipaddress.ip_address(str(v)); return True
            except ValueError: return False
        ip_count=int(pattern_values.map(ip_like).astype(bool).sum())
        if ip_count:
            evidence['pattern_hints'].append({'pattern':'ip_like','count':ip_count,'ratio':ip_count/nonnull})
            evidence['sensitivity_hints'].append({'hint':'ip_like','confidence':'PATTERN_ONLY','basis':'structural_match'})
        if nonnull and lengths.nunique()==1:
            evidence['pattern_hints'].append({'pattern':'fixed_length','count':nonnull,'ratio':1})
        if distinct<=CATEGORICAL_LIMIT:
            # Public-safe by construction: no raw values, hashes, or string extrema.
            counts=present.value_counts(sort=False).tolist()
            evidence['categorical_statistics']={'top_k_limit':TOP_K,'values_redacted':True,
                'top_values':[{'rank':i+1,'value':None,'count':int(n),'percentage':100*n/nonnull} for i,n in enumerate(sorted(counts,reverse=True)[:TOP_K])]}
    elif dtype in ('INTEGER','DECIMAL') and nonnull:
        numeric=present.astype('float64')
        finite=math.isfinite(float(numeric.min())) and math.isfinite(float(numeric.max()))
        stats={'min':number(present.min()),'max':number(present.max()),'mean':number(numeric.mean()) if finite else None,
            'median':number(numeric.median()) if finite else None,'standard_deviation':number(numeric.std(ddof=0)) if finite else None,
            'computation_precision':'FLOAT64_AGGREGATES',
            'standard_deviation_basis':'POPULATION','zero_count':int((present==0).sum()),
            'negative_count':int((present<0).sum())}
        stats.update(zero_ratio=stats['zero_count']/nonnull,negative_ratio=stats['negative_count']/nonnull)
        if not evidence['sensitivity_hints']:
            evidence['numeric_statistics']=stats
            evidence['min_value']=stats['min']; evidence['max_value']=stats['max']
    elif dtype=='DATETIME' and not evidence['sensitivity_hints']:
        tz=str(series.dt.tz) if pd.api.types.is_datetime64_any_dtype(series) and series.dt.tz is not None else None
        evidence['datetime_statistics']={'min':present.min().isoformat() if nonnull else None,
            'max':present.max().isoformat() if nonnull else None,'timezone':tz,
            'timezone_aware':tz is not None if pd.api.types.is_datetime64_any_dtype(series) else None}
        evidence['min_value']=evidence['datetime_statistics']['min']; evidence['max_value']=evidence['datetime_statistics']['max']
    elif dtype=='BOOLEAN':
        evidence['boolean_distribution']={'true_count':int((present==True).sum()),'false_count':int((present==False).sum())}
    evidence['safe_value_policy']='NO_RAW_SAMPLES_OR_CATEGORICAL_LABELS_V1'
    return evidence


def build_evidence(frame,policy,required=()):
    total=len(frame); active=total
    if policy['load_strategy']=='SNAPSHOT':
        flags=frame['_datarise_active']
        if flags.isna().any() or not flags.map(lambda v:isinstance(v,bool)).all(): raise ValueError('Invalid activity state')
        active=int(flags.sum()); frame=frame.loc[flags]
    names=[c['name'] for c in policy['schema_columns']]
    columns=[column_evidence(name,frame[name],policy,required) for name in names]
    summary={'row_count':len(frame),'column_count':len(names),'trusted_rows':total,
        'active_rows':active if policy['load_strategy']=='SNAPSHOT' else None,
        'inactive_rows':total-active if policy['load_strategy']=='SNAPSHOT' else None,
        'load_strategy':policy['load_strategy'],'business_key':policy['business_keys'],
        'event_time_column':policy.get('event_time_column'),'snapshot_coverage':policy.get('snapshot_coverage'),
        'identifier_candidates':[c['original_name'] for c in columns if c['identifier_candidate']],
        'datetime_candidates':[c['original_name'] for c in columns if c['datetime_candidate']],
        'numeric_columns':[c['original_name'] for c in columns if c['canonical_type'] in ('INTEGER','DECIMAL')],
        'categorical_columns':[c['original_name'] for c in columns if c['categorical_statistics'] is not None],
        'potential_sensitive_fields':[c['original_name'] for c in columns if c['sensitivity_hints']]}
    return summary,columns
