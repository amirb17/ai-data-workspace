"""Read-only result error logging never leaks arbitrary provider exception text."""
from unittest.mock import patch

import pytest

from app.services import warehouse_query_workflow_service as workflow


def test_result_failure_reports_stage_and_safe_code_without_raw_exception(caplog):
    with patch.object(workflow, "_query_results_impl",
                      side_effect=RuntimeError("secret bearer token in exception")):
        with pytest.raises(RuntimeError):
            workflow.query_results(None, 18, "f" * 32, None)
    assert "WAREHOUSE_RESULT_READ_FAILED stage=start class=RuntimeError code=OTHER" in caplog.text
    assert "secret bearer token" not in caplog.text
    assert workflow._result_stage.get() == "start"


def test_result_success_keeps_original_return_value():
    expected={"columns":["id"],"rows":[["A"]]}
    with patch.object(workflow,"_query_results_impl",return_value=expected):
        assert workflow.query_results(None,18,"f"*32,None) is expected
    assert workflow._result_stage.get()=="start"
