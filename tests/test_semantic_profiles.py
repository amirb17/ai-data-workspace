"""Pure deterministic evidence plus real isolated PostgreSQL lifecycle checks."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from decimal import Decimal
import pandas as pd
import pytest
from tests.test_rule_approval_postgres import pg
from tests.test_approved_rule_reuse import deliveries
from tests.test_delivery_execution import pipeline
from tests.test_append_execution import append_pipeline,apply
from tests.test_incremental_foundation import second_delivery
from app.processing.semantic_evidence import build_evidence,name_features,TOP_K
from app.services import dataset_profile_service as service
from app.services import append_application_service as execution
from app.storage.incremental_artifacts import IncrementalArtifacts


def policy(columns,**changes):
    return {'schema_columns':[{'name':c} for c in columns],'load_strategy':'APPEND','business_keys':['customerId'],'event_time_column':'event',**changes}


def test_evidence_statistics_patterns_keys_and_no_values():
    frame=pd.DataFrame({'customerId':['a','b','c','d'], 'amount':[0.,-2.,4.,None],
        'event':pd.to_datetime(['2026-01-01T00:00:00Z','2026-01-03T00:00:00Z',None,None]),
        'contact':['private@example.com','other@example.com',None,None],
        'status':['open','open','closed',None], 'flag':[True,False,True,False]})
    summary,cols=build_evidence(frame,policy(frame.columns),['customerId'])
    c={r['original_name']:r for r in cols}
    assert summary['row_count']==4 and summary['business_key']==['customerId']
    assert c['customerId']['approved_required'] and c['customerId']['authoritative_business_key']
    assert c['customerId']['identifier_candidate'] and c['customerId']['distinct_ratio']==1
    a=c['amount']; assert (a['null_count'],a['non_null_count'],a['null_ratio'])==(1,3,.25)
    assert a['numeric_statistics']['mean']==pytest.approx(2/3) and a['numeric_statistics']['median']==0
    assert a['numeric_statistics']['zero_count']==1 and a['numeric_statistics']['negative_count']==1
    assert c['event']['authoritative_event_time'] and c['event']['datetime_statistics']['timezone']=='UTC'
    assert c['event']['datetime_statistics']['max']=='2026-01-03T00:00:00+00:00'
    assert c['contact']['sensitivity_hints'] and c['contact']['pattern_hints'][0]['pattern']=='email_like'
    assert 'private@example.com' not in str(cols) and 'closed' not in str(cols)
    assert c['status']['categorical_statistics']['top_values'][0]['count']==2
    assert c['flag']['boolean_distribution']=={'true_count':2,'false_count':2}


@pytest.mark.parametrize('name',['Customer ID','customerId','CUSTOMER-ID','customer_id'])
def test_name_normalization(name):
    assert name_features(name)==('customer_id',['customer','id'])


def test_bounded_categories_high_cardinality_empty_and_snapshot():
    frame=pd.DataFrame({'code':['A'+str(i) for i in range(150)],'_datarise_active':[True]*10+[False]*140})
    summary,cols=build_evidence(frame,policy(['code'],business_keys=[],event_time_column=None))
    assert cols[0]['categorical_statistics'] is None and cols[0]['distinct_count']==150
    summary,cols=build_evidence(frame,policy(['code'],load_strategy='SNAPSHOT',business_keys=['code']))
    assert summary['row_count']==10 and summary['inactive_rows']==140
    assert len(cols[0]['categorical_statistics']['top_values'])==TOP_K
    empty=frame.iloc[:0]; summary,cols=build_evidence(empty,policy(['code'],business_keys=[]))
    assert summary['row_count']==0 and cols[0]['distinct_ratio'] is None
    assert cols[0]['nullable'] is None
    assert cols[0]['min_value'] is None


def test_structural_patterns_not_business_classification():
    values={'contact':['+1 (555) 123-4567'],'address':['192.0.2.1'],'link':['https://example.test/x'],
        'reference':['550e8400-e29b-41d4-a716-446655440000'],'patient_name':['Synthetic Person']}
    _,cols=build_evidence(pd.DataFrame(values),policy(values,business_keys=[],event_time_column=None))
    hints={c['original_name']:[p['pattern'] for p in c['pattern_hints']] for c in cols}
    assert 'phone_like' in hints['contact'] and 'ip_like' in hints['address']
    assert 'url_like' in hints['link'] and 'uuid_like' in hints['reference']
    assert cols[-1]['sensitivity_hints'][0]['confidence']=='LOW'
    assert all(c['min_value'] is None for c in cols)
    assert not any(k in cols[0] for k in ('domain','industry','primary_key','entity'))


def test_decimal_and_sensitive_numeric_statistics():
    frame=pd.DataFrame({'amount':[Decimal('1.25'),Decimal('2.75')],'account_number':[1001,1002]})
    _,cols=build_evidence(frame,policy(frame.columns,business_keys=[]))
    assert cols[0]['canonical_type']=='DECIMAL' and cols[0]['numeric_statistics']['mean']==2
    assert cols[0]['min_value']=='1.25' and cols[0]['max_value']=='2.75'
    assert cols[1]['numeric_statistics'] is None and cols[1]['min_value'] is None
    frame=pd.DataFrame({'counter':pd.Series([2**60,2**60+1],dtype='int64')})
    _,cols=build_evidence(frame,policy(['counter'],business_keys=[]))
    assert cols[0]['min_value']==str(2**60) and cols[0]['max_value']==str(2**60+1)


def test_ordered_composite_keys_and_typed_time_without_name_guessing():
    frame=pd.DataFrame({'line':[1,2],'orderId':['A','A'],'recorded':pd.to_datetime(['2026-01-01','2026-01-02']),
                        'order_date':['not a date','still text']})
    summary,cols=build_evidence(frame,policy(frame.columns,business_keys=['orderId','line'],event_time_column='recorded'))
    assert summary['business_key']==['orderId','line']
    c={x['original_name']:x for x in cols}
    assert c['orderId']['business_key_position']==1 and c['line']['business_key_position']==2
    assert c['recorded']['datetime_candidate'] and c['recorded']['datetime_statistics']['timezone'] is None
    assert not c['order_date']['datetime_candidate']
    assert name_features('customer_order_id')==('customer_order_id',['customer','order','id'])
    dates=pd.DataFrame({'recorded':pd.Series([__import__('datetime').date(2026,1,1)],dtype=object)})
    _,columns=build_evidence(dates,policy(['recorded'],business_keys=[]))
    assert columns[0]['datetime_statistics']['timezone_aware'] is None


def test_all_missing_numeric_nonfinite_and_safe_contract():
    from app.schemas.semantic_evidence import ColumnEvidence
    frame=pd.DataFrame({'amount':[float('nan'),float('nan')]})
    _,cols=build_evidence(frame,policy(['amount'],business_keys=[]))
    c=ColumnEvidence.model_validate(cols[0])
    assert c.null_count==2 and c.nullable and c.distinct_ratio is None and c.numeric_statistics is None
    frame=pd.DataFrame({'amount':[float('inf'),1.]})
    _,cols=build_evidence(frame,policy(['amount'],business_keys=[]))
    assert cols[0]['max_value'] is None and cols[0]['numeric_statistics']['mean'] is None
    _,cols=build_evidence(pd.DataFrame({'note':['x'*10000]}),policy(['note'],business_keys=[]))
    assert cols[0]['pattern_scan']['skipped_long_values']==1


@pytest.fixture
def profiled(append_pipeline):
    apply(append_pipeline)
    store=execution.IncrementalArtifacts()
    append_pipeline[2].setattr(service,'IncrementalArtifacts',lambda:store)
    return append_pipeline


def refresh(fixture):
    db=fixture[0][0]; return service.refresh_profile(db[2],db[3],db[1])


def read(fixture):
    db=fixture[0][0]; return service.read_profile(db[2],db[3],db[1])


def test_first_repeat_new_state_history_algorithm_and_immutable(profiled):
    assert read(profiled)['freshness']=='STALE'
    first=refresh(profiled)
    assert first['profile_ready'] and first['summary']['row_count']==2 and first['algorithm_version']==1
    assert next(c for c in first['columns'] if c['original_name']=='id')['approved_required']
    assert refresh(profiled)==first
    apply(profiled,second_delivery(profiled[0]))
    stale=read(profiled); assert stale['freshness']=='STALE' and not stale['columns'] and stale['summary'] is None
    later=refresh(profiled); assert later['summary']['row_count']==4 and later['profile_version']==2
    db=profiled[0][0]
    import psycopg
    with db[0]() as conn:
        assert conn.execute('SELECT count(*) FROM semantic_profiles').fetchone()[0]==2
        assert conn.execute('SELECT summary FROM semantic_profiles WHERE profile_id=%s',(first['profile_id'],)).fetchone()[0]['row_count']==2
    with db[0]() as conn,pytest.raises(psycopg.Error): conn.execute('UPDATE semantic_column_evidence SET evidence=\'{}\'')
    with db[0]() as conn,pytest.raises(psycopg.Error): conn.execute('UPDATE semantic_profiles SET algorithm_version=2')
    profiled[2].setattr(service,'ALGORITHM_VERSION',2)
    assert read(profiled)['freshness']=='STALE'
    assert read(profiled)['built_algorithm_version']==1
    newer=refresh(profiled); assert newer['algorithm_version']==2 and newer['profile_version']==3


@pytest.mark.parametrize('boundary',['build','publish'])
def test_failure_preserves_previous_profile_and_retry(profiled,boundary):
    first=refresh(profiled); apply(profiled,second_delivery(profiled[0]))
    target=service if boundary=='build' else service.repository
    name='build_evidence' if boundary=='build' else 'publish'
    original=getattr(target,name)
    def fail(*args,**kwargs):
        if boundary=='publish': original(*args,**kwargs)
        raise ValueError('private@example.com s3://private')
    profiled[2].setattr(target,name,fail)
    with pytest.raises(RuntimeError,match='previous evidence'):refresh(profiled)
    assert read(profiled)['freshness']=='FAILED' and read(profiled)['profile_id']==first['profile_id']
    profiled[2].setattr(target,name,original)
    assert refresh(profiled)['summary']['row_count']==4
    db=profiled[0][0]
    with db[0]() as conn: assert conn.execute('SELECT count(*) FROM semantic_profiles').fetchone()[0]==2


def test_stale_head_and_io_read_race(profiled):
    original=service.build_evidence
    def race(*args):
        result=original(*args);apply(profiled,second_delivery(profiled[0]));return result
    profiled[2].setattr(service,'build_evidence',race)
    result=refresh(profiled); assert result['freshness']=='STALE' and not result['columns'] and result['profile_id'] is None
    profiled[2].setattr(service,'build_evidence',original)
    assert refresh(profiled)['summary']['row_count']==4
    original=service.repository.columns
    def read_race(*args):
        result=original(*args);apply(profiled,second_delivery(profiled[0]));return result
    profiled[2].setattr(service.repository,'columns',read_race)
    result=read(profiled); assert not result['profile_ready'] and not result['columns']


def test_concurrency_and_api_ownership(profiled):
    entered=Event();release=Event();original=service.build_evidence
    def pause(*args): entered.set();assert release.wait(20);return original(*args)
    profiled[2].setattr(service,'build_evidence',pause)
    with ThreadPoolExecutor(2) as pool:
        future=pool.submit(refresh,profiled);assert entered.wait(20)
        try:
            assert read(profiled)['freshness']=='PROFILING'
            with pytest.raises(RuntimeError,match='already active'):refresh(profiled)
        finally: release.set()
        assert future.result()['profile_ready']
    from app.main import app
    from app.api.identity import get_current_user
    from fastapi.testclient import TestClient
    db=profiled[0][0];app.dependency_overrides[get_current_user]=lambda:db[1]
    try:
        client=TestClient(app);url=f'/workspaces/{db[2]}/datasets/{db[3]}/profile'
        result=client.get(url);assert result.status_code==200 and result.json()['profile_ready']
        assert not any(secret in result.text for secret in ('s3://','manifest','sha256','storage_path','owner_key'))
        assert client.post(url+'/refresh').json()['profile_id']==result.json()['profile_id']
        assert client.get(f'/workspaces/{db[2]+1}/datasets/{db[3]}/profile').status_code==403
        with pytest.raises(PermissionError):service.read_profile(db[2],db[3],{'user_id':999,'owner_key':'other'})
    finally:app.dependency_overrides.pop(get_current_user,None)


def test_not_profiled_and_interrupted_recovery(profiled):
    db=profiled[0][0]
    from psycopg.rows import dict_row
    with db[0]() as conn:
        untouched=conn.execute("INSERT INTO datasets(workspace_id,dataset_name,owner) VALUES (%s,'Empty','dev:test') RETURNING dataset_id",(db[2],)).fetchone()[0]
    assert service.read_profile(db[2],untouched,db[1])['freshness']=='NOT_PROFILED'
    with pytest.raises(ValueError):service.refresh_profile(db[2],untouched,db[1])
    with db[0]() as conn:
        s=conn.cursor(row_factory=dict_row).execute('SELECT s.* FROM datasets d JOIN dataset_state_versions s ON s.state_id=d.current_state_id WHERE d.dataset_id=%s',(db[3],)).fetchone()
        conn.execute("INSERT INTO semantic_profiles(owner_key,workspace_id,dataset_id,source_state_id,dataset_version_id,policy_id,source_sha256,profile_version,algorithm_version,status) VALUES ('dev:test',%s,%s,%s,%s,%s,%s,1,1,'PROFILING')",(db[2],db[3],s['state_id'],s['dataset_version_id'],s['policy_id'],s['manifest_sha256']))
        conn.execute("UPDATE datasets SET profile_status='PROFILING' WHERE dataset_id=%s",(db[3],))
    assert read(profiled)['failure_code']=='INTERRUPTED' and read(profiled)['can_refresh']
    assert refresh(profiled)['profile_ready']
