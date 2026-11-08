"""Actual stage algorithms, real Parquet, synthetic storage; no DB/cloud proof."""
import io
import json
from decimal import Decimal

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from tests.task21h153_support import CSV, run_processors, workbook
from app.storage.versioned_artifacts import validate_csv_source


@pytest.fixture
def processed(monkeypatch):
    return run_processors(CSV, lambda module, profiles: monkeypatch.setattr(module, 'get_dataset_profiles', lambda _: profiles))


def test_actual_stage_counts_quarantine_lineage_and_gold(processed):
    objects, bronze, silver, gold = processed
    assert bronze['row_count'] == silver['total_rows'] == 8
    assert (silver['valid_rows'], silver['rejected_rows']) == (2, 6)
    assert silver['silver_df']['id'].tolist() == [2, 6]
    quarantine = pd.read_parquet(io.BytesIO(objects.read(silver['quarantine_key'])))
    reasons = [v for row in quarantine['_dq_violations'] for v in json.loads(row)]
    assert reasons.count('id:UNIQUE') == 2
    assert {'city:NOT_NULL', 'amount:NOT_NULL', 'amount:ALLOW_NEGATIVE',
            'amount:INVALID_NUMERIC', 'ordered_on:INVALID_DATETIME'} <= set(reasons)
    base = pd.read_parquet(io.BytesIO(objects.read(gold['gold_key'])))
    assert base['id'].tolist() == [2, 6]
    assert set(base['_source_file_id']) == {41}
    assert set(base['_gold_attempt_id']) == {73}
    assert base['_gold_published_at'].notna().all()
    assert not any(c.startswith('_dq_') for c in base)
    assert gold['row_count'] == 2
    marts = [a for a in gold['artifacts'] if a['artifact_type'] == 'MART']
    assert marts
    summary = next(a for a in marts if a['artifact_name'] == 'id_summary')
    mart = pd.read_parquet(io.BytesIO(objects.read(summary['storage_path'])))
    assert mart['quantity_sum'].sum() == 10
    for key, identity in objects.identities.items():
        if key.endswith('.parquet'):
            payload = objects.read(key)
            assert payload[:4] == payload[-4:] == b'PAR1'
            assert pq.read_metadata(io.BytesIO(payload)).num_rows >= 1
            assert identity.key.startswith('artifact-candidates/')


def test_lexical_precision_preserved_but_legacy_numeric_is_not_exact(processed):
    objects, _, silver, _ = processed
    lexical = pd.read_parquet(io.BytesIO(objects.read('bronze/file_id=41/raw_text.parquet')))
    assert lexical.loc[2, 'amount'] == '9007199254740993.01'
    actual = silver['silver_df'].loc[lambda f: f.id == 2, 'amount'].iloc[0]
    assert Decimal(str(actual)) != Decimal(lexical.loc[2, 'amount'])
    # Explicitly demonstrate the existing limitation; never promote this float
    # to a trusted exact warehouse amount or enable an Exact V2 gate.


def test_existing_exact_primitives_preserve_lexical_decimal_without_gates():
    from app.processing.exact_decimal import ExactDecimalSpec, parse_exact_decimal
    from app.processing.exact_parquet import write_exact_parquet, read_exact_parquet
    text = '9007199254740993.01'
    value = parse_exact_decimal(text, ExactDecimalSpec(20, 2))
    rules = [(1, 1, 'amount', 'DATA_TYPE', dict(type='DECIMAL', mode='EXACT', precision=20, scale=2))]
    frame = pd.DataFrame({'amount': [value]})
    payload, contract = write_exact_parquet(frame, rules, ['amount'])
    assert pq.read_schema(io.BytesIO(payload)).field('amount').type == pa.decimal128(20, 2)
    assert read_exact_parquet(payload, contract, rules, ['amount'], expected_columns=['amount'])['amount'].iloc[0] == Decimal(text)


def test_excel_is_not_silently_treated_as_supported_csv():
    import zipfile
    payload = workbook()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert 'xl/worksheets/sheet1.xml' in archive.namelist()
    with pytest.raises((UnicodeDecodeError, ValueError)):
        validate_csv_source(payload)


@pytest.mark.parametrize('payload', [b'id,id\n1,2\n', b'id,amount\n1\n', b'_source_file_id\n1\n', b''])
def test_real_ingress_rejects_ambiguous_or_unsafe_structure(payload):
    with pytest.raises(ValueError):
        validate_csv_source(payload)


@pytest.mark.parametrize('rows', [0, 9, 8001, True])
def test_benchmark_workloads_are_explicitly_bounded(rows):
    from tools.task21h153_benchmark import payload_for
    with pytest.raises(ValueError):
        payload_for(rows)


def test_gold_quantity_projection_through_real_fact_provider_with_fake_target(processed):
    """Explicit synthetic mapping, not automatic Gold-to-warehouse admission."""
    from datetime import datetime, time, timezone
    from app.warehouse.accepted_changes import SourcePin, event, manifest, projection, digest
    from app.warehouse.fact_planning import compile_fact
    from app.warehouse.providers import fact_physical
    from app.warehouse.providers.base import TokenState
    from tests.test_warehouse_fact_planning import context, evidence
    from tests.test_warehouse_fact_bigquery import target, batch
    objects, _, _, gold = processed
    frame = pd.read_parquet(io.BytesIO(objects.read(gold['gold_key'])))
    c = context()
    pin_value = evidence(c)['pin']
    pin_value['artifact_sha256'] = objects.identities[gold['gold_key']].sha256
    pin = SourcePin.model_validate_json(json.dumps(pin_value))
    accepted = []
    for row in frame.to_dict('records'):
        # Test mapping: warehouse measure "amount" is the exact integer
        # quantity. The lossy legacy monetary column is never promoted.
        values = dict(id=str(row['id']), line=1, amount=Decimal(int(row['quantity'])),
                      segment=row['city'], effective=datetime.combine(row['ordered_on'], time(), timezone.utc))
        accepted.append(event(pin, 'synthetic-gold-id:'+str(row['id']), 'INSERTED', values, projection(c.configuration)))
    body = manifest(pin, 1, c.model_version_id, digest(c.configuration.model_dump(mode='json')), accepted, 1)
    plan = compile_fact(c, [body])
    assert plan.result.inserted == 2
    encoded = fact_physical.rows(plan)
    assert fact_physical.states(c, encoded) == plan.after
    assert sum(dict(s.measures)['amount'] for s in plan.after) == Decimal(10)
    warehouse, _ = target()
    warehouse.provision(c)
    operation = batch(plan, warehouse)
    assert warehouse.lookup(operation) == TokenState.NOT_APPLIED
    assert warehouse.apply(operation) == plan.result
    assert warehouse.lookup(operation) == TokenState.APPLIED
    assert warehouse.reconcile(operation) == plan.result
