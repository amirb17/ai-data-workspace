"""Controlled interleaving helpers shared with the recovered historical test."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import pytest
from app.services import dataset_understanding_service as service


def stale_head_during_provider(ready):
    from tests.test_dataset_understanding import call
    from tests.test_append_execution import apply
    from tests.test_incremental_foundation import second_delivery
    f, provider = ready
    entered, release = Event(), Event()
    def pause():
        entered.set()
        # Controller always releases in finally. Do not race a 30s provider
        # assertion against an unrelated real database ingestion operation.
        release.wait()
    provider.hook = pause
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(call, f, service.generate_understanding)
        try:
            assert entered.wait(20)
            assert call(f, service.read_understanding)['status'] == 'GENERATING'
            with pytest.raises(RuntimeError):
                call(f, service.generate_understanding)
            apply(f, second_delivery(f[0]))
        finally:
            release.set()
        result = future.result(30)
    assert result['status'] == 'STALE' and result['suggestion'] is None
    with f[0][0][0]() as conn:
        assert conn.execute('SELECT status,reasoning FROM semantic_suggestions').fetchone() == ('FAILED', None)
