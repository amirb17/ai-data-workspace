"""Explicit, tiny real-cloud checks. Never part of default cloud execution."""
from dataclasses import replace
from decimal import Decimal
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from google.api_core.exceptions import NotFound
from google.cloud import bigquery

from app.warehouse.providers.base import Binding, Policy, TokenState, ProviderError
from app.warehouse.providers.bigquery import BigQueryTarget
from app.warehouse.providers.planning import compile_dimension
from app.schemas.warehouse_modeling import SourceSchema
from tests.test_warehouse_bigquery import evidence, batch
from tests.test_warehouse_scd import context, JAN, JUN

pytestmark = pytest.mark.skipif(os.getenv('RUN_BIGQUERY_INTEGRATION') != '1',
                              reason='Explicit real BigQuery integration opt-in required')


class LoseCommittedResponse:
    def __init__(self, client):
        self.client, self.lose = client, False

    def __getattr__(self, name):
        return getattr(self.client, name)

    def query(self, sql, **kwargs):
        job = self.client.query(sql, **kwargs)
        if self.lose and sql.startswith('BEGIN TRANSACTION'):
            self.lose = False
            real_result = job.result
            def result(**options):
                real_result(**options)
                raise ConnectionError('controlled committed-response loss')
            job.result = result
        return job


@pytest.fixture
def live():
    policy = Policy.from_environment()  # Requires a second, separate provider opt-in.
    client = bigquery.Client(project=policy.project, location=policy.location)
    # Entirely isolated synthetic workspace, absent before the test. Never drop
    # shared or pre-existing datasets, even if someone has assigned similar names.
    binding = Binding(policy.project, int(uuid4().hex[:14], 16), 1, 1, 1, 1)
    names = [f'{binding.project}.{name}' for name in (binding.staging, binding.serving)]
    for name in names:
        try:
            client.get_dataset(name, retry=None, timeout=policy.timeout_seconds)
        except NotFound:
            pass
        else:
            pytest.fail('Refuse a pre-existing integration dataset')
    proxy = LoseCommittedResponse(client)
    target = BigQueryTarget(policy, binding, proxy)
    complete = False
    record = dict(project=policy.project, location=policy.location, datasets=names, cleaned=[], telemetry=[])
    try:
        yield target, proxy
        complete = True
    finally:
        record['telemetry'] = target.telemetry
        record['resources_retained'] = names.copy()
        if complete:
            for name in names:
                found = client.get_dataset(name, retry=None, timeout=policy.timeout_seconds)
                assert found.labels.get('workspace') == str(binding.workspace_id)
                assert found.location == 'asia-south1' and found.labels.get('datarise') == 'warehouse'
                client.delete_dataset(name, delete_contents=True, retry=None, timeout=policy.timeout_seconds)
                record['cleaned'].append(name)
            record['resources_retained'] = []
        # Failure/UNKNOWN retains evidence for explicit investigation, never age-only cleanup.
        directory = Path('tmp'); directory.mkdir(exist_ok=True)
        (directory/f'phase11f-live-{binding.workspace_id}.json').write_text(json.dumps(record, indent=2))
        client.close()


@pytest.mark.parametrize('scd', [1, 2])
def test_real_scd_history_and_unknown_recovery(live, scd):
    target, proxy = live
    c = context(scd); before = ()
    target.provision(c)
    for n, at, city, kind in [(1, JAN, 'Pune', None), (2, JUN, 'Mumbai', None),
                               (3, JAN, 'Delhi', 'CORRECTION')]:
        m = evidence(c, n, at, city, kind=kind)
        # SourcePin workspace belongs to this isolated owned test binding.
        from app.warehouse.accepted_changes import SourcePin, event, manifest, digest
        pin = SourcePin.model_validate_json(json.dumps(m['pin'] | dict(workspace_id=target.binding.workspace_id)))
        e = event(pin, 'live-synthetic', m['events'][0]['outcome'],
                  dict(id='C001', city=city, label='One', effective=at), ('id','city','label','effective'))
        m = manifest(pin, 1, 1, digest(c.configuration.model_dump(mode='json')), [e], n)
        plan = compile_dimension(c, [m], before); b = batch(plan, target, n)
        if n == 2:
            proxy.lose = True
            with pytest.raises(ProviderError): target.apply(b)
            assert target.lookup(b) == TokenState.APPLIED
        assert target.apply(b) == plan.result
        assert target.reconcile(b) == plan.result
        before = plan.after
    if scd == 2:
        from datetime import datetime, timezone
        march = datetime(2026, 3, 1, tzinfo=timezone.utc)
        pin = pin.model_copy(update=dict(application_id=4, state_id=4, previous_state_id=3, delivery_kind='BACKFILL'))
        e = event(pin, 'live-late', 'UPDATED', dict(id='C001', city='Bangalore', label='One', effective=march),
                  ('id','city','label','effective'))
        m = manifest(pin, 1, 1, digest(c.configuration.model_dump(mode='json')), [e], 4)
        plan = compile_dimension(c, [m], before)
        assert target.apply(batch(plan, target, 4)) == plan.result
        assert len(plan.after[0].versions) == 3


def test_real_decimal_unicode_null_and_timestamp(live):
    target, _ = live
    c = context()
    schema = SourceSchema(columns=tuple(column.model_copy(update=dict(data_type='DECIMAL' if column.name=='city' else column.data_type,
        nullable=True if column.name=='label' else column.nullable)) for column in c.source_schema.columns))
    c = replace(c, source_schema=schema)
    target.provision(c)
    from app.warehouse.accepted_changes import SourcePin, event, manifest, digest
    original = evidence(context())
    pin = SourcePin.model_validate_json(json.dumps(original['pin'] | dict(workspace_id=target.binding.workspace_id)))
    e = event(pin, 'precise-live', 'INSERTED', dict(id='東京', city=Decimal('12345678901234567890.123456789012345678'),
        label=None, effective=JAN), ('id','city','label','effective'))
    m = manifest(pin, 1, 1, digest(c.configuration.model_dump(mode='json')), [e], 1)
    plan = compile_dimension(c, [m])
    assert target.apply(batch(plan, target)) == plan.result
