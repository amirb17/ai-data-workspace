"""Actual registered sources/stages/applications and independent dataset Gold proof."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from threading import Event
import io
import json
import hashlib
import pytest
import psycopg

from app.db import database
from app.services import dataset_gold_serving_service as gold
from app.storage.cumulative_gold_artifacts import CumulativeGoldArtifacts
from app.schemas.incremental import LoadPolicyRequest,SnapshotDeliveryRequest
from app.services.incremental_policy_service import save_policy
from app.services.snapshot_context_service import declare_snapshot
from tests.test_rule_approval_postgres import pg
from tests.test_processing_jobs_postgres import source
from tests.test_task20_processing_postgres import pipeline,approve,new_delivery


class PinnedClient:
    def __init__(self,client):self.client=client
    def get_object(self,*,Bucket,Key):
        version=self.client.head_object(Bucket=Bucket,Key=Key)['VersionId']
        return self.client.get_object(Bucket=Bucket,Key=Key,VersionId=version)
    def put_object(self,**kwargs):return self.client.put_object(**kwargs)


@pytest.fixture
def accepted(pipeline):
    src,client,run=pipeline
    def setup(mode='APPEND',payload=None):
        upload=src[2] if payload is None else new_delivery(pipeline,payload)[0]
        run('BRONZE','first-bronze',upload=upload);association=approve(src)
        with src[0]() as conn:
            ws,ds,version=conn.execute('SELECT d.workspace_id,d.dataset_id,v.dataset_version_id FROM datasets d JOIN dataset_versions v USING(dataset_id) JOIN dataset_version_files f USING(dataset_version_id) WHERE f.dataset_version_file_id=%s',(association,)).fetchone()
        user=dict(user_id=src[1],owner_key='dev:test')
        policy=save_policy(ws,ds,user,LoadPolicyRequest(dataset_version_id=version,expected_policy_version=0,
            load_strategy=mode,business_keys=[] if mode=='APPEND' else ['id'],schema_evolution_policy='STRICT',
            snapshot_coverage='COMPLETE' if mode=='SNAPSHOT' else None))
        if mode=='SNAPSHOT':declare_snapshot(ws,ds,user,upload,SnapshotDeliveryRequest(coverage='COMPLETE',effective_at='2026-10-01T00:00:00Z'))
        run('SILVER','first-silver',upload=upload)
        store=lambda:gold.EvidenceStore(PinnedClient(client))
        verifier=gold.GoldVerifier(src[0],store)
        service=gold.DatasetGoldServing(store,verifier,enabled=True)
        def next_(payload=None,*,reuse=None,effective='2026-10-02T00:00:00Z',before_silver=None):
            next_upload,file=new_delivery(pipeline,payload,reuse_file=reuse)
            run('BRONZE','bronze-'+str(next_upload),upload=next_upload)
            if before_silver:before_silver(next_upload,file)
            if mode=='SNAPSHOT':declare_snapshot(ws,ds,user,next_upload,SnapshotDeliveryRequest(coverage='COMPLETE',effective_at=effective))
            run('SILVER','silver-'+str(next_upload),upload=next_upload)
            return next_upload,file
        return SimpleNamespace(src=src,client=client,run=run,workspace=ws,dataset=ds,user=user,policy=policy,version=version,
            association=association,upload=upload,store=store,verifier=verifier,service=service,next=next_)
    return setup


def refresh(f):return f.service.refresh(f.workspace,f.dataset,f.user)
def current(f):return f.service.current(f.workspace,f.dataset,f.user)
def base(f):
    evidence=current(f);artifact=evidence['catalog'][0]
    return f.store().frame(artifact['storage_path'],artifact['sha256'])


@pytest.mark.parametrize('mode',['APPEND','UPSERT','SNAPSHOT'])
def test_two_deliveries_are_complete_dataset_not_latest_delivery(accepted,mode):
    f=accepted(mode)
    first=refresh(f);assert base(f).id.tolist()==[1,2]
    f.next(b'id,city,amount\n1,B,15\n3,C,30\n')
    second=refresh(f);assert second!=first
    frame=base(f)
    assert frame.id.tolist()==({'APPEND':[1,2,1,3],'UPSERT':[1,2,3],'SNAPSHOT':[1,3]}[mode])
    assert int(frame.amount.sum())==({'APPEND':75,'UPSERT':65,'SNAPSHOT':45}[mode])
    assert not any(name.startswith('_datarise') for name in frame.columns)
    assert len(current(f)['source_contract']['history'])==2
    if mode=='SNAPSHOT':
        with f.src[0]() as conn:
            state=conn.execute('SELECT row_count FROM dataset_state_versions WHERE state_id=(SELECT current_state_id FROM datasets WHERE dataset_id=%s)',(f.dataset,)).fetchone()[0]
        assert state==3  # inactive row remains in accepted state, not serving base.
    writes=len(f.client.latest)
    assert refresh(f)==second and len(f.client.latest)==writes


def test_interrupted_head_publication_reuses_persisted_verification(accepted,monkeypatch):
    f=accepted();original=gold.publish_gold
    with monkeypatch.context() as patch:
        def crash(*args):original(*args);raise RuntimeError('Synthetic post-pointer failure')
        patch.setattr(gold,'publish_gold',crash)
        with pytest.raises(RuntimeError,match='post-pointer'):refresh(f)
    with f.src[0]() as conn:
        assert conn.execute('SELECT current_gold_run_id FROM datasets WHERE dataset_id=%s',(f.dataset,)).fetchone()[0] is None
        assert conn.execute('SELECT count(*) FROM dataset_gold_evidence').fetchone()[0]==1
        assert conn.execute("SELECT count(*) FROM dataset_gold_runs WHERE status='SUCCESS'").fetchone()[0]==0
        assert conn.execute("SELECT status FROM dataset_gold_runs").fetchone()[0]=='FAILED'
        assert conn.execute('SELECT state_analytics_status FROM datasets WHERE dataset_id=%s',(f.dataset,)).fetchone()[0]=='FAILED'
    writes=len(f.client.latest);refresh(f)
    assert len(f.client.latest)==writes and len(base(f))==2


def test_zero_valid_delivery_does_not_erase_previous_dataset(accepted):
    f=accepted(payload=b'id,city,amount\n1.0,A,10\n2.0,B,20\n')
    refresh(f)
    f.next(b'id,city,amount\n,A,10\n,B,20\n')
    refresh(f)
    assert len(base(f))==2 and base(f).amount.sum()==30
    with f.src[0]() as conn:
        assert conn.execute('SELECT valid_rows,rejected_rows FROM data_quality_runs ORDER BY dq_run_id DESC LIMIT 1').fetchone()==(0,2)


def test_snapshot_stale_effective_time_cannot_rewind_serving(accepted):
    f=accepted('SNAPSHOT');f.next(b'id,city,amount\n1,B,15\n3,C,30\n');refresh(f)
    f.next(b'id,city,amount\n1,Z,999\n',effective='2026-10-01T00:00:00Z')
    refresh(f)
    assert base(f).id.tolist()==[1,3] and int(base(f).amount.sum())==45


def test_expected_head_cas_and_actual_concurrent_refresh_exclusion(accepted):
    f=accepted();entered,release=Event(),Event();verify=f.verifier.verify
    def held(*args):
        result=verify(*args);entered.set()
        if not release.wait(60):raise TimeoutError('Verifier release missing')
        return result
    f.verifier.verify=held
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(refresh,f)
            try:
                assert entered.wait(60)
                with pytest.raises(RuntimeError,match='already active'):refresh(f)
                # A real competing accepted publication advances the dataset,
                # while refresh owns only its separate analytics lock.
                f.next(b'id,city,amount\n3,B,30\n4,C,40\n')
            finally:release.set()
            with pytest.raises(gold.ServingRejected,match='CAS'):future.result(timeout=60)
    finally:f.verifier.verify=verify;release.set()
    with f.src[0]() as conn:assert conn.execute('SELECT current_gold_run_id FROM datasets WHERE dataset_id=%s',(f.dataset,)).fetchone()[0] is None
    refresh(f);assert len(base(f))==4


def test_independent_values_verification_rejects_checksum_consistent_bad_gold(accepted,monkeypatch):
    f=accepted();build=gold.reconstruct
    def bad(store,w,history):
        artifacts=build(store,w,history);artifacts[0][0].loc[0,'amount']=999
        return artifacts
    with monkeypatch.context() as patch:
        # Change only the publisher builder, not the independent verifier.
        original=f.verifier.verify
        def verified(*args):
            with patch.context() as independent:
                independent.setattr(gold,'reconstruct',build)
                return original(*args)
        patch.setattr(gold,'reconstruct',bad);patch.setattr(f.verifier,'verify',verified)
        with pytest.raises(gold.ServingRejected,match='differs'):refresh(f)
    with f.src[0]() as conn:
        assert conn.execute('SELECT count(*) FROM dataset_gold_evidence').fetchone()[0]==0
        assert conn.execute('SELECT current_gold_run_id FROM datasets WHERE dataset_id=%s',(f.dataset,)).fetchone()[0] is None


def test_forged_python_certificate_is_not_persisted_verifier_authority(accepted):
    f=accepted()
    def fake(w,d,u,run,key,sha,contract):
        columns=[c['name'] for c in contract['history'][-1]['policy']['schema_columns']]
        catalog=f.store().validate(run,key,sha,2,columns)
        return gold.Certificate(run['gold_run_id'],gold.digest(contract),sha,gold.digest(catalog)),catalog
    f.verifier.verify=fake
    with pytest.raises(gold.ServingRejected,match='Persisted'):refresh(f)


def test_epoch_revocation_ownership_and_legacy_adoption_boundary(accepted):
    f=accepted();refresh(f);before=current(f)['verification_id'];writes=len(f.client.latest)
    with f.src[0]() as conn:conn.execute('UPDATE processing_dependency_epochs SET revision=revision+1 WHERE dataset_id=%s',(f.dataset,))
    with pytest.raises(gold.ServingRejected):current(f)
    refresh(f)
    assert current(f)['verification_id']>before and len(f.client.latest)==writes
    with f.src[0]() as conn:conn.execute("UPDATE datasets SET owner='dev:other' WHERE dataset_id=%s",(f.dataset,))
    with pytest.raises(PermissionError):current(f)


def test_disabled_and_outer_transaction_cannot_publish(accepted):
    f=accepted()
    with pytest.raises(PermissionError):gold.DatasetGoldServing(f.store,f.verifier).refresh(f.workspace,f.dataset,f.user)
    with database.repository_transaction():
        with pytest.raises(RuntimeError,match='Nested'):refresh(f)
    with f.src[0]() as conn:assert conn.execute('SELECT count(*) FROM dataset_gold_runs').fetchone()[0]==0


def test_different_applied_rule_snapshots_preserve_old_accepted_rows(accepted,monkeypatch):
    from app.services import business_rule_service as rules,processing_context_service as context
    from app.schemas.business_rules import BusinessRuleAnswer
    f=accepted();refresh(f)
    def review(upload,file):
        with f.src[0]() as conn:
            association=conn.execute('SELECT dataset_version_file_id FROM dataset_version_files WHERE file_id=%s',(file,)).fetchone()[0]
            # Fixture-only return to review; actual answer/version/finalization
            # use the existing authenticated transactional workflow below.
            conn.execute("UPDATE dataset_version_files SET status='AWAITING_RULES',applied_rule_version=NULL WHERE dataset_version_file_id=%s",(association,))
        monkeypatch.setattr(rules,'generate_rule_suggestions',lambda _:[dict(column_name=c,suggested_rule_type='NOT_NULL',options=['YES','NO']) for c in ('id','city')])
        with context.rule_mutation(association,f.user,1,f.workspace,f.dataset):
            changed=rules.submit_business_rule_answers(association,[BusinessRuleAnswer(column_name=c,rule_type='NOT_NULL',answer='YES') for c in ('id','city')])
            assert changed['rule_version']==2
        with context.rule_mutation(association,f.user,2,f.workspace,f.dataset):assert rules.finalize_business_rules(association)['finalized']
    f.next(b'id,city,amount\n3,,30\n4,D,40\n',before_silver=review)
    refresh(f)
    assert base(f).id.tolist()==[1,2,4] and int(base(f).amount.sum())==70
    history=current(f)['source_contract']['history']
    assert [h['application']['applied_rule_version'] for h in history]==[1,2]
    assert [(h['dq']['valid_rows'],h['dq']['rejected_rows']) for h in history]==[(2,0),(1,1)]


def test_schema_drift_requires_existing_contract_review_not_serving_inference(accepted):
    f=accepted();refresh(f);prior=base(f)
    with pytest.raises(psycopg.DatabaseError,match='Approved'):
        f.next(b'id,city,amount,extra\n3,C,30,new\n')
    refresh(f)
    import pandas as pd
    pd.testing.assert_frame_equal(base(f),prior)


@pytest.mark.parametrize('kind',['state','silver'])
def test_damaged_pinned_artifacts_cannot_be_certified(accepted,kind):
    f=accepted()
    with f.src[0]() as conn:
        if kind=='state':key=conn.execute('SELECT manifest_key FROM dataset_state_versions WHERE state_id=(SELECT current_state_id FROM datasets WHERE dataset_id=%s)',(f.dataset,)).fetchone()[0]
        else:key=conn.execute('SELECT silver_path FROM data_quality_runs ORDER BY dq_run_id LIMIT 1').fetchone()[0]
    version=f.client.latest[key]
    f.client.objects[key,version]=b'SYNTHETIC DAMAGED EVIDENCE'
    with pytest.raises(ValueError):refresh(f)
    with f.src[0]() as conn:assert conn.execute('SELECT count(*) FROM dataset_gold_evidence').fetchone()[0]==0


def test_verification_evidence_is_append_only_and_migration_once_only(accepted):
    from pathlib import Path
    f=accepted();refresh(f)
    with f.src[0]() as conn:
        for statement in ('UPDATE dataset_gold_evidence SET verified_at=clock_timestamp()', 'DELETE FROM dataset_gold_evidence','TRUNCATE dataset_gold_evidence'):
            with pytest.raises(psycopg.DatabaseError):
                with conn.transaction():conn.execute(statement)
        with pytest.raises(psycopg.errors.DuplicateTable):
            with conn.transaction():conn.execute(Path('migrations/0045_dataset_gold_evidence.sql').read_text())
        assert conn.execute('SELECT count(*) FROM dataset_gold_evidence').fetchone()[0]==1


def test_in_place_historical_rule_tamper_is_not_a_new_approval(accepted):
    from psycopg.types.json import Jsonb
    f=accepted();refresh(f)
    with f.src[0]() as conn:
        conn.execute("UPDATE approved_rule_policies SET rules=rules||%s WHERE dataset_version_id=%s AND rule_version=1",
            (Jsonb([dict(column_name='city',rule_type='NOT_NULL',rule_config={'required':True})]),f.version))
    with pytest.raises(gold.ServingRejected,match='snapshot'):refresh(f)
