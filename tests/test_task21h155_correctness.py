"""Real Decimal/Parquet/stage functions and read-only warehouse contracts."""
from decimal import Decimal, Inexact, Rounded, localcontext
import io

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.processing.exact_decimal import ExactDecimalSpec, parse_exact_decimal
from app.processing.numeric_preview import preview_exact_csv
from app.storage.versioned_artifacts import MemoryVersionedClient
from app.warehouse.gold_projection import assess_gold_projection
from app.warehouse.providers.base import ProviderError
from app.schemas.warehouse_modeling import FactConfiguration, SourceSchema
from tests.task21h153_support import CSV, rules, run_processors


@pytest.mark.parametrize('text', ['1.2300', '9007199254740993.0100', '-0.0000'])
@pytest.mark.parametrize('prec', [2, 6, 28])
def test_exact_policy_is_independent_of_decimal_context(text, prec):
    with localcontext() as ctx:
        ctx.prec = prec
        ctx.Emax, ctx.Emin = 2, -2
        ctx.traps[Inexact] = ctx.traps[Rounded] = True
        before = ctx.copy()
        result = parse_exact_decimal(text, ExactDecimalSpec(20, 4))
        assert result == Decimal(text) and result.as_tuple().exponent == -4
        assert ctx.prec == before.prec and ctx.Emax == before.Emax
        assert ctx.flags == before.flags


@pytest.mark.parametrize('text', ['1.234', '123456789012345678901.01', '1e2', 'NaN', 'Infinity'])
def test_strict_policy_rejects_rounding_overflow_or_nonlexical_values(text):
    with pytest.raises(ValueError):
        parse_exact_decimal(text, ExactDecimalSpec(20, 2))


def exact_rules():
    return [(r[0], r[1], r[2], r[3], dict(type='DECIMAL', mode='EXACT', precision=20, scale=2)
             if r[2:4] == ('amount', 'DATA_TYPE') else r[4]) for r in rules()]


def test_source_to_exact_preview_preserves_value_quarantines_and_physical_types():
    identity = MemoryVersionedClient().seed('raw/synthetic.csv', CSV)
    result = preview_exact_csv(CSV, identity, exact_rules(), ['amount'])
    assert (result.total_rows, result.valid_rows, result.rejected_rows) == (8, 2, 6)
    assert not result.publication_authorized
    frame = pd.read_parquet(io.BytesIO(result.valid_parquet))
    assert frame.loc[frame.id == '2', 'amount'].iloc[0] == Decimal('9007199254740993.01')
    for body in (result.valid_parquet, result.quarantine_parquet):
        assert pq.read_schema(io.BytesIO(body)).field('amount').type == pa.decimal128(20, 2)
    assert ('id', 'UNIQUE', 2) in result.issues
    assert '9007199254740993' not in repr(result)


@pytest.mark.parametrize('fault', ['bytes', 'legacy', 'missing', 'duplicate', 'conflict'])
def test_preview_fails_closed_on_mismatched_source_or_precision_contract(fault):
    identity = MemoryVersionedClient().seed('raw/synthetic.csv', CSV)
    selected = exact_rules()
    columns = ['amount']
    if fault == 'legacy': selected = rules()
    if fault == 'missing': columns = ['missing']
    if fault == 'duplicate': columns *= 2
    if fault == 'conflict': selected.append(selected[3])
    with pytest.raises(ValueError):
        preview_exact_csv(CSV+b'\n' if fault == 'bytes' else CSV, identity, selected, columns)


@pytest.mark.parametrize('text', ['0.001', '999999999999999999999.00'])
def test_invalid_exact_amount_is_quarantined_without_rounding(text):
    body = ('id,amount\nA,'+text+'\nB,1.20\n').encode()
    selected = [(1, 1, 'amount', 'DATA_TYPE', dict(type='DECIMAL', mode='EXACT', precision=20, scale=2))]
    identity = MemoryVersionedClient().seed('raw/synthetic.csv', body)
    result = preview_exact_csv(body, identity, selected, ['amount'])
    assert (result.valid_rows, result.rejected_rows) == (1, 1)
    assert result.issues == (('amount', 'INVALID_NUMERIC', 1),)


@pytest.mark.parametrize('limit', ['MAX_PREVIEW_BYTES', 'MAX_PREVIEW_ROWS', 'MAX_PREVIEW_COLUMNS'])
def test_numeric_preview_resource_bounds_precede_dataframe_allocation(monkeypatch, limit):
    from app.processing import numeric_preview
    monkeypatch.setattr(numeric_preview, limit, 0)
    identity = MemoryVersionedClient().seed('raw/synthetic.csv', CSV)
    monkeypatch.setattr(numeric_preview.pd, 'read_csv', lambda *a, **kw: pytest.fail('oversized preview parsed'))
    with pytest.raises(ValueError): preview_exact_csv(CSV, identity, exact_rules(), ['amount'])


def test_declared_decimal_that_cannot_fit_warehouse_physical_envelope_fails():
    body = ('id,amount\nA,'+'9'*39+'\n').encode()
    selected = [(1, 1, 'amount', 'DATA_TYPE', dict(type='DECIMAL', mode='EXACT', precision=76, scale=0))]
    identity = MemoryVersionedClient().seed('raw/synthetic.csv', body)
    with pytest.raises(ValueError, match='BigQuery numeric envelope'):
        preview_exact_csv(body, identity, selected, ['amount'])


def model():
    config = FactConfiguration(model_type='FACT', grain=dict(description='One order', identity_columns=['id']),
                               measures=['quantity'], attributes=['city'], event_time=dict(column='ordered_on', interpretation='UTC'))
    schema = SourceSchema(columns=tuple(dict(name=n, data_type=t, nullable=False) for n, t in
        [('id', 'INTEGER'), ('quantity', 'INTEGER'), ('city', 'STRING'), ('ordered_on', 'DATE')]))
    return config, schema


@pytest.fixture
def gold(monkeypatch):
    *_, result = run_processors(CSV, lambda module, profiles: monkeypatch.setattr(module, 'get_dataset_profiles', lambda _: profiles))
    return result['gold_df']


def test_real_gold_read_only_mapping_is_exact_and_does_not_authorize_admission(gold):
    config, schema = model()
    assessment = assess_gold_projection(gold, config, schema, expected_rows=2)
    assert assessment.rows == 2 and not assessment.publication_authorized
    assert ('quantity', 'INT64') in assessment.physical_types
    assert 'amount' not in assessment.columns  # Legacy float is not trusted money.
    assert all(not name.startswith('_') for name in assessment.columns)
    assert assessment == assess_gold_projection(gold, config, schema, expected_rows=2)


@pytest.mark.parametrize('fault', ['count', 'missing', 'duplicate', 'null-key', 'float', 'schema'])
def test_gold_projection_rejects_unsafe_mapping(gold, fault):
    config, schema = model()
    frame = gold.copy()
    if fault == 'missing': frame = frame.drop(columns=['city'])
    if fault == 'duplicate': frame = pd.concat([frame.iloc[:1], frame.iloc[:1]], ignore_index=True)
    if fault == 'null-key': frame['id'] = None
    if fault == 'float': frame['quantity'] = frame['quantity'].astype(float)
    if fault == 'schema': schema = SourceSchema(columns=tuple(dict(name=c.name, data_type='DECIMAL' if c.name == 'quantity' else c.data_type, nullable=c.nullable) for c in schema.columns))
    with pytest.raises((ValueError, ProviderError)):
        assess_gold_projection(frame, config, schema, expected_rows=3 if fault == 'count' else 2)


@pytest.mark.parametrize('tamper', [False, True])
def test_actual_bronze_and_exact_silver_verify_original_source_before_parquet(monkeypatch, tamper):
    import hashlib
    from app.processing import bronze_processor, silver_processor
    from app.processing_worker import io_context
    from app.config import S3_BUCKET_NAME
    from tests.task21h153_support import StageObjects
    objects = StageObjects(CSV)
    selected = exact_rules()
    with io_context.using(objects):
        bronze = bronze_processor.run_bronze_stage(41, 'raw/input.csv')
        physical = (41, 'synthetic.csv', len(CSV), hashlib.sha256(CSV).hexdigest(),
                    's3://'+S3_BUCKET_NAME+'/raw/input.csv', 'SUCCESS', bronze['schema_hash'])
        monkeypatch.setattr(silver_processor, 'get_approved_policy', lambda *a: selected)
        monkeypatch.setattr(silver_processor, 'get_physical_file_by_id', lambda _: physical)
        if tamper:
            identity = objects.identities['bronze/file_id=41/raw_text.parquet']
            original = objects.client.objects[identity.key, identity.version]
            objects.client.objects[identity.key, identity.version] = b'FAIL'+original[4:]
            with pytest.raises(ValueError, match='integrity mismatch'):
                silver_processor.process_silver(41, 9, 1, 'synthetic-only', approved_rules=selected)
        else:
            result = silver_processor.process_silver(41, 9, 1, 'synthetic-only', approved_rules=selected)
            assert (result['valid_rows'], result['rejected_rows']) == (2, 6)
            data = objects.read(result['silver_key'])
            assert pq.read_schema(io.BytesIO(data)).field('amount').type == pa.decimal128(20, 2)
            frame = pd.read_parquet(io.BytesIO(data))
            assert frame.loc[frame.id == 2, 'amount'].iloc[0] == Decimal('9007199254740993.01')
