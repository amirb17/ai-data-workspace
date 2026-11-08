
from copy import deepcopy

import pytest

from app.warehouse.sydney_binding import Approval, Registry


def setup():
    approved = Approval(
        project="synthetic-project",
        workspace_id=1,
        loader_identity=(
            "synthetic-loader@synthetic-project."
            "iam.gserviceaccount.com"
        ),
        query_identity=(
            "synthetic-query@synthetic-project."
            "iam.gserviceaccount.com"
        ),
        revision=1,
    )

    record = {
        "approval_id": 10,
        "workspace_id": 1,
        "project": approved.project,
        "loader_identity": approved.loader_identity,
        "query_identity": approved.query_identity,
        "approval_revision": approved.revision,
        "aws_region": approved.aws_region,
        "location": approved.location,
    }

    return approved, Registry([approved]), record


def test_exact_persisted_approval_accepted():
    approved, registry, record = setup()

    result = registry.verify_persisted(
        1,
        record,
        expected_fingerprint=approved.fingerprint,
    )

    assert result == {
        "approval_id": 10,
        "approval_revision": 1,
        "approval_sha256": approved.fingerprint,
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("workspace_id", 2),
        ("project", "different-project"),
        (
            "loader_identity",
            "other-loader@synthetic-project.iam.gserviceaccount.com",
        ),
        (
            "query_identity",
            "other-query@synthetic-project.iam.gserviceaccount.com",
        ),
        ("approval_revision", 2),
        ("location", "asia-south1"),
        ("approval_id", True),
    ],
)
def test_persisted_approval_drift_rejected(field, value):
    approved, registry, record = setup()
    changed = deepcopy(record)
    changed[field] = value

    with pytest.raises(PermissionError):
        registry.verify_persisted(
            1,
            changed,
            expected_fingerprint=approved.fingerprint,
        )


def test_missing_persisted_approval_rejected():
    approved, registry, _ = setup()

    with pytest.raises(PermissionError):
        registry.verify_persisted(
            1,
            None,
            expected_fingerprint=approved.fingerprint,
        )


def test_unknown_reviewed_approval_rejected():
    approved, _, record = setup()

    with pytest.raises(PermissionError):
        Registry().verify_persisted(
            1,
            record,
            expected_fingerprint=approved.fingerprint,
        )


def test_wrong_expected_fingerprint_rejected():
    _, registry, record = setup()

    with pytest.raises(PermissionError):
        registry.verify_persisted(
            1,
            record,
            expected_fingerprint="0" * 64,
        )
