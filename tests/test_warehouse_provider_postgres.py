"""Real 11E transactions + real adapter with a fake cloud, disposable schemas only."""
from types import SimpleNamespace
from datetime import timedelta

import pytest
from fastapi import HTTPException

from tests.test_warehouse_foundation import pg
from tests.test_warehouse_modeling import (
    modeling_pg,
    configured,
    dimension,
    fact,
)
from tests.test_warehouse_reliability import publish_source, Objects, NOW
from tests.bigquery_fake import Client
from app.schemas.warehouse import ResourceRegistration
from app.services import warehouse_model_service as models, warehouse_resource_service as resources
from app.services.warehouse_provider_service import ProviderService
from app.services.warehouse_reliability_service import ReliabilityService
from app.storage.incremental_artifacts import IncrementalArtifacts
from app.warehouse.providers.bigquery import BigQueryTarget
from app.warehouse.providers.base import ProviderError


@pytest.fixture
def provider_pg(modeling_pg, monkeypatch):
    p = modeling_pg
    m, v, _, _ = configured(p, dimension())
    models.approve_version(p['workspace'], m, v, p['owner'])
    r = resources.register_resource(p['workspace'], m, v, p['owner'], ResourceRegistration(
        environment='development', location='asia-south1', generation=1,
        physical_reference=dict(project='synthetic-project', dataset=f'dr_dev_w{p["workspace"]}', table=f'm{m}_v{v}_g1')))
    with p['connect']() as conn:
        from pathlib import Path
        conn.execute(Path('migrations/0031_warehouse_reliability.sql').read_text())
        conn.execute(
            Path(
                'migrations/0034_warehouse_serving_materialization.sql'
            ).read_text()
        )
    monkeypatch.setenv('BIGQUERY_ENABLED', 'true')
    monkeypatch.setenv('BIGQUERY_PROJECT_ID', 'synthetic-project')
    cloud = Client()
    targets = []
    def factory(policy, binding):
        target = BigQueryTarget(policy, binding, cloud)
        targets.append(target)
        return target
    provider = ProviderService(p['workspace'], m, v, p['owner'], target_factory=factory)
    f = SimpleNamespace(pg=p, model=m, version=v, resource=r['resource_id'], mode='APPEND',
        store=IncrementalArtifacts(Objects()), frame=None, state=None,
        service=ReliabilityService(p['workspace'], m, v, p['owner']), provider=provider,
        cloud=cloud, factory=factory, targets=targets)
    provider.provision(f.resource)
    return f


def work(f, records=None, *, effective=NOW, key='first'):
    state = publish_source(f, records, effective=effective)
    manifest = f.service.register(state, f.store)
    plan = f.provider.prepare(f.resource, [manifest])
    mode = 'INITIAL_LOAD' if key == 'first' else 'INCREMENTAL'
    intent = f.service.create_intent(f.resource, mode, [manifest['manifest_id']], key, expected=plan.result)
    claim = f.service.claim(intent['operation_id'], 'provider-test', lease_seconds=900)
    return intent, claim, plan


def publish(f, intent, claim, response):
    return f.service.publish(intent['operation_id'], claim['run_id'], claim['fence'], response['reconciliation_id'])

@pytest.fixture
def fact_provider_pg(
    modeling_pg,
    monkeypatch,
):
    p = dict(modeling_pg)

    # FACT update semantics need a keyed source state so the second
    # accepted delivery can produce UPDATED for the same fact identity.
    with p['connect']() as conn:
        p['policy'] = conn.execute(
            """
            INSERT INTO dataset_load_policies(
                dataset_id,
                dataset_version_id,
                policy_version,
                load_strategy,
                business_keys,
                schema_columns,
                schema_hash,
                schema_evolution_policy,
                created_by
            )
            SELECT
                dataset_id,
                dataset_version_id,
                2,
                'UPSERT',
                '["id"]',
                schema_columns,
                schema_hash,
                schema_evolution_policy,
                created_by
            FROM dataset_load_policies
            WHERE policy_id=%s
            RETURNING policy_id
            """,
            (p['policy'],),
        ).fetchone()[0]

    configuration = fact(
        grain=dict(
            description='One fact per source id',
            identity_columns=['id'],
        ),
        measures=['line'],
        attributes=[
            'segment',
            'label',
        ],
        event_time=dict(
            column='effective',
            interpretation='UTC',
        ),
    )

    m, v, _, _ = configured(
        p,
        configuration,
        name='Physical FACT provider',
    )

    models.approve_version(
        p['workspace'],
        m,
        v,
        p['owner'],
    )

    r = resources.register_resource(
        p['workspace'],
        m,
        v,
        p['owner'],
        ResourceRegistration(
            environment='development',
            location='asia-south1',
            generation=1,
            physical_reference=dict(
                project='synthetic-project',
                dataset=(
                    f'dr_dev_w{p["workspace"]}'
                ),
                table=f'm{m}_v{v}_g1',
            ),
        ),
    )

    with p['connect']() as conn:
        from pathlib import Path

        conn.execute(
            Path(
                'migrations/0031_warehouse_reliability.sql'
            ).read_text()
        )
        conn.execute(
            Path(
                'migrations/0034_warehouse_serving_materialization.sql'
            ).read_text()
        )

    monkeypatch.setenv(
        'BIGQUERY_ENABLED',
        'true',
    )

    monkeypatch.setenv(
        'BIGQUERY_PROJECT_ID',
        'synthetic-project',
    )

    cloud = Client()
    targets = []

    def factory(policy, binding):
        target = BigQueryTarget(
            policy,
            binding,
            cloud,
        )
        targets.append(target)
        return target

    provider = ProviderService(
        p['workspace'],
        m,
        v,
        p['owner'],
        target_factory=factory,
    )

    f = SimpleNamespace(
        pg=p,
        model=m,
        version=v,
        resource=r['resource_id'],
        mode='UPSERT',
        store=IncrementalArtifacts(
            Objects()
        ),
        frame=None,
        state=None,
        service=ReliabilityService(
            p['workspace'],
            m,
            v,
            p['owner'],
        ),
        provider=provider,
        cloud=cloud,
        factory=factory,
        targets=targets,
    )

    provider.provision(
        f.resource
    )

    return f

def test_provider_to_fenced_reconciliation_publication_and_incremental(provider_pg):
    f = provider_pg
    i, c, initial = work(f)
    response = f.provider.execute(i['operation_id'], c['run_id'], c['fence'])
    assert response['publication_eligible']
    assert f.service.read_progress(f.resource)['head'] is None  # Candidate alone is invisible.
    publish(f, i, c, response)
    i2, c2, incremental = work(f, [dict(id='A', line=2, segment='Two', label='L',
        effective=NOW+timedelta(days=180), float_id=1.5)], effective=NOW+timedelta(days=180), key='second')
    assert incremental.before == initial.after
    response = f.provider.execute(i2['operation_id'], c2['run_id'], c2['fence'])
    publish(f, i2, c2, response)
    progress = f.service.read_progress(f.resource)
    assert progress['head']['revision'] == 2 and progress['checkpoint']['position'] == 2
    assert len(incremental.after[0].versions) == 2


def test_lost_response_new_fence_provider_lookup_then_publication(provider_pg):
    f = provider_pg; i, c, plan = work(f)
    f.cloud.lose_response = True
    with pytest.raises(ProviderError):
        f.provider.execute(i['operation_id'], c['run_id'], c['fence'])
    assert f.service.read_progress(f.resource)['head'] is None
    with f.pg['connect']() as conn:
        conn.execute("UPDATE warehouse_writer_leases SET expires_at=clock_timestamp()-interval '1 second'")
    newer = f.service.claim(i['operation_id'], 'recovery', lease_seconds=900)
    assert newer['fence'] > c['fence']
    response = f.provider.execute(i['operation_id'], newer['run_id'], newer['fence'])
    publish(f, i, newer, response)
    journal = next(rows for name, rows in f.cloud.rows.items() if name.endswith('_journal'))
    assert len(journal) == 1
    assert f.service.read_progress(f.resource)['checkpoint']['position'] == 1
    with pytest.raises(ValueError): f.provider.execute(i['operation_id'], c['run_id'], c['fence'])


def test_owned_resolution_precedes_any_provider_access(provider_pg):
    f = provider_pg
    before = len(f.targets)
    other = ProviderService(f.pg['workspace'], f.model, f.version, f.pg['other'], target_factory=f.factory)
    with pytest.raises(HTTPException): other.provision(f.resource)
    assert len(f.targets) == before


def test_physical_mismatch_never_advances_publication(provider_pg):
    f = provider_pg; i, c, plan = work(f)
    f.cloud.lose_response = True
    with pytest.raises(ProviderError): f.provider.execute(i['operation_id'], c['run_id'], c['fence'])
    candidate = next(rows for name, rows in f.cloud.rows.items() if '_candidate_' in name)
    candidate[0]['c0'] = 'tampered'
    with pytest.raises(ValueError): f.provider.execute(i['operation_id'], c['run_id'], c['fence'])
    assert f.service.read_progress(f.resource) == dict(head=None, checkpoint=None)
