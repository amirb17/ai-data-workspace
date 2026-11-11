"""C9: cloud demo refuses live execution unless explicitly approved and scoped.

No network access. Test only preflight validation, not the provider.
"""
import sys

import pytest

from tools import warehouse_live_bigquery_three_principal as live


@pytest.mark.parametrize("arguments", [
    ["--source-table", "model_1_v1_g1"],
    ["--source-table", "model_1_v1_g1;DELETE", "--approve-billable-demo"],
    ["--source-table", "model_1_v1_g1.other", "--approve-billable-demo"],
])
def test_reject_unapproved_or_unsafe_source_table_before_auth(monkeypatch, arguments):
    monkeypatch.setattr(sys, "argv", ["cloud-demo", *arguments])
    monkeypatch.setattr(
        live.google.auth, "default",
        lambda **kwargs: pytest.fail("Cloud credential discovery forbidden"),
    )
    with pytest.raises(SystemExit):
        live.main()


def test_credential_file_override_disallowed_before_auth(monkeypatch):
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "unreviewed-key.json")
    monkeypatch.setattr(sys, "argv", [
        "cloud-demo", "--source-table", "model_1_v1_g1",
        "--approve-billable-demo",
    ])
    monkeypatch.setattr(
        live.google.auth, "default",
        lambda **kwargs: pytest.fail("Cloud credential discovery forbidden"),
    )
    with pytest.raises(SystemExit):
        live.main()


def test_invalid_resume_job_id_is_rejected_without_cloud(monkeypatch):
    monkeypatch.setattr(sys, "argv", [
        "cloud-demo", "--source-table", "m1_v1_g1_p1_r1",
        "--resume-job-id", "unreviewed_job",
    ])
    monkeypatch.setattr(
        live.google.auth, "default",
        lambda **kwargs: pytest.fail("No cloud call for invalid resume identity"),
    )
    with pytest.raises(SystemExit):
        live.main()


def test_sdk_float_timestamp_normalization_preserves_strict_billing_types():
    """Hydrated SDK timestamps can be floats; billing evidence cannot."""
    from app.warehouse.query_recovery_attestation import LookupOnlyClient, ExpectedJob
    import copy

    class Hydrated:
        _properties = {
            "statistics": {
                "creationTime": 1794430000000.0,
                "startTime": 1794430000123.0,
                "endTime": 1794430000222.0,
                "query": {"totalBytesProcessed": "1000", "totalBytesBilled": "0"},
            }
        }

    class FakeClient:
        def get_job(self, job_id, *, project, location, retry, timeout):
            return Hydrated()

    scope = ExpectedJob(
        "datarise-511817", "asia-south1",
        "dr_query_w18_r1_" + "a" * 32,
        18, 1, "b" * 64, "c" * 64,
        ("datarise-511817.dr_dev_w18.m1_v1_g1_p1_r1",),
        10 * 1024 * 1024, 0,
    )
    result = LookupOnlyClient(FakeClient()).lookup(scope)
    assert result["statistics"]["creationTime"] == "1794430000000"
    assert result["statistics"]["startTime"] == "1794430000123"
    assert result["statistics"]["endTime"] == "1794430000222"
    assert result["statistics"]["query"]["totalBytesProcessed"] == "1000"
    assert result["statistics"]["query"]["totalBytesBilled"] == "0"
