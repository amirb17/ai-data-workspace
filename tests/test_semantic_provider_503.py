"""Only transient Gemini 503 is retried; failures never expose response bodies."""
from types import SimpleNamespace
from unittest.mock import patch
import pytest

from app.ai import semantic_provider as mod


class StatusFailure(Exception):
    def __init__(self, status):
        super().__init__("sensitive external error body must not be leaked")
        self.status_code = status


class FakeClient:
    def __init__(self, results, calls):
        self.results = results
        self.calls = calls
        self.interactions = self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def create(self, **kwargs):
        self.calls.append(kwargs)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def provider():
    return mod.GeminiSemanticProvider()


def test_503_then_success_retries_once(provider):
    calls = []
    results = [StatusFailure(503), SimpleNamespace(output_text='{}', model='test')]
    with patch.object(mod, 'get_ai_client', side_effect=lambda **_: FakeClient(results, calls)), \
         patch.object(mod.time, 'sleep') as sleep:
        out = provider.generate('{}')
    assert out.text == '{}'
    assert len(calls) == 2
    assert calls[0] == calls[1]
    sleep.assert_called_once_with(1)


def test_repeated_503_stops_after_three(provider):
    calls = []
    results = [StatusFailure(503) for _ in range(3)]
    with patch.object(mod, 'get_ai_client', side_effect=lambda **_: FakeClient(results, calls)), \
         patch.object(mod.time, 'sleep') as sleep:
        with pytest.raises(mod.ProviderFailure) as exc:
            provider.generate('{}')
    assert exc.value.code == 'SERVICE_UNAVAILABLE'
    assert 'sensitive' not in str(exc.value)
    assert len(calls) == 3
    assert [c.args[0] for c in sleep.call_args_list] == [1, 2]


@pytest.mark.parametrize('status,expected', [(400,'PROVIDER_ERROR'), (403,'PROVIDER_AUTH'),
                                                (404,'MODEL_UNAVAILABLE'), (429,'RATE_LIMIT')])
def test_other_http_errors_never_retry(provider, status, expected):
    calls = []
    results = [StatusFailure(status)]
    with patch.object(mod, 'get_ai_client', side_effect=lambda **_: FakeClient(results, calls)), \
         patch.object(mod.time, 'sleep') as sleep:
        with pytest.raises(mod.ProviderFailure) as exc:
            provider.generate('{}')
    assert exc.value.code == expected
    assert len(calls) == 1
    sleep.assert_not_called()


def test_empty_output_not_retried(provider):
    calls = []
    results = [SimpleNamespace(output_text='', model='test')]
    with patch.object(mod, 'get_ai_client', side_effect=lambda **_: FakeClient(results, calls)), \
         patch.object(mod.time, 'sleep') as sleep:
        with pytest.raises(mod.ProviderFailure) as exc:
            provider.generate('{}')
    assert exc.value.code == 'REFUSED'
    assert len(calls) == 1
    sleep.assert_not_called()
