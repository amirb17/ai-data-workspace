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
