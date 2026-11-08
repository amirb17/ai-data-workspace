
import pytest

from app.services.warehouse_provider_service import ProviderService
from app.warehouse.providers.base import (
    ErrorCode,
    ProviderError,
)
from app.warehouse.providers.sydney_binding import SydneyBinding
from app.warehouse.providers.sydney_policy import SydneyPolicy


def binding():
    return SydneyBinding(
        project="synthetic-project",
        workspace_id=1,
        model_id=2,
        version_id=3,
        resource_id=4,
        generation=1,
        approval_sha256="a" * 64,
    )


def test_sydney_live_target_is_disabled():
    provider = ProviderService(
        1, 2, 3, {"user_id": 1}
    )

    with pytest.raises(ProviderError) as exc:
        provider._target(binding())

    assert exc.value.code == ErrorCode.DISABLED


def test_sydney_test_factory_receives_disabled_policy():
    calls = []
    synthetic_target = object()

    def factory(policy, target_binding):
        calls.append((policy, target_binding))
        return synthetic_target

    provider = ProviderService(
        1, 2, 3, {"user_id": 1},
        target_factory=factory,
    )

    result = provider._target(binding())

    assert result is synthetic_target
    assert len(calls) == 1

    policy, target_binding = calls[0]

    assert isinstance(policy, SydneyPolicy)
    assert policy.enabled is False
    assert policy.location == "australia-southeast1"
    assert target_binding == binding()
