"""Explicit, disabled-by-default dataset Gold certification, not delivery Gold.

Reuses cumulative build/run/head and accepted-state engines. Independent verifier
composition owns its own connection/store factories. No cloud client or route.
"""
from dataclasses import dataclass
import hashlib
import io
import json

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from app.db import database,warehouse_repository as warehouse
from app.services.processing_context_service import lock_scope
from app.services.dataset_analytics_service import refresh_lock,publish_gold,load_pinned_state,verify_legacy_gold_source
from app.processing.cumulative_gold import build_cumulative_gold
from app.warehouse.accepted_changes import digest
from app.warehouse.source_evidence import extract
from app.storage.cumulative_gold_artifacts import CumulativeGoldArtifacts


class ServingRejected(ValueError):pass


class EvidenceStore(CumulativeGoldArtifacts):
    """Explicit injected client; bounded bytes/Parquet metadata before decoding."""
    def __init__(self,client):
        if client is None:raise ValueError('Injected immutable storage required')
        super().__init__(client);self.consumed=0;self.writes=0

    def _check(self,body):
        if len(body)>32*1024*1024:raise ServingRejected('Gold object bound')
        self.consumed+=len(body)
        if self.consumed>128*1024*1024:raise ServingRejected('Gold evidence I/O bound')
        if body.startswith(b'PAR1'):
            import pyarrow.parquet as pq
            try:meta=pq.ParquetFile(io.BytesIO(body)).metadata
            except Exception:raise ServingRejected('Malformed Parquet evidence') from None
            if meta.num_rows>1000 or meta.num_columns>128 or sum(meta.row_group(i).total_byte_size for i in range(meta.num_row_groups))>32*1024*1024:
                raise ServingRejected('Gold decoded-state bound')

    def read(self,key,max_bytes=None):
        body=super().read(key,max_bytes=min(max_bytes or 32*1024*1024,32*1024*1024));self._check(body);return body

    def put(self,key,body):
        self.writes+=1
        if self.writes>17:raise ServingRejected('Gold artifact bound')
        self._check(body);return super().put(key,body)


def row(conn,sql,values=()):return conn.cursor(row_factory=dict_row).execute(sql,values).fetchone()


def source_snapshot(conn,w,d,user,*,lock=False):
    if lock:lock_scope(conn,user,w,d)
    suffix=' FOR SHARE' if lock else ''
    actor=row(conn,"SELECT * FROM users WHERE user_id=%s AND owner_key=%s AND account_status='ACTIVE'"+suffix,(user['user_id'],user['owner_key']))
    head=row(conn,'''SELECT d.*,w.owner AS workspace_owner,w.status AS workspace_status FROM datasets d
        JOIN workspaces w USING(workspace_id) WHERE d.dataset_id=%s AND d.workspace_id=%s''',(d,w))
    if not actor or not head or head['owner']!=user['owner_key'] or head['workspace_owner']!=user['owner_key'] or head['status']!='ACTIVE' or head['workspace_status']!='ACTIVE':
        raise PermissionError('Current dataset ownership unavailable')
    epoch=row(conn,'SELECT revision FROM processing_dependency_epochs WHERE dataset_id=%s'+suffix,(d,))
    history=[];seen=set();state_id=head['current_state_id']
    while state_id is not None:
        if state_id in seen or len(seen)>=64:raise ServingRejected('Bounded accepted chain required')
        seen.add(state_id)
        state=row(conn,'SELECT * FROM dataset_state_versions WHERE state_id=%s AND dataset_id=%s'+suffix,(state_id,d))
        if not state or state['status']!='PUBLISHED' or state['row_count']>1000:raise ServingRejected('Published bounded state required')
        app=row(conn,'SELECT * FROM delivery_applications WHERE application_id=%s'+suffix,(state['source_application_id'],))
        if not app or app['status']!='SUCCESS' or app['result_state_id']!=state_id:raise ServingRejected('Accepted application required')
        policy=row(conn,'SELECT * FROM dataset_load_policies WHERE policy_id=%s AND dataset_id=%s'+suffix,(app['policy_id'],d))
        rules=row(conn,'SELECT rules FROM approved_rule_policies WHERE dataset_version_id=%s AND rule_version=%s'+suffix,(app['dataset_version_id'],app['applied_rule_version']))
        dq=row(conn,'SELECT * FROM data_quality_runs WHERE dq_run_id=%s'+suffix,(app['source_dq_run_id'],))
        attempt=row(conn,'SELECT * FROM processing_attempts WHERE attempt_id=%s'+suffix,(dq['attempt_id'],)) if dq else None
        delivery=row(conn,'SELECT * FROM delivery_manifests WHERE application_id=%s'+suffix,(app['application_id'],))
        upload=row(conn,'SELECT * FROM upload_requests WHERE upload_id=%s'+suffix,(app['upload_request_id'],))
        raw=row(conn,'SELECT identity FROM processing_source_objects WHERE file_id=%s'+suffix,(upload['file_id'],)) if upload else None
        from app.services.append_application_service import artifact_key
        source_key=artifact_key(dq['silver_path']) if dq and dq['silver_path'] else None
        receipt=row(conn,'''SELECT r.receipt_id,i.input_fingerprint,i.input_contract,c.manifest_identity,c.artifact_identities
            FROM processing_stage_receipts r JOIN processing_stage_intents i USING(intent_id)
            JOIN processing_artifact_candidates c ON c.candidate_id=r.candidate_id
            WHERE r.stage='SILVER' AND r.file_id=%s AND r.dataset_version_file_id=%s
            AND i.input_contract->>'upload_id'=%s AND i.input_contract->>'rule_version'=%s
            AND c.artifact_identities->'data'->>'key'=%s AND c.artifact_identities->'data'->>'sha256'=%s
            AND c.verification_report IS NOT NULL''',
            (upload['file_id'],app['dataset_version_file_id'],str(app['upload_request_id']),str(app['applied_rule_version']),source_key,dq['silver_sha256'])) if upload and delivery and dq else None
        for label,value in [('policy',policy),('rules',rules),('DQ',dq),('delivery',delivery),('upload',upload),('Raw identity',raw),('Silver attempt',attempt),('Silver receipt',receipt)]:
            if not value:raise ServingRejected('Missing trusted '+label)
        if receipt['input_contract'].get('rules')!=rules['rules']:
            raise ServingRejected('Verified rule snapshot differs')
        if not 1<=len(policy['schema_columns'])<=64 or raw['identity'].get('size',2**63)>2*1024*1024:
            raise ServingRejected('Bounded Release 1 source required')
        if (app['dataset_id']!=d or state['policy_id']!=app['policy_id']
            or policy['dataset_version_id']!=app['dataset_version_id'] or state['dataset_version_id']!=app['dataset_version_id']
            or attempt['status']!='SUCCESS' or attempt['stage']!='SILVER'
            or attempt['file_id']!=upload['file_id'] or attempt['dataset_version_file_id']!=app['dataset_version_file_id']
            or upload['dataset_id']!=d or upload['workspace_id']!=w
            or dq['dataset_version_file_id']!=app['dataset_version_file_id'] or dq['file_id']!=upload['file_id']
            or dq['rule_version']!=app['applied_rule_version'] or dq['valid_rows']!=app['valid_rows']
            or dq['total_rows']!=app['input_rows'] or dq['rejected_rows']!=app['rejected_rows']):raise ServingRejected('Pinned source/rule/DQ evidence required')
        history.append(dict(state=state,application=app,policy=policy,rules=rules['rules'],dq=dq,attempt=attempt,
            delivery=delivery,raw=raw['identity'],silver_receipt=receipt))
        state_id=state['previous_state_id']
    if not history:raise ServingRejected('Accepted dataset state required')
    contract=json.loads(json.dumps(dict(version=1,workspace_id=w,dataset_id=d,state_id=head['current_state_id'],
        dependency_epoch=epoch['revision'] if epoch else 0,history=list(reversed(history))),default=str,sort_keys=True))
    if len(json.dumps(contract).encode())>4*1024*1024:raise ServingRejected('Bounded source metadata required')
    return head,contract,history


def reconstruct(store,w,history):
    """Verify every real accepted transition; do not create accepted-event rows."""
    previous=None
    for item in reversed(history):
        state,app,policy=item['state'],item['application'],item['policy']
        verify_legacy_gold_source(store,state)  # Explicitly reject Exact V2.
        # The incremental delivery is a business-column projection/copy of
        # verified Silver, not the same physical object or checksum.
        from app.warehouse.source_evidence import checked_json
        import pandas as pd
        dm=checked_json(store,item['delivery']['manifest_key'],item['delivery']['manifest_sha256'])
        identity=item['silver_receipt']['artifact_identities']['data']
        source_bytes=store.read(identity['key'])
        if (hashlib.sha256(source_bytes).hexdigest()!=identity['sha256'] or len(source_bytes)!=identity['size']
            or dm.get('source_silver_sha256')!=identity['sha256']):raise ServingRejected('Silver-to-delivery lineage differs')
        columns=[c['name'] for c in policy['schema_columns']]
        original=pd.read_parquet(io.BytesIO(source_bytes))[columns]
        copied=store.frame(dm['silver_key'],dm['silver_sha256'])
        try:pd.testing.assert_frame_equal(original.reset_index(drop=True),copied.reset_index(drop=True),check_dtype=True,check_exact=True)
        except AssertionError:raise ServingRejected('Accepted delivery projection differs from verified Silver') from None
        from app.services.warehouse_modeling_service import rule_digest
        rule_hash=rule_digest(item['rules'])
        extract(store,state,app,policy,item['delivery'],previous,(),w,rule_hash,verify_only=True)
        previous=dict(state,snapshot_boundary_at=app['snapshot_boundary_at'])
    current=history[0]
    _,frame=load_pinned_state(store,current['state'],current['application'],current['policy'])
    return build_cumulative_gold(frame,current['policy'])


@dataclass(frozen=True,slots=True)
class Certificate:
    run_id:int
    source_fingerprint:str
    manifest_sha256:str
    catalog_sha256:str


class GoldVerifier:
    def __init__(self,connection,store_factory):self.connection,self.store_factory=connection,store_factory

    def verify(self,w,d,user,run,key,checksum,contract):
        import pandas as pd
        with self.connection() as conn:
            _,current,history=source_snapshot(conn,w,d,user)
            registered=row(conn,'SELECT * FROM dataset_gold_runs WHERE gold_run_id=%s AND dataset_id=%s',(run['gold_run_id'],d))
        pins=('gold_run_id','dataset_id','source_state_id','dataset_version_id','policy_id','source_sha256','build_version')
        if not registered or any(registered[k]!=run[k] for k in pins):raise ServingRejected('Registered immutable Gold run required')
        state=history[0]['state']
        if any(registered[k]!=value for k,value in dict(dataset_id=d,source_state_id=state['state_id'],
            dataset_version_id=state['dataset_version_id'],policy_id=state['policy_id'],
            source_sha256=state['manifest_sha256'],build_version=1).items()):
            raise ServingRejected('Gold run does not pin current accepted state')
        if registered['status']=='SUCCESS' and (registered['manifest_key'],registered['manifest_sha256'])!=(key,checksum):
            raise ServingRejected('Published Gold candidate substitution')
        if current!=contract:raise ServingRejected('Source changed before verification')
        store=self.store_factory();expected=reconstruct(store,w,history);frames={}
        catalog=store.validate(run,key,checksum,len(expected[0][0]),[c['name'] for c in history[0]['policy']['schema_columns']],frames=frames)
        if len(catalog)!=len(expected):raise ServingRejected('Gold artifact set differs')
        for index,((frame,semantic),artifact) in enumerate(zip(expected,catalog),1):
            if any(artifact.get(k)!=v for k,v in semantic.items()):raise ServingRejected('Gold semantic model differs')
            try:pd.testing.assert_frame_equal(frame.reset_index(drop=True),frames[index].reset_index(drop=True),check_dtype=True,check_exact=True)
            except AssertionError:raise ServingRejected('Gold differs from accepted dataset') from None
        with self.connection() as conn:
            _,after,_=source_snapshot(conn,w,d,user,lock=True)
            if after!=contract:raise ServingRejected('Source changed during verification')
            prior=row(conn,'SELECT * FROM dataset_gold_evidence WHERE gold_run_id=%s AND source_fingerprint=%s',(run['gold_run_id'],digest(contract)))
            if prior:
                if (prior['source_contract']!=contract or prior['manifest_key']!=key or prior['manifest_sha256']!=checksum
                    or prior['catalog_sha256']!=digest(catalog)):raise ServingRejected('Immutable verification conflict')
            else:
                conn.execute('''INSERT INTO dataset_gold_evidence(gold_run_id,contract_version,source_fingerprint,source_contract,manifest_key,manifest_sha256,catalog_sha256)
                    VALUES(%s,1,%s,%s,%s,%s,%s)''',(run['gold_run_id'],digest(contract),Jsonb(contract),key,checksum,digest(catalog)))
        return Certificate(run['gold_run_id'],digest(contract),checksum,digest(catalog)),catalog


class DatasetGoldServing:
    def __init__(self,store_factory,verifier,*,enabled=False):
        self.store_factory,self.verifier,self.enabled=store_factory,verifier,enabled

    def refresh(self,w,d,user):
        if self.enabled is not True:raise PermissionError('Dataset serving composition disabled')
        with refresh_lock(w,d):
            info={}
            try:return self._refresh_locked(w,d,user,info)
            except Exception:
                # Do not regress committed success, replace a competing head,
                # or mutate an owner's dataset after authority has moved.
                try:
                    if 'run_id' in info:
                        with database.repository_transaction() as conn:
                            lock_scope(conn,user,w,d)
                            persisted=row(conn,'SELECT status FROM dataset_gold_runs WHERE gold_run_id=%s FOR UPDATE',(info['run_id'],))
                            if persisted and persisted['status']!='SUCCESS':
                                conn.execute("UPDATE dataset_gold_runs SET status='FAILED',completed_at=clock_timestamp(),failure_code='VALIDATION_FAILED' WHERE gold_run_id=%s",(info['run_id'],))
                            conn.execute("UPDATE datasets SET state_analytics_status='FAILED' WHERE dataset_id=%s AND current_state_id=%s AND current_gold_run_id IS NOT DISTINCT FROM %s",(d,info['state_id'],info['expected_head']))
                except Exception:pass
                raise

    def _refresh_locked(self,w,d,user,info):
        with database.repository_transaction() as conn:
            head,contract,history=source_snapshot(conn,w,d,user,lock=True)
            expected_head=head['current_gold_run_id'];state=history[0]['state']
            info.update(state_id=state['state_id'],expected_head=expected_head)
            run=row(conn,'SELECT * FROM dataset_gold_runs WHERE dataset_id=%s AND source_state_id=%s AND build_version=1',(d,state['state_id']))
            if run is None:
                run=row(conn,"""INSERT INTO dataset_gold_runs(dataset_id,source_state_id,dataset_version_id,policy_id,source_sha256,status)
                    VALUES(%s,%s,%s,%s,%s,'REFRESHING') RETURNING *""",(d,state['state_id'],state['dataset_version_id'],state['policy_id'],state['manifest_sha256']))
            elif run['status']!='SUCCESS':
                conn.execute("UPDATE dataset_gold_runs SET status='REFRESHING',completed_at=NULL,failure_code=NULL WHERE gold_run_id=%s",(run['gold_run_id'],))
            info['run_id']=run['gold_run_id']
            prior=row(conn,'SELECT * FROM dataset_gold_evidence WHERE gold_run_id=%s AND source_fingerprint=%s',(run['gold_run_id'],digest(contract)))
        store=self.store_factory()
        if prior:key,checksum=prior['manifest_key'],prior['manifest_sha256']
        elif run['status']=='SUCCESS':key,checksum=run['manifest_key'],run['manifest_sha256']
        else:
            artifacts=reconstruct(store,w,history)
            if len(artifacts)>16:raise ServingRejected('Bounded Gold model required')
            key,checksum,_=store.build(run,artifacts)
        certificate,catalog=self.verifier.verify(w,d,user,run,key,checksum,contract)
        if not isinstance(certificate,Certificate) or certificate!=Certificate(run['gold_run_id'],digest(contract),checksum,digest(catalog)):
            raise ServingRejected('Independent verification required')
        with database.repository_transaction() as conn:
            latest,pinned,_=source_snapshot(conn,w,d,user,lock=True)
            if pinned!=contract or latest['current_gold_run_id']!=expected_head:raise ServingRejected('Expected source/Gold head CAS failed')
            prior=row(conn,'SELECT * FROM dataset_gold_evidence WHERE gold_run_id=%s AND source_fingerprint=%s',(run['gold_run_id'],digest(contract)))
            if not prior or (prior['source_contract']!=contract or prior['manifest_key']!=key or prior['manifest_sha256']!=checksum
                or prior['catalog_sha256']!=digest(catalog)):raise ServingRejected('Persisted independent serving evidence required')
            if run['status']!='SUCCESS':publish_gold(conn,d,run,key,checksum,catalog)
            else:conn.execute("UPDATE datasets SET current_gold_run_id=%s,state_analytics_status='FRESH' WHERE dataset_id=%s",(run['gold_run_id'],d))
        return run['gold_run_id']

    def current(self,w,d,user):
        if self.enabled is not True:raise PermissionError('Dataset serving composition disabled')
        with database.repository_savepoint() as conn:
            head,contract,_=source_snapshot(conn,w,d,user,lock=True)
            evidence=row(conn,'''SELECT e.*,e.manifest_sha256 AS verified_manifest_sha256,g.* FROM dataset_gold_evidence e JOIN dataset_gold_runs g USING(gold_run_id)
                WHERE e.gold_run_id=%s AND e.source_fingerprint=%s AND g.status='SUCCESS' ''',(head['current_gold_run_id'],digest(contract)))
            if (not evidence or head['state_analytics_status']!='FRESH' or evidence['source_contract']!=contract
                or evidence['source_fingerprint']!=digest(contract) or evidence['verified_manifest_sha256']!=evidence['manifest_sha256']
                or evidence['catalog_sha256']!=digest(evidence['catalog'])):
                raise ServingRejected('Current independently verified dataset Gold required')
            return evidence
