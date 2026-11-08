
import pytest

from app.warehouse.providers.sydney_binding import SydneyBinding


def binding(**overrides):
    values = dict(
        project="synthetic-project",
        workspace_id=1,
        model_id=2,
        version_id=3,
        resource_id=4,
        generation=1,
        approval_sha256="a" * 64,
    )
    values.update(overrides)
    return SydneyBinding(**values)


def test_sydney_binding_uses_production_names():
    result = binding()

    assert result.environment == "production"
    assert result.location == "australia-southeast1"

    assert result.serving == "dr_prod_w1"
    assert result.staging == "dr_prod_stage_w1"
    assert result.table == "m2_v3_g1"

    assert result.physical("candidate") == (
        "synthetic-project.dr_prod_stage_w1."
        "m2_v3_g1_candidate"
    )

    assert result.publication_table(5, 6) == (
        "m2_v3_g1_p5_r6"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"environment": "development"},
        {"location": "asia-south1"},
        {"workspace_id": 0},
        {"generation": 0},
        {"approval_sha256": "invalid"},
        {"project": "INVALID_PROJECT"},
    ],
)
def test_sydney_binding_rejects_invalid_scope(changes):
    with pytest.raises(ValueError):
        binding(**changes)


def test_sydney_binding_rejects_untrusted_suffix():
    with pytest.raises(ValueError):
        binding().physical("../other-dataset")


def test_sydney_binding_rejects_invalid_publication():
    with pytest.raises(ValueError):
        binding().publication_table(0, 1)
