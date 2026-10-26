"""Dedicated A1.5 differential matrix: real Pandas vs generated SQL + governed SDK fake."""
from dataclasses import asdict, replace
from decimal import Decimal
import json

import pandas as pd
import pytest

from app.processing.metric_shadow import ShadowOutcome
from app.processing.metric_warehouse_compiler import compile_metric_query
from tests.metric_shadow_fixture import frame, plan, sources, service


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setenv('METRIC_BIGQUERY_SHADOW_ENABLED','true')


@pytest.mark.parametrize('op',['COUNT','COUNT_DISTINCT','SUM','AVG','MIN','MAX','DIVIDE','MULTIPLY'])
@pytest.mark.parametrize('values',[[1,2,2,-1],[None,2,None,4],[],[7]],ids=['normal-duplicates','null-heavy','empty','one-row'])
def test_integer_formula_matrix(op,values):
    p=plan(op); frames={10:frame(values)}; src=sources()
    run,cloud,gov=service(src,frames)
    before=frames[10].copy(deep=True)
    result=run.execute(p,frames)
    assert result.evidence.outcome==ShadowOutcome.MATCH, result.evidence
    assert result.evidence.comparison.match
    assert result.evidence.consumer=='METRIC'
    assert [c[0] for c in gov.calls]==['admit','claim','complete']
    assert len(cloud.jobs)==1
    pd.testing.assert_frame_equal(before,frames[10])


@pytest.mark.parametrize('op',['COUNT','COUNT_DISTINCT','SUM','AVG','MIN','MAX','MULTIPLY'])
def test_decimal_scale_negative_and_null_formula_matrix(op):
    values=[Decimal('1.2'),Decimal('1.200'),Decimal('-0.1'),None]
    frames={10:frame(values,numeric='BIGNUMERIC')}; src=sources(numeric='BIGNUMERIC')
    run,_,_=service(src,frames)
    result=run.execute(plan(op),frames)
    assert result.evidence.outcome==ShadowOutcome.MATCH, result.evidence
    if op=='AVG':
        assert isinstance(result.pandas_result['value'],float)  # existing legacy contract
        query=compile_metric_query(plan(op),src)
        assert 'SAFE_DIVIDE(CAST(SUM(f.c1) AS FLOAT64), COUNT(f.c1))' in query.sql


@pytest.mark.parametrize('x,y,expected',[
    ('1','2','0.5'),('1','3','0.'+'3'*38),('1','0',None),(None,'2',None),('1',None,None),
])
def test_decimal_division_independent_high_precision_reference(x,y,expected):
    # Explicit BigQuery native result fixtures, never derived from Pandas output.
    x=Decimal(x) if x is not None else None; y=Decimal(y) if y is not None else None
    expected=Decimal(expected) if expected is not None else None
    frames={10:frame([x],ys=[y],numeric='BIGNUMERIC')}; src=sources(numeric='BIGNUMERIC')
    run,_,_=service(src,frames,decimal_reference=(expected,))
    result=run.execute(plan('DIVIDE'),frames)
    assert result.evidence.outcome==ShadowOutcome.MATCH, result.evidence


@pytest.mark.parametrize('values,ys,op',[
    ([2**62,2**62],None,'SUM'),([2**32],[2**32],'MULTIPLY'),
    ([Decimal('12345678901234567890.123456')],None,'SUM'),
])
def test_precision_beyond_float_and_int64_aggregation(values,ys,op):
    kind='BIGNUMERIC' if isinstance(values[0],Decimal) else 'INT64'
    frames={10:frame(values,ys=ys,numeric=kind)}; src=sources(numeric=kind)
    run,_,_=service(src,frames)
    result=run.execute(plan(op),frames)
    assert result.evidence.outcome==ShadowOutcome.MATCH, result.evidence
    assert isinstance(result.pandas_result['value'],str)


@pytest.mark.parametrize('groups',[('category',),('category','region'),('category','region','id')])
@pytest.mark.parametrize('values',[[1,None,3,4],[]],ids=['null-groups','empty-groups'])
def test_group_shape_keys_null_and_order(groups,values):
    n=len(values)
    frames={10:frame(values,categories=['B',None,'A','B'][:n],regions=['West','East',None,'East'][:n])}
    run,_,_=service(sources(),frames)
    result=run.execute(plan(groups=groups),frames)
    assert result.evidence.outcome==ShadowOutcome.MATCH,result.evidence


@pytest.mark.parametrize('n',[99,100,101])
def test_group_limit_boundary(n):
    frames={10:frame([1]*n,categories=[f'G{i:03d}' for i in range(n)])}
    run,_,_=service(sources(),frames)
    result=run.execute(plan(groups=('category',)),frames)
    assert result.evidence.outcome==ShadowOutcome.MATCH,result.evidence
    assert result.pandas_result['total_groups']==n
    assert result.pandas_result['truncated']==(n>100)


@pytest.mark.parametrize('bucket',['DAY','WEEK','MONTH'])
@pytest.mark.parametrize('timezone',['UTC','Asia/Kolkata'])
def test_time_calendar_rollover_and_week_start(bucket,timezone):
    frames={10:frame([1,2,3,4],dates=['2025-12-28T18:29:59Z','2025-12-28T18:30:00Z',
        '2025-12-31T18:30:00Z','2026-01-04T23:59:59Z'])}
    frames[10]['event']=frames[10]['event'].dt.tz_convert(timezone)
    src=sources(timezone=timezone); run,_,_=service(src,frames)
    result=run.execute(plan(bucket=bucket),frames)
    assert result.evidence.outcome==ShadowOutcome.MATCH,result.evidence
    assert all('T00:00:00' in g['dimensions'][-1] for g in result.warehouse_result['groups'])


@pytest.mark.parametrize('bucket',['DAY','WEEK','MONTH'])
def test_date_not_timestamp_and_null_time(bucket):
    frames={10:frame([1,2],dates=['2026-01-01',None])}
    frames[10]['event']=frames[10]['event'].dt.tz_localize(None)
    src=sources(time_kind='DATE'); run,_,_=service(src,frames)
    result=run.execute(plan(bucket=bucket),frames)
    assert result.evidence.outcome==ShadowOutcome.MATCH,result.evidence


def test_one_hop_dimension_group_and_entity_grain():
    frames={10:frame([1,2,3]),11:pd.DataFrame(dict(parent=['P1'],region=['North']))}
    src=sources(join=True); run,_,_=service(src,frames)
    result=run.execute(plan(groups=('region',),join=True,entity=True),frames)
    assert result.evidence.outcome==ShadowOutcome.MATCH,result.evidence
    assert result.pandas_result['groups']==[dict(dimensions=['North'],value=6)]


def test_mismatch_is_observable_and_evidence_contains_no_business_values():
    frames={10:frame([12345],categories=['private-label'])}; src=sources()
    run,cloud,_=service(src,frames)
    cloud.mutate_rows=lambda rows:tuple(dict(r,__metric_value=999) for r in rows)
    result=run.execute(plan(),frames)
    assert result.pandas_result['value']==12345
    assert result.evidence.outcome==ShadowOutcome.MISMATCH
    assert result.evidence.comparison.categories==('VALUE_MISMATCH',)
    evidence=json.dumps(asdict(result.evidence))
    assert 'private-label' not in evidence and '12345' not in evidence and '__metric_value' not in evidence


@pytest.mark.parametrize('op',['MIN','MAX','COUNT_DISTINCT'])
def test_exact_selection_preserves_current_canonical_decimal_context(op):
    value=Decimal('12345678901234567890.123456789012')
    frames={10:frame([value],numeric='BIGNUMERIC')}
    run,_,_=service(sources(numeric='BIGNUMERIC'),frames)
    result=run.execute(plan(op),frames)
    assert result.evidence.outcome==ShadowOutcome.MATCH,result.evidence
    if op!='COUNT_DISTINCT': assert result.pandas_result['value']==str(value*Decimal(1))


def test_nonportable_decimal_reduction_is_explicitly_ineligible_not_forced_to_match():
    # Pandas' existing 28-digit, order-sensitive Decimal arithmetic can lose the
    # middle 1. BigQuery exact SUM is associative and returns 1. Do not change
    # Pandas or report these as equivalent by choosing a tolerance.
    values=[Decimal('1e28'),Decimal(1),Decimal('-1e28')]
    frames={10:frame(values,numeric='BIGNUMERIC')}
    run,client,_=service(sources(numeric='BIGNUMERIC'),frames)
    result=run.execute(plan(),frames)
    assert Decimal(result.pandas_result['value'])==0
    assert result.evidence.outcome==ShadowOutcome.INELIGIBLE
    assert result.evidence.reason=='NUMERIC_PRECISION_UNSUPPORTED' and not client.jobs
