"""Read-only dataset Gold to existing model/warehouse reconciliation contract.

No source event fabrication, provider execution, new readiness table or public API.
"""
from dataclasses import dataclass
from app.services.dataset_gold_serving_service import ServingRejected,row
from app.warehouse.accepted_changes import digest
from app.warehouse.gold_projection import assess_gold_projection,GoldProjectionAssessment
from app.warehouse.providers import model_adapter


@dataclass(frozen=True,slots=True)
class DatasetProjection:
    workspace_id:int
    model_id:int
    model_version_id:int
    resource_id:int
    resource_generation:int
    dataset_id:int
    state_id:int
    gold_run_id:int
    verification_id:int
    source_fingerprint:str
    configuration_sha256:str
    schema_sha256:str
    accepted_states:tuple
    assessment:GoldProjectionAssessment

    @property
    def publication_authorized(self):return False


def projection(serving,provider,resource_id,store):
    service=provider.reliability;w,_,_=service.scope
    with service.transaction() as (conn,source,config,settings):
        binding,context=provider._resolve(conn,config,settings,resource_id)
        d=source['source_dataset_id'];gold=serving.current(w,d,service.user)
        base=gold['catalog'][0]
    # Read only the declared base. Never interpret an aggregate mart as rows.
    frame=store.frame(base['storage_path'],base['sha256'])
    assessment=assess_gold_projection(frame,context.configuration,context.source_schema,expected_rows=base['row_count'])
    fingerprint=digest(config['configuration'])
    with service.transaction() as (conn,source,config,settings):
        again=serving.current(w,d,service.user)
        if again['gold_run_id']!=gold['gold_run_id'] or again['verification_id']!=gold['verification_id'] or again['source_fingerprint']!=gold['source_fingerprint'] or digest(config['configuration'])!=fingerprint:
            raise ServingRejected('Dataset/model changed during source assessment')
        if provider._resolve(conn,config,settings,resource_id)!=(binding,context):
            raise ServingRejected('Warehouse resource identity changed during assessment')
    states=tuple((item['state']['state_id'],item['state']['manifest_sha256']) for item in gold['source_contract']['history'])
    return DatasetProjection(w,service.scope[1],service.scope[2],resource_id,binding.generation,d,gold['source_state_id'],gold['gold_run_id'],gold['verification_id'],gold['source_fingerprint'],fingerprint,model_adapter.schema_hash(context),states,assessment)


def readiness(serving,provider,resource_id=None):
    """Safe derived labels over existing durable authorities; no state mutation."""
    service=provider.reliability;w,_,_=service.scope
    with service.transaction() as (conn,source,config,settings):
        try:gold=serving.current(w,source['source_dataset_id'],service.user)
        except ServingRejected:return 'NOT_READY'
        if resource_id is None:return 'GOLD_VERIFIED'
        binding,context=provider._resolve(conn,config,settings,resource_id)
        publication=row(conn,'''SELECT p.* FROM warehouse_publication_heads h JOIN warehouse_publications p USING(publication_id)
            WHERE h.resource_id=%s''',(resource_id,))
        if publication and any(v.get('dataset_id')==source['source_dataset_id'] and v.get('pin',{}).get('state_id')==gold['source_state_id'] and v.get('pin',{}).get('artifact_sha256')==gold['source_sha256'] for v in publication['source_vector']):
            materialized=row(conn,'SELECT * FROM warehouse_serving_materializations WHERE resource_id=%s AND publication_id=%s',(resource_id,publication['publication_id']))
            if materialized:
                pins=provider._serving_pins(binding,context,publication)
                provider._check_existing_serving(materialized,pins)
                return 'ANALYTICS_READY'
            return 'WAREHOUSE_RECONCILING'
        latest=row(conn,'''SELECT r.* FROM warehouse_runs r JOIN warehouse_operations o USING(operation_id)
            WHERE o.resource_id=%s AND EXISTS(SELECT 1 FROM warehouse_execution_intents i,
              jsonb_array_elements(i.body->'manifests') entry,warehouse_accepted_manifests m
              WHERE i.operation_id=o.operation_id AND m.manifest_id=entry->>'id'
              AND m.source_state_id=%s AND m.source_dataset_id=%s)
            ORDER BY r.warehouse_run_id DESC LIMIT 1''',(resource_id,gold['source_state_id'],source['source_dataset_id']))
        if latest and latest['status']=='FAILED':return 'WAREHOUSE_FAILED'
        if latest and latest['status']=='RUNNING':
            lease=row(conn,'''SELECT 1 FROM warehouse_writer_leases WHERE resource_id=%s
                AND warehouse_run_id=%s AND expires_at>clock_timestamp()''',(resource_id,latest['warehouse_run_id']))
            journal=row(conn,'SELECT stage FROM warehouse_run_journal WHERE warehouse_run_id=%s ORDER BY journal_id DESC LIMIT 1',(latest['warehouse_run_id'],))
            return 'WAREHOUSE_RECONCILING' if not lease or (journal and journal['stage'] in ('TARGET_RECEIPT','RECONCILED')) else 'WAREHOUSE_LOADING'
        return 'WAREHOUSE_PENDING'
