"""414F1 deterministic, no-network BigQuery query request validation."""
from copy import deepcopy
import hashlib
import pytest

from app.warehouse.query_job_request import (
    BigQueryJobRequest, JobRequestMismatch, build_reviewed_query_job_request,
)

SQL = "SELECT order_id FROM `synthetic-project.dr_dev_w5.orders_fact` LIMIT 5"


def policy():
    intent = "a" * 64
    return {
        "contract_version": 1,
        "project": "synthetic-project",
        "location": "asia-south1",
        "dataset": "dr_dev_w5",
        "job_id": "dr_query_w5_r7_" + "b" * 32,
        "sql_sha256": hashlib.sha256(SQL.encode()).hexdigest(),
        "compiled_intent_sha256": intent,
        "tables": ["synthetic-project.dr_dev_w5.orders_fact"],
        "labels": {"datarise": "query", "workspace": "5", "reservation": "7", "intent": intent[:63]},
        "maximum_bytes_billed": 1048576,
        "job_timeout_ms": 60000,
        "publication_sha256": "c" * 64,
        "governance_policy_sha256": "d" * 64,
        "provider_policy": {"project": "synthetic-project", "location": "asia-south1",
                            "maximum_bytes": 268435456, "timeout_ms": 60000},
        "lookup_policy": {"project": "synthetic-project", "location": "asia-south1",
                          "maximum_timeout_ms": 60000},
    }


def test_identical_reviewed_policy_produces_identical_bounded_request():
    first = build_reviewed_query_job_request(expected=policy(), compiled_sql=SQL)
    second = build_reviewed_query_job_request(expected=policy(), compiled_sql=SQL)
    assert isinstance(first, BigQueryJobRequest) and first == second
    assert first.job_id == "dr_query_w5_r7_" + "b" * 32
    assert first.location == "asia-south1"
    assert first.body["configuration"] == {
        "jobType": "QUERY", "dryRun": False, "jobTimeoutMs": "60000",
        "labels": policy()["labels"],
        "query": {
            "query": SQL, "useLegacySql": False, "useQueryCache": False,
            "priority": "INTERACTIVE", "allowLargeResults": False,
            "maximumBytesBilled": "1048576",
        },
    }
    assert first.body["jobReference"]["jobId"] == first.job_id


@pytest.mark.parametrize("key,value", [
    ("project", "foreign-project"), ("location", "US"), ("dataset", "dr_dev_w6"),
    ("job_id", "other"), ("job_timeout_ms", 0), ("job_timeout_ms", True),
    ("maximum_bytes_billed", 0), ("maximum_bytes_billed", True),
    ("maximum_bytes_billed", 2**40), ("tables", ["foreign.foo.bar"]),
    ("tables", []), ("compiled_intent_sha256", "wrong"),
])
def test_scope_or_policy_drift_is_rejected(key, value):
    data = policy()
    data[key] = value
    with pytest.raises(JobRequestMismatch):
        build_reviewed_query_job_request(expected=data, compiled_sql=SQL)


@pytest.mark.parametrize("change", [
    lambda p: p["labels"].update(workspace="6"),
    lambda p: p["labels"].update(reservation="8"),
    lambda p: p["labels"].update(intent="f" * 63),
    lambda p: p["labels"].update(unexpected="yes"),
    lambda p: p["provider_policy"].update(maximum_bytes=1),
    lambda p: p["provider_policy"].update(timeout_ms=120000),
    lambda p: p["lookup_policy"].update(location="US"),
    lambda p: p.update(extra="not-authorized"),
])
def test_nested_policy_substitution_fails_closed(change):
    data = deepcopy(policy())
    change(data)
    with pytest.raises(JobRequestMismatch):
        build_reviewed_query_job_request(expected=data, compiled_sql=SQL)


def test_sql_digest_required_and_no_provider_transport():
    with pytest.raises(JobRequestMismatch, match="FINGERPRINT"):
        build_reviewed_query_job_request(expected=policy(), compiled_sql=SQL + " -- altered")
    assert not hasattr(build_reviewed_query_job_request, "submit")
    assert not hasattr(build_reviewed_query_job_request, "send")
