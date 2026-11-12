"""Regression: the live warehouse admission wrapper must not mutate ContextVar attrs."""
from unittest.mock import patch

import pytest

from app.services import warehouse_query_workflow_service as workflow


def test_admission_stage_log_uses_contextvar_without_crashing(caplog):
    with patch.object(workflow, "_admit_query_impl", side_effect=RuntimeError("synthetic")):
        with pytest.raises(RuntimeError, match="synthetic"):
            workflow.admit_query(None, 18, 1, "SELECT id FROM model_1 LIMIT 1", "synthetic", None)
    assert "WAREHOUSE_ADMISSION_FAILED stage=start class=RuntimeError sqlstate=NONE" in caplog.text
    assert workflow._admission_stage.get() == "start"


def test_admission_stage_is_reset_after_success():
    with patch.object(workflow, "_admit_query_impl", return_value={"attempt_handle":"f"*32}):
        assert workflow.admit_query(None,18,1,"SELECT id FROM model_1 LIMIT 1","synthetic",None)=={"attempt_handle":"f"*32}
    assert workflow._admission_stage.get()=="start"
