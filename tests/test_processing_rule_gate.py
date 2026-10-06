"""Protect the existing rule-approval prerequisite discovered during Phase 5.

These checks use the existing services with mocked persistence/storage; they do
not execute uploads or processing against the application's database or S3.
"""
import io
from contextlib import nullcontext
from types import SimpleNamespace

import pandas as pd
import pytest

from app.processing import silver_processor
from app.services import business_rule_service, processing_service
from app.services import delivery_execution_service


@pytest.mark.parametrize("answer", [None, "NOT_SURE"])
def test_unapproved_rule_cannot_finalize_or_advance(monkeypatch, answer):
    monkeypatch.setattr(
        business_rule_service, "get_dataset_version_file_by_id",
        lambda _: (101, 201, 301, "AWAITING_RULES"),
    )
    monkeypatch.setattr(
        business_rule_service, "generate_rule_suggestions",
        lambda _: [{"column_name": "id", "suggested_rule_type": "NOT_NULL"}],
    )
    monkeypatch.setattr(
        business_rule_service, "get_business_rule_answers_for_dataset_version",
        lambda _: [] if answer is None else [(1, 201, "id", "NOT_NULL", answer)],
    )
    transitions = []
    monkeypatch.setattr(
        business_rule_service, "update_dataset_version_file_status",
        lambda **kwargs: transitions.append(kwargs),
    )

    result = business_rule_service.finalize_business_rules(101)

    assert result["finalized"] is False
    assert result["status"] == "AWAITING_RULES"
    assert result["missing_questions"] if answer is None else result["unresolved_questions"]
    assert transitions == []


def test_awaiting_rules_cannot_start_silver_attempt(monkeypatch):
    monkeypatch.setattr(delivery_execution_service, "delivery_lock", lambda _: nullcontext())
    monkeypatch.setattr(processing_service, "get_rule_approval_context", lambda _: (201,0,None,None,False,True))
    monkeypatch.setattr(
        processing_service, "get_dataset_version_file_by_id",
        lambda _: (101, 201, 301, "AWAITING_RULES"),
    )
    monkeypatch.setattr(processing_service, "get_physical_file_by_id", lambda _: (301,))
    monkeypatch.setattr(
        processing_service, "get_latest_successful_dq_run_for_dataset_version_file",
        lambda _: None,
    )
    attempts = []
    monkeypatch.setattr(
        processing_service, "create_processing_attempt",
        lambda **kwargs: attempts.append(kwargs),
    )

    with pytest.raises(ValueError, match="AWAITING_RULES"):
        processing_service.run_silver_processing(101, "test-bucket")

    assert attempts == []


def test_silver_without_active_rules_does_not_publish_outputs(monkeypatch):
    writes = []
    monkeypatch.setattr(
        silver_processor, "s3_client",
        SimpleNamespace(get_object=lambda **_: {"Body": io.BytesIO(b"mocked parquet")}),
    )
    monkeypatch.setattr(silver_processor.pd, "read_parquet", lambda _: pd.DataFrame({"id": [1]}))
    monkeypatch.setattr(
        silver_processor, "get_active_business_rules_for_dataset_version", lambda _: [],
    )
    monkeypatch.setattr(silver_processor, "_write_parquet_to_s3", lambda **kwargs: writes.append(kwargs))

    with pytest.raises(ValueError, match="No active business rules"):
        silver_processor.process_silver(301, 201, 0, "test-bucket")

    assert writes == []
