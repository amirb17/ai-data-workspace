
import pytest

from app.warehouse.providers.base import (
    ErrorCode,
    Policy,
    ProviderError,
)
from app.warehouse.providers.sydney_policy import SydneyPolicy


def test_sydney_policy_is_disabled_by_default():
    policy = SydneyPolicy(project="synthetic-project")

    assert policy.enabled is False
    assert policy.location == "australia-southeast1"
    assert policy.maximum_bytes == 1 << 30
    assert policy.timeout_seconds == 60


def test_existing_mumbai_policy_remains_unchanged():
    policy = Policy(project="synthetic-project")

    assert policy.location == "asia-south1"

    with pytest.raises(ProviderError) as exc:
        Policy(
            project="synthetic-project",
            location="australia-southeast1",
        )

    assert exc.value.code == ErrorCode.LOCATION_MISMATCH


def test_sydney_policy_rejects_mumbai_location():
    with pytest.raises(ProviderError) as exc:
        SydneyPolicy(
            project="synthetic-project",
            location="asia-south1",
        )

    assert exc.value.code == ErrorCode.LOCATION_MISMATCH


@pytest.mark.parametrize(
    "changes",
    [
        {"maximum_bytes": 0},
        {"maximum_bytes": (1 << 30) + 1},
        {"timeout_seconds": 301},
        {"maximum_rows": 1001},
        {"maximum_payload_bytes": (8 << 20) + 1},
        {"enabled": 1},
    ],
)
def test_sydney_policy_rejects_invalid_limits(changes):
    values = {"project": "synthetic-project"}
    values.update(changes)

    with pytest.raises(ValueError):
        SydneyPolicy(**values)


def test_sydney_policy_rejects_invalid_project():
    with pytest.raises(ProviderError) as exc:
        SydneyPolicy(project="INVALID_PROJECT")

    assert exc.value.code == ErrorCode.RESOURCE_NOT_FOUND
