"""Real multi-delivery accepted state -> verified Gold -> existing fake BigQuery protocol."""
import pytest
from app.db import warehouse_reliability_repository as db
from app.schemas.warehouse import ModelCreate,VersionCreate,SourceMapping,ResourceRegistration
from app.schemas.warehouse_modeling import ModelingRequest
from app.services import warehouse_model_service as models,warehouse_modeling_service as modeling,warehouse_resource_service as resources
from app.services.warehouse_reliability_service import ReliabilityService
from app.services.warehouse_provider_service import ProviderService
from app.warehouse.providers.bigquery import BigQueryTarget
from app.warehouse.providers import fact_physical
from app.warehouse.providers.types import decode
from app.warehouse.dataset_serving import projection,readiness
from tests.bigquery_fake import Client
from tests.test_task21h158e1_gold_postgres import pg,source,pipeline,accepted,refresh,base


def setup(f,monkeypatch):
    monkeypatch.setenv('WAREHOUSE_ENABLED','true');monkeypatch.setenv('APP_ENV','development')
    monkeypatch.setenv('BIGQUERY_ENABLED','true');monkeypatch.setenv('BIGQUERY_PROJECT_ID','synthetic-project')
    w,u=f.workspace,f.user
    model=models.create_model(w,u,ModelCreate(display_name='Synthetic complete dataset'))['warehouse_model_id']
    version=models.create_version(w,model,u,VersionCreate(expected_version=0))['warehouse_model_version_id']
    models.add_source(w,model,version,u,SourceMapping(source_dataset_id=f.dataset))
    with f.src[0]() as conn:policy=conn.execute('SELECT policy_id FROM dataset_load_policies WHERE dataset_id=%s ORDER BY policy_version DESC LIMIT 1',(f.dataset,)).fetchone()[0]
    modeling.configure_version(w,model,version,u,ModelingRequest(source_policy_id=policy,source_rule_version=1,
        configuration=dict(model_type='FACT',grain=dict(description='One current row per source id',identity_columns=['id']),measures=['amount'],attributes=['city'])))
    models.approve_version(w,model,version,u)
    resource=resources.register_resource(w,model,version,u,ResourceRegistration(environment='development',location='asia-south1',generation=1,
        physical_reference=dict(project='synthetic-project',dataset=f'dr_dev_w{w}',table=f'm{model}_v{version}_g1')))['resource_id']
    cloud=Client();provider=ProviderService(w,model,version,u,target_factory=lambda policy,binding:BigQueryTarget(policy,binding,cloud))
    provider.provision(resource)
    return provider,resource,cloud


def load(provider,resource,manifests,key,*,publish=True):
    plan=provider.prepare(resource,manifests);service=provider.reliability
    prior=service.read_progress(resource)['head']
    intent=service.create_intent(resource,'INCREMENTAL' if prior else 'INITIAL_LOAD',[m['manifest_id'] for m in manifests],key,expected=plan.result)
    claim=service.claim(intent['operation_id'],'synthetic-local',lease_seconds=900)
    response=provider.execute(intent['operation_id'],claim['run_id'],claim['fence'])
    if publish:service.publish(intent['operation_id'],claim['run_id'],claim['fence'],response['reconciliation_id'])
    return plan,intent,claim,response


@pytest.mark.parametrize('mode',['APPEND','UPSERT','SNAPSHOT'])
def test_warehouse_visible_active_rows_match_complete_gold(accepted,monkeypatch,mode):
    f=accepted(mode);provider,resource,cloud=setup(f,monkeypatch);service=provider.reliability
    assert readiness(f.service,provider,resource)=='NOT_READY'
    refresh(f);assert readiness(f.service,provider)=='GOLD_VERIFIED'
    assert readiness(f.service,provider,resource)=='WAREHOUSE_PENDING'
    first=service.register(service.discover()[0]['state_id'],f.store())
    load(provider,resource,[first],'first')
    assert readiness(f.service,provider,resource)=='WAREHOUSE_RECONCILING'
    provider.materialize_current_publication(resource)
    assert readiness(f.service,provider,resource)=='ANALYTICS_READY'
    next_payload=b'id,city,amount\n3,C,30\n4,D,40\n' if mode=='APPEND' else b'id,city,amount\n1,B,15\n3,C,30\n'
    f.next(next_payload);assert readiness(f.service,provider,resource)=='NOT_READY'
    refresh(f)
    assessment=projection(f.service,provider,resource,f.store())
    assert not assessment.publication_authorized and len(assessment.accepted_states)==2
    second=service.register(service.discover()[0]['state_id'],f.store())
    assert service.register(second['pin']['state_id'],f.store())==second
    plan,_,_,_=load(provider,resource,[second],'next')
    materialized=provider.materialize_current_publication(resource)
    assert readiness(f.service,provider,resource)=='ANALYTICS_READY'
    with service.transaction() as (conn,source,config,settings):
        binding,context=provider._resolve(conn,config,settings,resource)
        head=db.head(conn,resource)
    rows=cloud.rows[binding.publication_physical(head['publication_id'],head['revision'])]
    mapping={name:(physical,kind) for name,physical,kind in fact_physical.columns(context)}
    active=[r for r in rows if r['is_active']]
    values=[{n:decode(r[p],kind) for n,(p,kind) in mapping.items()} for r in active]
    assert sorted(r['id'] for r in values)==sorted(base(f).id.tolist())
    assert sum(r['amount'] for r in values)==int(base(f).amount.sum())
    assert len(active)==assessment.assessment.rows
    if mode=='SNAPSHOT':assert len(rows)==3 and len(active)==2
    # A physical repeat can reconcile without a second publication/mutation.
    assert provider.materialize_current_publication(resource)==materialized
    assert service.read_progress(resource)['head']['revision']==2


def test_provider_success_is_not_readiness_and_sql_rollback_preserves_old_head(accepted,monkeypatch):
    f=accepted('UPSERT');provider,resource,cloud=setup(f,monkeypatch);service=provider.reliability
    refresh(f);manifest=service.register(service.discover()[0]['state_id'],f.store())
    plan,intent,claim,response=load(provider,resource,[manifest],'pending',publish=False)
    assert readiness(f.service,provider,resource) in ('WAREHOUSE_LOADING','WAREHOUSE_RECONCILING')
    original=db.journal
    def interrupted(conn,run,fence,stage,evidence):
        if stage=='PUBLISHED':raise RuntimeError('Synthetic after-pointer interruption')
        return original(conn,run,fence,stage,evidence)
    with monkeypatch.context() as patch:
        patch.setattr(db,'journal',interrupted)
        with pytest.raises(RuntimeError,match='after-pointer'):
            service.publish(intent['operation_id'],claim['run_id'],claim['fence'],response['reconciliation_id'])
    assert service.read_progress(resource)['head'] is None
    service.publish(intent['operation_id'],claim['run_id'],claim['fence'],response['reconciliation_id'])
    assert readiness(f.service,provider,resource)=='WAREHOUSE_RECONCILING'
    provider.materialize_current_publication(resource)
    assert readiness(f.service,provider,resource)=='ANALYTICS_READY'


def test_ambiguous_source_grain_is_rejected_without_dropping_append_rows(accepted,monkeypatch):
    f=accepted('APPEND');provider,resource,_=setup(f,monkeypatch)
    with f.src[0]() as conn:
        file=conn.execute('SELECT file_id FROM upload_requests WHERE upload_id=%s',(f.upload,)).fetchone()[0]
    f.next(reuse=file);refresh(f)
    assert len(base(f))==4
    with pytest.raises(ValueError,match='ambiguous'):projection(f.service,provider,resource,f.store())
    assert readiness(f.service,provider,resource)=='WAREHOUSE_PENDING'


@pytest.mark.parametrize('failure',['expired','manual_review'])
def test_loading_is_not_ready_and_abandoned_writer_requires_reconciliation(accepted,monkeypatch,failure):
    f=accepted('UPSERT');provider,resource,_=setup(f,monkeypatch);service=provider.reliability
    refresh(f);manifest=service.register(service.discover()[0]['state_id'],f.store())
    plan=provider.prepare(resource,[manifest])
    intent=service.create_intent(resource,'INITIAL_LOAD',[manifest['manifest_id']],'unresolved',expected=plan.result)
    claim=service.claim(intent['operation_id'],'synthetic-local',lease_seconds=900)
    assert readiness(f.service,provider,resource)=='WAREHOUSE_LOADING'
    if failure=='expired':
        with f.src[0]() as conn:
            conn.execute("UPDATE warehouse_writer_leases SET expires_at=clock_timestamp()-interval '1 second' WHERE resource_id=%s",(resource,))
        assert readiness(f.service,provider,resource)=='WAREHOUSE_RECONCILING'
    else:
        service.manual_review(intent['operation_id'],claim['run_id'],claim['fence'])
        assert readiness(f.service,provider,resource)=='WAREHOUSE_FAILED'
    assert service.read_progress(resource)['head'] is None
