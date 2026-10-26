"""Shadow rollout, governance, recovery, non-authority and fail-closed contracts."""
from copy import deepcopy
from dataclasses import asdict, replace
from decimal import Decimal
import json

import pandas as pd
import pytest

from app.processing.metric_execution import PandasMetricExecutionEngine
from app.processing.metric_shadow import ShadowOutcome, compare_metric_results
from app.processing.metric_warehouse_compiler import compile_metric_query
from app.services import metric_shadow_service as shadow
from tests.metric_shadow_fixture import USER, frame, plan, sources, service


@pytest.fixture
def fixture(monkeypatch):
    monkeypatch.setenv('METRIC_BIGQUERY_SHADOW_ENABLED','true')
    p=plan(); frames={10:frame([1,2])}; src=sources()
    run,client,gov=service(src,frames)
    return p,frames,src,run,client,gov


def test_default_off_does_not_resolve_construct_or_admit(monkeypatch):
    monkeypatch.delenv('METRIC_BIGQUERY_SHADOW_ENABLED',raising=False)
    monkeypatch.setenv('BIGQUERY_ENABLED','true')
    def forbidden(*args): pytest.fail('Disabled shadow crossed its boundary')
    run=shadow.MetricShadowService(1,USER,source_resolver=forbidden,
        provider_factory=forbidden,governance_factory=forbidden)
    result=run.execute(plan(),{10:frame([1,2])})
    assert result.pandas_result['value']==3
    assert result.evidence.outcome==ShadowOutcome.DISABLED


@pytest.mark.parametrize('drift',['generation-only','missing-schema','wrong-workspace','unavailable'])
def test_eligibility_fails_before_provider_factory(fixture,drift):
    p,frames,src,run,client,gov=fixture
    if drift=='generation-only': src[10]=replace(src[10],physical_table='synthetic-project.dr_dev_w1.m20_v30_g1')
    if drift=='missing-schema': src[10]=replace(src[10],schema_sha256=None)
    if drift=='wrong-workspace': run.workspace_id=2
    if drift=='unavailable':
        def unavailable(*args): raise ValueError('WAREHOUSE_SERVING_NOT_READY')
        run.source_resolver=unavailable
    result=run.execute(p,frames)
    assert result.evidence.outcome==ShadowOutcome.INELIGIBLE
    assert result.pandas_result['value']==3 and not client.calls and not gov.calls


def test_not_admitted_keeps_pandas_without_claim(fixture):
    p,frames,_,run,client,gov=fixture; gov.admitted=False
    result=run.execute(p,frames)
    assert result.evidence.outcome==ShadowOutcome.NOT_ADMITTED
    assert result.pandas_result['value']==3
    assert [c[0] for c in gov.calls]==['admit'] and not client.jobs


def test_fresh_estimate_drift_releases_only_active(fixture):
    p,frames,_,run,client,gov=fixture
    original=client.query; calls=0
    def drift(*args,**kwargs):
        nonlocal calls
        calls+=1
        if calls==2: client.estimate=200*1024*1024
        return original(*args,**kwargs)
    client.query=drift
    result=run.execute(p,frames)
    assert result.evidence.outcome==ShadowOutcome.REVALIDATION_REQUIRED
    assert gov.row['status']=='RELEASED' and not client.jobs
    assert result.pandas_result['value']==3


@pytest.mark.parametrize('loss',['response','completion'])
def test_uncertain_job_recovered_once_without_second_execution(fixture,loss):
    p,frames,_,run,client,gov=fixture
    client.lose_response=loss=='response'; gov.fail_complete=loss=='completion'
    first=run.execute(p,frames)
    assert first.evidence.outcome==ShadowOutcome.RECOVERY_REQUIRED
    assert gov.row['status']=='EXECUTING' and len(client.jobs)==1
    again=run.compare(p,first.pandas_result)
    assert again.evidence.outcome==ShadowOutcome.RECOVERY_REQUIRED
    assert again.evidence.reservation_id==first.evidence.reservation_id
    assert not any(c[0]=='release' for c in gov.calls)
    gov.fail_complete=False
    recovered=run.compare(p,first.pandas_result,recovery=first.evidence)
    assert recovered.evidence.outcome==ShadowOutcome.MATCH,recovered.evidence
    assert recovered.pandas_result is first.pandas_result
    assert gov.row['status']=='COMPLETED'
    assert len([c for c in client.calls if c[0]=='execute'])==1
    assert len([c for c in gov.calls if c[0]=='claim'])==1


@pytest.mark.parametrize('state',['NOT_FOUND','RUNNING','FAILED'])
def test_recovery_never_blindly_reexecutes(fixture,state):
    p,frames,_,run,client,gov=fixture; client.lose_response=True
    first=run.execute(p,frames); job=next(iter(client.jobs.values()))
    if state=='NOT_FOUND': client.jobs.clear()
    if state=='RUNNING': job.state='RUNNING'
    if state=='FAILED': job.error_result={'reason':'syntheticFailure'}
    result=run.compare(p,first.pandas_result,recovery=first.evidence)
    assert result.evidence.outcome==(ShadowOutcome.PROVIDER_ERROR if state=='FAILED' else ShadowOutcome.RECOVERY_REQUIRED)
    assert gov.row['status']==('FAILED' if state=='FAILED' else 'EXECUTING')
    assert len([c for c in client.calls if c[0]=='execute'])==1
    assert not any(c[0]=='release' for c in gov.calls)


def test_changed_publication_cannot_recover_other_query(fixture):
    p,frames,src,run,client,gov=fixture; client.lose_response=True
    first=run.execute(p,frames)
    src[10]=replace(src[10],publication_id=52,publication_revision=2,
        physical_table='synthetic-project.dr_dev_w1.m20_v30_g1_p52_r2')
    result=run.compare(p,first.pandas_result,recovery=first.evidence)
    assert result.evidence.outcome==ShadowOutcome.REVALIDATION_REQUIRED
    assert gov.row['status']=='EXECUTING'
    assert len([c for c in client.calls if c[0]=='execute'])==1


def test_independent_publication_label_verification_is_not_bypassed(fixture):
    p,frames,src,run,client,gov=fixture
    client.tables[src[10].physical_table].labels['publication']='99'
    result=run.execute(p,frames)
    assert result.evidence.outcome==ShadowOutcome.PROVIDER_ERROR
    assert result.evidence.reason=='PERMISSION_DENIED'
    assert not client.jobs and not gov.calls and result.pandas_result['value']==3


@pytest.mark.parametrize('corruption',['alias','metadata','type','payload'])
def test_malformed_or_unbounded_shadow_never_changes_pandas(fixture,corruption):
    p,frames,_,run,client,gov=fixture
    if corruption=='alias': client.mutate_rows=lambda rows:tuple(dict(r,extra='bad') for r in rows)
    if corruption=='metadata': client.mutate_rows=lambda rows:tuple(dict(r,__input_rows=True) for r in rows)
    if corruption=='type': client.mutate_rows=lambda rows:tuple(dict(r,__metric_value={'bad':'type'}) for r in rows)
    if corruption=='payload':
        p=plan(groups=('category',))
        client.mutate_rows=lambda rows:tuple(dict(r,__group_0='x'*300000) for r in rows)
    result=run.execute(p,frames)
    assert result.evidence.outcome==ShadowOutcome.PROVIDER_ERROR
    assert result.pandas_result['value']==(None if corruption=='payload' else 3)
    assert gov.row['status']=='COMPLETED'  # known provider usage is settled even if decoding fails


def test_legacy_float_does_not_weaken_warehouse_measure_contract(fixture):
    p,frames,_,run,client,gov=fixture
    frames[10]['x']=[1.1,2.2]
    result=run.execute(p,frames)
    assert result.evidence.outcome==ShadowOutcome.INELIGIBLE
    assert result.evidence.reason=='WAREHOUSE_FLOAT_UNSUPPORTED'
    assert not client.calls and not gov.calls


def test_entity_duplicate_and_unresolved_filter_remain_pandas_errors(fixture):
    _,frames,_,run,client,_=fixture
    frames[10]['id']=['same','same']
    with pytest.raises(ValueError,match='GRAIN_NOT_UNIQUE'):
        run.execute(plan(entity=True),frames)
    p=plan(); p.definition['default_filters']=[dict(column=dict(dataset_id=10,column='category'),operator='EQ',category_ref='unresolved')]
    with pytest.raises(ValueError,match='UNRESOLVED_FILTER'):
        run.execute(p,frames)
    assert not client.calls


@pytest.mark.parametrize('field,value',[
    ('publication_id',52),('publication_revision',3),('generation',2),
    ('serving_materialization_id',62),('model_version_id',31),('state_id',101),
])
def test_source_pin_changes_query_identity(field,value):
    p=plan(); a=sources(); b=sources()
    update={field:value}
    if field=='state_id':
        p=replace(p,sources=tuple(dict(pin,state_id=value) for pin in p.sources))
        first=compile_metric_query(plan(),a)
    else: first=compile_metric_query(p,a)
    source=replace(b[10],**update)
    source=replace(source,physical_table=(f'synthetic-project.dr_dev_w1.m{source.model_id}_v{source.model_version_id}'
        f'_g{source.generation}_p{source.publication_id}_r{source.publication_revision}'))
    b[10]=source
    second=compile_metric_query(p,b)
    assert first.request_token!=second.request_token and first.intent_sha256!=second.intent_sha256


@pytest.mark.parametrize('outcome',list(ShadowOutcome))
def test_post_success_observer_cannot_replace_or_mutate_pandas(monkeypatch,outcome):
    monkeypatch.setenv('METRIC_BIGQUERY_SHADOW_ENABLED','true')
    p=plan(); output=PandasMetricExecutionEngine().execute(p,{10:frame([1,2])}); before=deepcopy(output)
    evidence=shadow.MetricShadowEvidence(outcome,p.signature)
    class Fake:
        def compare(self,*args,**kwargs): return shadow.MetricShadowExecution(output,evidence)
    monkeypatch.setattr(shadow,'MetricShadowService',lambda *args:Fake())
    seen=[]
    assert shadow.observe_metric_shadow(p,output,USER,observer=seen.append)==evidence
    assert seen==[evidence] and output==before
    def broken(evidence): raise RuntimeError('private business payload')
    shadow.observe_metric_shadow(p,output,USER,observer=broken)
    assert output==before


def test_comparison_exact_decimal_no_float_round_trip():
    p=plan(); output=PandasMetricExecutionEngine().execute(p,{10:frame([1])})
    left=dict(output,value='9007199254740993.200')
    right=dict(output,value='9007199254740993.2')
    compare=lambda a,b:compare_metric_results(p,a,b,query_intent='a'*64,compiler_version=2)
    assert compare(left,right).match
    assert compare(left,dict(output,value=9007199254740992.0)).categories==('VALUE_MISMATCH',)
    assert compare(dict(output,value=True),output).categories==('TYPE_MISMATCH',)
    assert compare(dict(output,value=None),output).categories==('NULL_MISMATCH',)


def test_comparison_detects_group_order_and_input_coverage(fixture):
    _,frames,src,run,client,_=fixture
    frames[10]['category']=['A','B']; p=plan(groups=('category',))
    client.mutate_rows=lambda rows:tuple(reversed(rows))
    result=run.execute(p,frames)
    assert result.evidence.outcome==ShadowOutcome.MISMATCH
    assert 'ORDER_MISMATCH' in result.evidence.comparison.categories


def test_no_raw_exception_sql_or_values_in_evidence(fixture):
    p,frames,_,run,_,_=fixture
    def broken(*args): raise RuntimeError('private@example.test secret-token SELECT private')
    run.source_resolver=broken
    result=run.execute(p,frames)
    encoded=json.dumps(asdict(result.evidence))
    assert result.evidence.outcome==ShadowOutcome.INELIGIBLE
    assert all(s not in encoded for s in ('private@example.test','secret-token','SELECT private'))


@pytest.mark.parametrize('mode',['match','mismatch','ineligible','recovery','provider-error','raise'])
def test_refresh_commits_and_returns_pandas_regardless_of_shadow(monkeypatch,mode):
    from contextlib import nullcontext
    from app.services import workspace_analytics_service as analytics
    p=plan(); frames={10:frame([1,2])}; stored={}; public=object()
    class Connection:
        result=None
        def cursor(self,**kwargs): return self
        def execute(self,sql,params=None):
            if sql.startswith('SELECT result_id'): self.result=None
            elif 'RETURNING result_id' in sql: self.result={'result_id':1}
            elif "SET status='FRESH'" in sql:
                stored.update(status='FRESH',output=deepcopy(params[0].obj))
            elif "SET status='FAILED',failure_code" in sql and "failure_code='INTERRUPTED'" not in sql:
                pytest.fail('Shadow changed the authoritative attempt status')
            return self
        def fetchone(self): return self.result
    conn=Connection()
    monkeypatch.setattr(analytics,'validate_workspace',lambda *args:None)
    monkeypatch.setattr(analytics.repository,'execution_lock',lambda *args:nullcontext())
    monkeypatch.setattr(analytics,'repository_transaction',lambda:nullcontext(conn))
    monkeypatch.setattr(analytics,'resolve',lambda *args:[dict(plan=p,row=dict(candidate_id=7,run_id=1))])
    monkeypatch.setattr(analytics,'load_sources',lambda *args:())
    monkeypatch.setattr(analytics.BoundedMetricArtifacts,'create',lambda:object())
    monkeypatch.setattr(analytics,'read_frames',lambda *args:frames)
    monkeypatch.setattr(analytics,'read_analytics',lambda *args:public)
    def observe(plan,output,user,**kwargs):
        assert stored['status']=='FRESH' and stored['output']==output
        assert output['value']==3
        if mode=='raise': raise RuntimeError('private shadow error')
        return shadow.MetricShadowEvidence(ShadowOutcome.MATCH,p.signature)
    monkeypatch.setattr(shadow,'observe_metric_shadow',observe)
    assert analytics.refresh_analytics(1,USER) is public
    assert stored['status']=='FRESH' and stored['output']['value']==3


def test_missing_physical_dimension_coverage_does_not_replace_valid_pandas(monkeypatch):
    monkeypatch.setenv('METRIC_BIGQUERY_SHADOW_ENABLED','true')
    frames={10:frame([1,2]),11:pd.DataFrame(dict(parent=['P1'],region=['East']))}
    run,client,gov=service(sources(join=True),frames)
    client.mutate_rows=lambda rows:tuple(dict(r,__missing_dimension_rows=1) for r in rows)
    result=run.execute(plan(join=True,groups=('region',)),frames)
    assert result.pandas_result['groups']==[dict(dimensions=['East'],value=3)]
    assert result.evidence.outcome==ShadowOutcome.PROVIDER_ERROR and result.evidence.reason=='JOIN_COVERAGE'
    assert gov.row['status']=='COMPLETED'


def test_duplicate_current_dimension_cannot_become_serving_evidence():
    from app.warehouse.providers import physical
    from app.warehouse.providers.base import ProviderError
    from tests.test_warehouse_scd import context,first
    c=context(); rows=physical.rows_from_states(c,(first(c),))
    assert rows[0]['is_current'] is True
    with pytest.raises(ProviderError): physical.states(c,rows+deepcopy(rows))


def test_supported_compiler_versions_are_narrow_and_bool_is_not_a_version():
    from tests.test_query_provider_contract import query
    assert query(compiler_version=1).compiler_version==1
    assert query(compiler_version=2).compiler_version==2
    for version in (True,False,0,3,'2'):
        with pytest.raises(ValueError): query(compiler_version=version)
