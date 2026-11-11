"""414F0-C offline result-reader IAM review; never authorizes cloud deployment."""
import pytest

from app.warehouse.query_result_iam_preflight import (
    ResultIamInventory, inspect_result_reader_iam,
)


def inventory(**overrides):
    data=dict(
        project="synthetic-project",
        location="asia-south1",
        principal="warehouse-reader@synthetic-project.iam.gserviceaccount.com",
        job_permissions=frozenset({"bigquery.jobs.get"}),
        destination_table_permissions=frozenset({"bigquery.tables.getData"}),
        other_effective_permissions=frozenset(),
        role_bindings=frozenset({"projects/synthetic-project/roles/warehouseResultReader"}),
        job_creator=False,
    )
    data.update(overrides)
    return ResultIamInventory(**data)


def test_narrow_inventory_is_still_no_go_without_live_cloud_evidence():
    result=inspect_result_reader_iam(inventory())
    assert result.classification=="NO_GO"
    assert result.findings==("LIVE_CLOUD_IAM_AND_RETENTION_EVIDENCE_REQUIRED",)
    assert result.required_job_permission=="bigquery.jobs.get"
    assert result.required_destination_permission=="bigquery.tables.getData"
    assert not result.cloud_verified and not result.deployment_authorized


@pytest.mark.parametrize("changes,expected",[
    ({"job_creator":True},"RESULT_READER_MUST_NOT_BE_JOB_CREATOR"),
    ({"job_permissions":frozenset()},"MISSING_ORIGINAL_JOB_READ"),
    ({"destination_table_permissions":frozenset()},"MISSING_DESTINATION_TABLE_DATA_ACCESS"),
    ({"job_permissions":frozenset({"bigquery.jobs.get","bigquery.jobs.create"})},"MUTATION_OR_JOB_SUBMISSION_PERMISSION"),
    ({"destination_table_permissions":frozenset({"bigquery.tables.getData","bigquery.tables.delete"})},"MUTATION_OR_JOB_SUBMISSION_PERMISSION"),
    ({"other_effective_permissions":frozenset({"bigquery.datasets.delete"})},"MUTATION_OR_JOB_SUBMISSION_PERMISSION"),
    ({"role_bindings":frozenset({"roles/owner"})},"BROAD_ROLE_BINDING_REQUIRES_REMOVAL"),
    ({"role_bindings":frozenset({"roles/bigquery.admin"})},"BROAD_ROLE_BINDING_REQUIRES_REMOVAL"),
    ({"other_effective_permissions":frozenset({"iam.serviceAccounts.getAccessToken"})},"UNREVIEWED_EFFECTIVE_PERMISSIONS"),
])
def test_overprivileged_or_incomplete_role_is_no_go(changes,expected):
    result=inspect_result_reader_iam(inventory(**changes))
    assert result.classification=="NO_GO"
    assert expected in result.findings
    assert not result.cloud_verified and not result.deployment_authorized


@pytest.mark.parametrize("changes",[
    {"project":"other"},
    {"location":"US"},
    {"principal":"someone@example.com"},
    {"principal":"warehouse-reader@synthetic-projectXiamXgserviceaccountXcom"},
    {"job_creator":1},
    {"job_permissions":{"bigquery.jobs.get"}},
    {"role_bindings":frozenset({1})},
])
def test_ill_typed_or_untrusted_inventory_is_rejected(changes):
    with pytest.raises(ValueError,match="COMPLETE_REVIEWED_IAM_INVENTORY_REQUIRED"):
        inventory(**changes)
