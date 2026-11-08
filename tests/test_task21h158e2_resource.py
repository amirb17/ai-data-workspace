
from copy import deepcopy

import pytest

from app.warehouse.sydney_binding import Approval, Registry
from app.warehouse.sydney_resource import resolve_sydney_resource


def approval():
    return Approval(
        "synthetic-project",
        1,
        "synthetic-loader@synthetic-project.iam.gserviceaccount.com",
        "synthetic-query@synthetic-project.iam.gserviceaccount.com",
        1,
    )


def resource():
    return {
        "resource_id": 7,
        "workspace_id": 1,
        "warehouse_model_id": 2,
        "warehouse_model_version_id": 3,
        "provider": "BIGQUERY",
        "environment": "production",
        "location": "australia-southeast1",
        "generation": 4,
        "status": "REGISTERED",
        "physical_reference": {
            "reference_version": 1,
            "project": "synthetic-project",
            "dataset": "dr_prod_w1",
            "table": "m2_v3_g4",
        },
    }


def resolve(row=None, registry=None, fingerprint=None):
    reviewed = approval()

    return resolve_sydney_resource(
        resource() if row is None else row,
        Registry([reviewed]) if registry is None else registry,
        workspace_id=1,
        model_id=2,
        version_id=3,
        expected_approval=(
            reviewed.fingerprint
            if fingerprint is None
            else fingerprint
        ),
        publication_id=5,
        revision=6,
        schema_sha256="a" * 64,
        source_fingerprint="b" * 64,
    )


def test_approved_production_resource_resolves():
    result = resolve()

    assert result["location"] == "australia-southeast1"
    assert result["physical_table"] == (
        "synthetic-project.dr_prod_w1.m2_v3_g4_p5_r6"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("environment", "development"),
        ("location", "asia-south1"),
        ("status", "PENDING"),
        ("provider", "OTHER"),
        ("workspace_id", 2),
        ("warehouse_model_id", 9),
        ("warehouse_model_version_id", 9),
        ("generation", True),
        ("resource_id", 0),
    ],
)
def test_resource_mismatch_rejected(field, value):
    row = resource()
    row[field] = value

    with pytest.raises((PermissionError, ValueError)):
        resolve(row)


@pytest.mark.parametrize(
    "field,value",
    [
        ("project", "other-project"),
        ("dataset", "dr_dev_w1"),
        ("table", "m2_v3_g5"),
        ("reference_version", 2),
    ],
)
def test_physical_reference_mismatch_rejected(field, value):
    row = deepcopy(resource())
    row["physical_reference"][field] = value

    with pytest.raises(PermissionError):
        resolve(row)


def test_unapproved_registry_rejected():
    with pytest.raises(PermissionError):
        resolve(registry=Registry())


def test_wrong_approval_fingerprint_rejected():
    with pytest.raises(PermissionError):
        resolve(fingerprint="0" * 64)
