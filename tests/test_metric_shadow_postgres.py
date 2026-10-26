"""Owned real publication -> serving proof -> durable METRIC governance -> fake SDK SQL."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from tests.test_warehouse_foundation import pg
from tests.test_warehouse_modeling import modeling_pg
from tests.test_warehouse_provider_postgres import fact_provider_pg
from tests.test_warehouse_serving_postgres import publication, row
from tests.metric_shadow_fixture import plan, QueryClient
from app.db.database import repository_transaction
from app.db.query_governance_repository import ReservationConflict
from app.processing.metric_shadow import ShadowOutcome
from app.processing.relationship_evidence import digest
from app.query_governance.policy import QueryPolicy, MIB
from app.query_governance.providers.bigquery import BigQueryReadProvider
from app.query_governance.types import QueryEstimate
from app.services.metric_shadow_service import MetricShadowService
from app.services.metric_warehouse_binding_service import resolve_metric_warehouse_sources
from app.services.query_governance_service import QueryGovernanceService
from app.warehouse.providers.base import Policy


@pytest.fixture
def shadow_pg(fact_provider_pg,monkeypatch):
    f=fact_provider_pg
    monkeypatch.setenv('METRIC_BIGQUERY_SHADOW_ENABLED','true')
    with f.pg['connect']() as conn:
        conn.execute(Path('migrations/0032_query_governance.sql').read_text())
    records=[row('A',1),row('B',2)]
    publication(f,records)
    definition=plan().definition
    definition['base_dataset_id']=f.pg['dataset']
    definition['formula']['nodes'][0]['column']=dict(dataset_id=f.pg['dataset'],column='line')
    pins=(dict(dataset_id=f.pg['dataset'],state_id=f.state,gold_run_id=1,
               structural_key='a'*64,semantic_key='b'*64),)
    metric=replace(plan(),workspace_id=f.pg['workspace'],definition=definition,sources=pins,
                   signature=digest([definition,pins]))
    frames={f.pg['dataset']:pd.DataFrame(records)}
    return f,metric,frames


def ready(f,metric,frames):
    receipt=f.provider.materialize_current_publication(f.resource)
    with repository_transaction() as conn:
        sources=resolve_metric_warehouse_sources(conn,metric)
    physical={i:deepcopy(f.cloud.rows[s.physical_table]) for i,s in sources.items()}
    client=QueryClient(sources,frames,physical=physical)
    client.tables={s.physical_table:deepcopy(f.cloud.tables[s.physical_table]) for s in sources.values()}
    service=MetricShadowService(f.pg['workspace'],f.pg['owner'],provider_factory=lambda:
        BigQueryReadProvider(Policy('synthetic-project',enabled=True),client))
    return service,client,receipt


def test_real_serving_gate_and_metric_accounting(shadow_pg):
    f,metric,frames=shadow_pg
    constructed=[]
    before=MetricShadowService(f.pg['workspace'],f.pg['owner'],provider_factory=lambda:constructed.append(True))
    result=before.execute(metric,frames)
    assert result.evidence.outcome==ShadowOutcome.INELIGIBLE and not constructed
    service,client,receipt=ready(f,metric,frames)
    result=service.execute(metric,frames)
    assert result.evidence.outcome==ShadowOutcome.MATCH,result.evidence
    assert result.pandas_result['value']==3
    assert f"_p{receipt['publication_id']}_r1" in next(iter(client.tables))
    with f.pg['connect']() as conn:
        assert conn.execute('SELECT consumer,status FROM query_governance_reservations').fetchall()==[('METRIC','COMPLETED')]
        assert conn.execute('SELECT count(*) FROM query_governance_usage').fetchone()[0]==1


def test_real_reservation_lost_response_recovery_and_ownership(shadow_pg):
    f,metric,frames=shadow_pg
    service,client,_=ready(f,metric,frames); client.lose_response=True
    first=service.execute(metric,frames)
    assert first.evidence.outcome==ShadowOutcome.RECOVERY_REQUIRED,first.evidence
    with f.pg['connect']() as conn:
        assert conn.execute('SELECT status FROM query_governance_reservations').fetchone()[0]=='EXECUTING'
        assert conn.execute('SELECT count(*) FROM query_governance_usage').fetchone()[0]==0
    other=QueryGovernanceService(f.pg['workspace'],f.pg['other'])
    with pytest.raises(ReservationConflict,match='WORKSPACE_NOT_OWNED'):
        other.reservation(first.evidence.reservation_id)
    repeated=service.execute(metric,frames)
    assert repeated.evidence.outcome==ShadowOutcome.RECOVERY_REQUIRED
    recovered=service.compare(metric,first.pandas_result,recovery=first.evidence)
    assert recovered.evidence.outcome==ShadowOutcome.MATCH,recovered.evidence
    assert len([c for c in client.calls if c[0]=='execute'])==1
    with f.pg['connect']() as conn:
        assert conn.execute('SELECT status FROM query_governance_reservations').fetchone()[0]=='COMPLETED'
        assert conn.execute('SELECT count(*) FROM query_governance_usage').fetchone()[0]==1


@pytest.mark.parametrize('mode',['review','hard','concurrency'])
def test_metric_shadow_uses_normal_policy_without_releasing_other_consumers(shadow_pg,mode):
    f,metric,frames=shadow_pg
    service,client,_=ready(f,metric,frames)
    policy=(QueryPolicy(auto_execute_bytes=1,hard_bytes=2) if mode=='hard' else
            QueryPolicy(auto_execute_bytes=1) if mode=='review' else
            QueryPolicy(workspace_concurrency=1,global_concurrency=1))
    governance=QueryGovernanceService(f.pg['workspace'],f.pg['owner'],policy=policy)
    if mode=='concurrency':
        governance.admit(consumer='SQL',provider='BIGQUERY',request_token='a'*64,
            intent_sha256='b'*64,estimate=QueryEstimate(10*MIB))
    service.governance_factory=lambda *args:governance
    result=service.execute(metric,frames)
    assert result.evidence.outcome==ShadowOutcome.NOT_ADMITTED,result.evidence
    assert result.pandas_result['value']==3 and not client.jobs
    with f.pg['connect']() as conn:
        assert conn.execute('SELECT consumer,status FROM query_governance_reservations').fetchall()==(
            [('SQL','ACTIVE')] if mode=='concurrency' else [])
