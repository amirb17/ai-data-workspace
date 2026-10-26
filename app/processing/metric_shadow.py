"""Provider-neutral semantic comparison; evidence contains hashes, never business rows."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, getcontext
from enum import StrEnum
import json
import math
import pandas as pd

from app.processing.metric_execution import (
    MAX_GROUPS, MAX_OUTPUT_BYTES, MAX_ROWS, PANDAS_ENGINE_ID, PANDAS_ENGINE_VERSION,
    MetricExecutionPlan,
)
from app.processing.relationship_evidence import digest

COMPARISON_VERSION = 1


class ShadowOutcome(StrEnum):
    DISABLED = 'DISABLED'
    MATCH = 'MATCH'
    MISMATCH = 'MISMATCH'
    INELIGIBLE = 'INELIGIBLE'
    NOT_ADMITTED = 'NOT_ADMITTED'
    REVALIDATION_REQUIRED = 'REVALIDATION_REQUIRED'
    RECOVERY_REQUIRED = 'RECOVERY_REQUIRED'
    PROVIDER_ERROR = 'PROVIDER_ERROR'


@dataclass(frozen=True)
class MetricShadowComparison:
    match: bool
    categories: tuple[str, ...]
    pandas_signature: str
    warehouse_signature: str
    comparison_signature: str
    pandas_input_rows: int
    warehouse_input_rows: int
    pandas_total_groups: int
    warehouse_total_groups: int
    normalization_version: int = COMPARISON_VERSION


def check_numeric_portability(plan, frames, column_kinds, pandas_result):
    """Conservative exact-arithmetic eligibility, never a second metric evaluator.

    Pandas Decimal reductions round under its ambient context; warehouse SUM is
    associative exact numeric arithmetic. Admit only a range/scale bound proving
    intermediates fit both contracts. Rejected data still uses Pandas normally.
    COUNT DISTINCT and unmodified MIN/MAX do not need a reduction precision cap.
    """
    bounds={}
    n=len(frames[plan.definition['base_dataset_id']])
    growth=len(str(n-1)) if n>1 else 0  # ceil(log10(n)), without float
    for node in plan.definition['formula']['nodes']:
        op=node['op']; args=[bounds[a] for a in node.get('args',[])]
        if op=='COLUMN':
            ref=node['column']; kind=column_kinds[(ref['dataset_id'],ref['column'])]
            whole=fraction=0
            if kind in ('INT64','BIGNUMERIC'):
                for value in frames[ref['dataset_id']][ref['column']]:
                    if pd.isna(value): continue
                    if hasattr(value,'item'): value=value.item()
                    number=Decimal(value)
                    if not number.is_finite(): raise ValueError('NUMERIC_PRECISION_UNSUPPORTED')
                    _,digits,exponent=number.as_tuple()
                    digits=list(digits)
                    while len(digits)>1 and digits[-1]==0:
                        digits.pop(); exponent+=1
                    whole=max(whole,len(digits)+exponent,0)
                    fraction=max(fraction,-exponent,0)
            bound=(whole,fraction,kind)
        elif op=='MULTIPLY':
            bound=(args[0][0]+args[1][0],args[0][1]+args[1][1],
                   'BIGNUMERIC' if any(a[2]=='BIGNUMERIC' for a in args) else 'INT64')
            if bound[2]=='BIGNUMERIC' and sum(bound[:2])>getcontext().prec:
                raise ValueError('NUMERIC_PRECISION_UNSUPPORTED')
        elif op in ('SUM','AVG'):
            bound=(args[0][0]+growth,args[0][1],args[0][2])
            if bound[2]=='BIGNUMERIC' and sum(bound[:2])>getcontext().prec:
                raise ValueError('NUMERIC_PRECISION_UNSUPPORTED')
        elif op in ('MIN','MAX'):
            bound=args[0]
        else:
            bound=(0,0,'AGGREGATE')
        if bound[0]>38 or bound[1]>38:
            raise ValueError('NUMERIC_PRECISION_UNSUPPORTED')
        bounds[node['id']]=bound
    # A Decimal ratio outside BigQuery's scale/range envelope cannot preserve
    # the authoritative result even when its operands are individually valid.
    values=([pandas_result['value']] if pandas_result['kind']=='scalar' else
            [g['value'] for g in pandas_result['groups']])
    for value in values:
        if isinstance(value,str):
            _,digits,exponent=Decimal(value).as_tuple()
            digits=list(digits)
            while len(digits)>1 and digits[-1]==0:
                digits.pop(); exponent+=1
            if max(len(digits)+exponent,0)>38 or max(-exponent,0)>38:
                raise ValueError('NUMERIC_PRECISION_UNSUPPORTED')


def _number(value):
    # Numeric strings are the existing MetricScalar representation for Decimal
    # and integers outside JSON's safe range. Group labels are handled separately.
    if isinstance(value, bool):
        return ('boolean', value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError('NONFINITE_RESULT')
        value = str(value)  # exact existing JSON/display representation; no Decimal -> float
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError('METRIC_RESULT_TYPE') from None
    if not number.is_finite():
        raise ValueError('NONFINITE_RESULT')
    sign, digits, exponent = number.as_tuple()
    digits = list(digits)
    if not any(digits):
        return ('number', 0, (0,), 0)
    while digits[-1] == 0:
        digits.pop()
        exponent += 1
    return ('number', sign, tuple(digits), exponent)


def _value(value):
    return ('null',) if value is None else _number(value)


def _group_key(value, *, numeric=False, temporal=False):
    if value is None:
        return ('null',)
    if temporal:
        if not isinstance(value, str):
            raise ValueError('METRIC_RESULT_TIME')
        try:
            bucket = datetime.fromisoformat(value)
        except ValueError:
            raise ValueError('METRIC_RESULT_TIME') from None
        if bucket.tzinfo is not None:
            raise ValueError('METRIC_RESULT_TIME')
        return ('time', bucket.isoformat())
    if numeric or type(value) in (int, float) or isinstance(value, Decimal):
        return _number(value)
    if type(value) in (str, bool):
        return (type(value).__name__, value)
    raise ValueError('METRIC_RESULT_TYPE')


def _normalize(plan, output, group_kinds):
    fields = {'kind','value','groups','input_rows','total_groups','truncated','display_hint'}
    if not isinstance(output, dict) or set(output) != fields:
        raise ValueError('METRIC_RESULT_SHAPE')
    if len(json.dumps(output,allow_nan=False).encode()) > MAX_OUTPUT_BYTES:
        raise ValueError('OUTPUT_LIMIT')
    if (type(output['input_rows']) is not int or not 0 <= output['input_rows'] <= MAX_ROWS
            or type(output['total_groups']) is not int or output['total_groups'] < 0
            or type(output['truncated']) is not bool or not isinstance(output['groups'],list)
            or len(output['groups']) > MAX_GROUPS):
        raise ValueError('METRIC_RESULT_SHAPE')
    if output['kind'] == 'scalar':
        if output['groups'] or output['total_groups'] or output['truncated'] or output['display_hint'] != 'scalar':
            raise ValueError('METRIC_RESULT_SHAPE')
    elif output['kind'] == 'grouped':
        if (output['value'] is not None or len(output['groups']) != min(output['total_groups'],MAX_GROUPS)
                or output['truncated'] != (output['total_groups'] > MAX_GROUPS)
                or output['display_hint'] not in ('line','table')):
            raise ValueError('METRIC_RESULT_SHAPE')
    else:
        raise ValueError('METRIC_RESULT_SHAPE')
    count = len(plan.definition['dimensions']) + bool(plan.definition.get('time_field'))
    groups = []
    for group in output['groups']:
        if set(group) != {'dimensions','value'} or len(group['dimensions']) != count:
            raise ValueError('METRIC_RESULT_SHAPE')
        keys = tuple(_group_key(key,
            numeric=(index < len(group_kinds) and group_kinds[index] in ('INT64','BIGNUMERIC')),
            temporal=bool(plan.definition.get('time_field')) and index == count-1)
            for index,key in enumerate(group['dimensions']))
        groups.append((keys,_value(group['value'])))
    if len({keys for keys,_ in groups}) != len(groups):
        raise ValueError('METRIC_RESULT_DUPLICATE_GROUP')
    return dict(kind=output['kind'],value=_value(output['value']),groups=groups,
        input_rows=output['input_rows'],total_groups=output['total_groups'],
        truncated=output['truncated'],display_hint=output['display_hint'])


def compare_metric_results(plan: MetricExecutionPlan, pandas_result, warehouse_result, *,
                           query_intent: str, compiler_version: int, publication_pins=(), group_kinds=()):
    """Exact canonical numeric/NULL/key/order comparison, with no tolerance or float coercion."""
    if not isinstance(plan,MetricExecutionPlan):
        raise ValueError('Metric execution plan required')
    left = _normalize(plan,pandas_result,group_kinds)
    right = _normalize(plan,warehouse_result,group_kinds)
    categories = set()
    if (left['kind'],left['display_hint']) != (right['kind'],right['display_hint']):
        categories.add('SHAPE_MISMATCH')
    if left['input_rows'] != right['input_rows']:
        categories.add('COVERAGE_MISMATCH')
    if left['total_groups'] != right['total_groups']:
        categories.add('GROUP_COUNT_MISMATCH')
    if left['truncated'] != right['truncated']:
        categories.add('TRUNCATION_MISMATCH')
    lkeys,rkeys = [g[0] for g in left['groups']], [g[0] for g in right['groups']]
    if lkeys != rkeys:
        categories.add('ORDER_MISMATCH' if set(lkeys)==set(rkeys) else 'GROUP_KEY_MISMATCH')
        if plan.definition.get('time_field'):
            categories.add('TIME_BUCKET_MISMATCH')
    def compare_value(a,b):
        if a == b:
            return
        categories.add('NULL_MISMATCH' if 'null' in (a[0],b[0]) else
                       'TYPE_MISMATCH' if a[0] != b[0] else 'VALUE_MISMATCH')
    if left['kind'] == right['kind'] == 'scalar':
        compare_value(left['value'],right['value'])
    for keys, value in left['groups']:
        match = dict(right['groups']).get(keys)
        if match is not None:
            compare_value(value,match)
    context = getcontext()
    pins = [plan.signature, publication_pins, (context.prec,context.rounding), COMPARISON_VERSION]
    psha = digest([PANDAS_ENGINE_ID,PANDAS_ENGINE_VERSION,pins,left])
    wsha = digest(['BIGQUERY',compiler_version,query_intent,pins,right])
    cats = tuple(sorted(categories))
    return MetricShadowComparison(not cats,cats,psha,wsha,digest([psha,wsha,cats]),
        left['input_rows'],right['input_rows'],left['total_groups'],right['total_groups'])
