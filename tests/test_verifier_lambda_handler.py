
from uuid import uuid4

import pytest

from app.lambda_worker.verifier_handler import handle_verification


def test_successful_verification():
    candidate_id = uuid4()
    calls = []

    result = handle_verification(
        {"candidate_id": str(candidate_id)},
        lambda value: calls.append(value),
    )

    assert calls == [candidate_id]
    assert result == {
        "verified": True,
        "candidate_id": str(candidate_id),
    }


@pytest.mark.parametrize(
    "event",
    [
        None,
        {},
        {"candidate_id": "invalid"},
        {"candidate_id": 123},
        {"candidate_id": str(uuid4()), "extra": True},
    ],
)
def test_invalid_requests_are_rejected(event):
    calls = []

    with pytest.raises((ValueError, TypeError)):
        handle_verification(event, lambda value: calls.append(value))

    assert calls == []


def test_verification_failure_is_not_acknowledged():
    candidate_id = uuid4()

    def failing_verifier(_candidate_id):
        raise RuntimeError("Verification failed")

    with pytest.raises(RuntimeError):
        handle_verification(
            {"candidate_id": str(candidate_id)},
            failing_verifier,
        )


def test_missing_verifier_is_rejected():
    with pytest.raises(TypeError):
        handle_verification(
            {"candidate_id": str(uuid4())},
            None,
        )
