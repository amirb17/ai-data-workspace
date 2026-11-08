"""Real local warehouse authority/rollback; real Gold, fake physical target."""
import io

import pandas as pd
import pytest
from fastapi import HTTPException

from tests.test_warehouse_foundation import pg
from tests.test_warehouse_modeling import modeling_pg
from tests.test_warehouse_provider_postgres import provider_pg, work, publish
from app.db import warehouse_reliability_repository as db
from app.processing import gold_processor
from app.processing.dataset_profiler import profile_dataframe
from app.processing_worker import io_context
from app.warehouse.accepted_changes import projection
from app.warehouse.gold_projection import assess_gold_projection
from app.warehouse.reliability import ProtocolError
from tests.task21h153_support import StageObjects


def test_real_gold_projection_then_existing_authorized_load_atomic_rollback_and_retry(provider_pg, monkeypatch):
    f = provider_pg
    intent, claim, plan = work(f)
    objects = StageObjects(b'synthetic-only')
    output = io.BytesIO()
    f.frame.to_parquet(output, index=False)
    objects.identities['accepted.parquet'] = objects.client.seed('accepted.parquet', output.getvalue())
    profiles = profile_dataframe(f.frame[list(projection(plan.context.configuration))])
    tuples = [(i, 1, p['column_name'], p['inferred_type'], p['null_count'], p['distinct_count'],
        p['duplicate_count'], p['min_value'], p['max_value'], p['negative_count']) for i,p in enumerate(profiles)]
    monkeypatch.setattr(gold_processor, 'get_dataset_profiles', lambda _: tuples)
    with io_context.using(objects):
        gold = gold_processor.process_gold(1, 1, 1, 'synthetic-only', 'accepted.parquet', 1)
    assessment = assess_gold_projection(gold['gold_df'], plan.context.configuration,
                                        plan.context.source_schema, expected_rows=len(f.frame))
    assert assessment.rows == 1 and not assessment.publication_authorized
    assert f.service.read_progress(f.resource) == dict(head=None, checkpoint=None)
    response = f.provider.execute(intent['operation_id'], claim['run_id'], claim['fence'])
    assert response['publication_eligible']
    original = db.journal
    def crash(conn, run, fence, stage, evidence):
        if stage == 'PUBLISHED': raise RuntimeError('Synthetic interruption after pointer writes')
        return original(conn, run, fence, stage, evidence)
    with monkeypatch.context() as patch:
        patch.setattr(db, 'journal', crash)
        with pytest.raises(RuntimeError, match='pointer writes'):
            publish(f, intent, claim, response)
    assert f.service.read_progress(f.resource) == dict(head=None, checkpoint=None)
    with f.pg['connect']() as conn:
        assert conn.execute('SELECT count(*) FROM warehouse_publications').fetchone()[0] == 0
    publish(f, intent, claim, response)
    assert f.service.read_progress(f.resource)['head']['revision'] == 1
    journal = next(rows for name, rows in f.cloud.rows.items() if name.endswith('_journal'))
    assert len(journal) == 1
    # Target and SQL recovery remain distinct: no second provider mutation.
    assert f.service.register(f.state, f.store)['pin']['dataset_id'] == f.pg['dataset']
    with pytest.raises(ValueError):
        publish(f, intent, claim, response)


@pytest.mark.parametrize('fault', ['unknown-state', 'row-content', 'schema', 'row-count'])
def test_source_identity_and_artifact_drift_do_not_publish(provider_pg, fault):
    from tests.test_warehouse_reliability import publish_source
    f = provider_pg
    state = publish_source(f)
    if fault == 'unknown-state':
        with pytest.raises(HTTPException): f.service.register(-1, f.store)
    else:
        # Physical bytes no longer match the DB-pinned checksum. A schema/row
        # change must fail before any accepted manifest or provider is created.
        with f.pg['connect']() as conn:
            key = conn.execute('SELECT manifest_key FROM dataset_state_versions WHERE state_id=%s', (state,)).fetchone()[0]
        import json
        manifest = json.loads(f.store.client.data[key])
        data = f.frame.copy()
        if fault == 'schema': data = data.drop(columns=['id'])
        elif fault == 'row-count': data = pd.concat([data, data], ignore_index=True)
        else: data['segment'] = 'changed'
        f.store.client.data[manifest['data_key']] = f.store.parquet(data)
        with pytest.raises(ProtocolError): f.service.register(state, f.store)
    assert f.service.read_progress(f.resource) == dict(head=None, checkpoint=None)
    with f.pg['connect']() as conn:
        assert conn.execute('SELECT count(*) FROM warehouse_accepted_manifests').fetchone()[0] == 0


def test_repeated_source_registration_preserves_source_vector(provider_pg):
    from tests.test_warehouse_reliability import publish_source
    f = provider_pg
    state = publish_source(f)
    first = f.service.register(state, f.store)
    assert f.service.register(state, f.store) == first
    assert first['pin']['state_id'] == state
    assert first['pin']['dataset_id'] == f.pg['dataset']
    assert first['pin']['dataset_version_id'] == f.pg['source_version']
    with f.pg['connect']() as conn:
        assert conn.execute('SELECT count(*) FROM warehouse_accepted_manifests').fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM warehouse_outbox').fetchone()[0] == 1
