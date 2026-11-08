
"""Security and execution tests for the trusted Lambda handler."""

from uuid import UUID

import pytest

import app.lambda_worker.verifier_handler as handler


CANDIDATE_ID = "123e4567-e89b-42d3-a456-426614174000"


def test_lambda_rejects_disabled_processing(monkeypatch):
    monkeypatch.setenv(
        "DATARISE_PROCESSING_DEPLOYMENT_APPROVED", "false"
    )
    monkeypatch.setenv(
        "DATARISE_PROCESSING_READERS", "false"
    )

    with pytest.raises(
        PermissionError,
        match="Verifier deployment is not approved",
    ):
        handler.lambda_handler(
            {"candidate_id": CANDIDATE_ID},
            None,
        )


def test_handler_rejects_invalid_candidate_ids():
    calls = []

    def fake_verifier(candidate_id):
        calls.append(candidate_id)

    invalid_events = [
        {},
        {"candidate_id": "not-a-uuid"},
        {"candidate_id": 123},
        {"candidate_id": CANDIDATE_ID.upper()},
        {"candidate_id": CANDIDATE_ID, "extra": True},
    ]

    for event in invalid_events:
        with pytest.raises((ValueError, TypeError)):
            handler.handle_verification(event, fake_verifier)

    assert calls == []


def test_handler_verifies_valid_candidate(monkeypatch):
    calls = []

    def fake_verifier(candidate_id):
        calls.append(candidate_id)

    monkeypatch.setattr(
        handler,
        "build_verifier",
        lambda: fake_verifier,
    )

    result = handler.lambda_handler(
        {"candidate_id": CANDIDATE_ID},
        None,
    )

    assert calls == [UUID(CANDIDATE_ID)]
    assert result == {
        "verified": True,
        "candidate_id": CANDIDATE_ID,
    }


def test_handler_propagates_verification_failure(monkeypatch):
    def failing_verifier(candidate_id):
        raise RuntimeError("Verification failed")

    monkeypatch.setattr(
        handler,
        "build_verifier",
        lambda: failing_verifier,
    )

    with pytest.raises(RuntimeError, match="Verification failed"):
        handler.lambda_handler(
            {"candidate_id": CANDIDATE_ID},
            None,
        )
