"""Prove the old test deadline can fail safely before the new state commits."""
import pytest
pytest.importorskip('tests.test_rule_approval_postgres', reason='Historical PostgreSQL fixtures require tools/ownership_compatibility_regression.py')
from tests.test_rule_approval_postgres import pg
from tests.test_approved_rule_reuse import deliveries
from tests.test_delivery_execution import pipeline
from tests.test_append_execution import append_pipeline, apply
from tests.test_semantic_profiles import profiled
from tests.test_dataset_understanding import ready, call
from tests.test_incremental_foundation import second_delivery
from app.services import dataset_understanding_service as service


def test_provider_barrier_expiry_before_ingestion_commit_is_failed_then_stale(ready):
    f, provider = ready
    def expired_barrier():
        raise AssertionError('Synthetic expiry of the historical provider barrier')
    provider.hook = expired_barrier
    result = call(f, service.generate_understanding)
    assert result['status'] == 'FAILED' and result['suggestion'] is None
    with f[0][0][0]() as conn:
        assert conn.execute('SELECT status,reasoning FROM semantic_suggestions').fetchone() == ('FAILED', None)
    apply(f, second_delivery(f[0]))
    result = call(f, service.read_understanding)
    assert result['status'] == 'STALE' and result['suggestion'] is None
