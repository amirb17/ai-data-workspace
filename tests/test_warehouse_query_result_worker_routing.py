"""The independent results worker must agree with the authenticated job pin.

No BigQuery call is made if the worker reports a mismatched attempt,
reservation, project, location or job ID.
"""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.services import warehouse_query_workflow_service as workflow


PIN={
    "reservation_id":11,
    "provider_project":"datarise-511817",
    "provider_location":"asia-south1",
    "provider_job_id":"dr_query_w18_r11_"+"a"*32,
}


@contextmanager
def scope(request,workspace):
    assert workspace==18
    yield object(),object(),object()


class Router:
    def __init__(self,override=None):
        self.override=override or {}
        self.calls=[]
    def inspect_eligibility(self,attempt):
        self.calls.append(attempt)
        return SimpleNamespace(
            **(dict(
                attempt_id=7,reservation_id=PIN["reservation_id"],
                project=PIN["provider_project"],
                location=PIN["provider_location"],
                job_id=PIN["provider_job_id"],
                classification="SETTLED_SUCCESS_RESULTS_REVIEW_REQUIRED",
                provider_dispatched=False,result_fetched=False,
            ) | self.override)
        )


@pytest.mark.parametrize("override",[
    {"attempt_id":8},{"reservation_id":12},
    {"project":"other-project"},{"location":"us-central1"},
    {"job_id":"different-job"},
    {"provider_dispatched":True},{"result_fetched":True},
    {"classification":"UNKNOWN"},
])
def test_mismatched_independent_result_authorization_fails_before_provider_read(override):
    router=Router(override)
    with patch.object(workflow,"_scope",scope),patch.object(
        workflow,"_owned",return_value={"attempt_id":7}
    ),patch.object(workflow.repo,"one",return_value=PIN),patch.object(
        workflow,"query_status",return_value={"status":"SUCCEEDED"}
    ),patch(
        "app.services.warehouse_sql_server_result_schema_service.inspect_server_result_schema"
    ) as schema:
        with pytest.raises(HTTPException) as result:
            workflow.query_results(None,18,"a"*32,SimpleNamespace(worker_router=router))
    assert result.value.status_code==409
    assert router.calls==[7]
    schema.assert_not_called()


def test_no_worker_authorization_call_when_query_is_not_settled():
    router=Router()
    with patch.object(workflow,"_scope",scope),patch.object(
        workflow,"_owned",return_value={"attempt_id":7}
    ),patch.object(workflow.repo,"one",return_value=PIN),patch.object(
        workflow,"query_status",return_value={"status":"RUNNING"}
    ):
        with pytest.raises(HTTPException) as result:
            workflow.query_results(None,18,"a"*32,SimpleNamespace(worker_router=router))
    assert result.value.status_code==409
    assert router.calls==[]
