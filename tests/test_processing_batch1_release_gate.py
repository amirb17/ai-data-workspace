"""Offline contract tests for the read-only Batch 1 release gate."""
import json

from tools.processing_batch1_release_gate import evaluate


class FakeClient:
    def __init__(self, service, calls, *, versioned=True, healthy=True):
        self.service, self.calls = service, calls
        self.versioned, self.healthy = versioned, healthy

    def get_caller_identity(self):
        self.calls.append(("sts", "get_caller_identity"))
        return {"Account": "123456789012"}

    def get_bucket_versioning(self, *, Bucket):
        self.calls.append(("s3", "get_bucket_versioning"))
        return {"Status": "Enabled" if self.versioned else "Suspended"}

    def get_queue_attributes(self, *, QueueUrl, AttributeNames):
        self.calls.append(("sqs", "get_queue_attributes"))
        return {"Attributes": {"VisibilityTimeout": "300", "SqsManagedSseEnabled": "true",
            "RedrivePolicy": json.dumps({"deadLetterTargetArn": "example", "maxReceiveCount": 5})}}

    def get_function_configuration(self, *, FunctionName):
        self.calls.append(("lambda", "get_function_configuration"))
        return {"State": "Active" if self.healthy else "Failed",
                "LastUpdateStatus": "Successful",
                "Environment": {"Variables": {"DATARISE_PROCESSING_DEPLOYMENT_APPROVED": "false",
                                               "DATARISE_PROCESSING_READERS": "false"}}}


class FakeSession:
    region_name = "ap-southeast-2"

    def __init__(self, calls, **kwargs):
        self.calls, self.kwargs = calls, kwargs

    def client(self, service):
        return FakeClient(service, self.calls, **self.kwargs)


def check(session, **kwargs):
    return evaluate(region="ap-southeast-2", bucket="test-artifacts",
                    queue="https://sqs.ap-southeast-2.amazonaws.com/123456789012/test",
                    verifier="test-verifier", session=session, **kwargs)


def test_gate_is_read_only_and_never_claims_release_acceptance(monkeypatch):
    calls = []
    monkeypatch.setenv("DATARISE_WORKER_DB_SECRET_ARN", "worker")
    monkeypatch.setenv("DATARISE_DISPATCH_DB_SECRET_ARN", "dispatcher")
    monkeypatch.setenv("DATARISE_VERIFIER_DB_SECRET_ARN", "verifier")
    result = check(FakeSession(calls), expected_account="123456789012")
    assert result["ready"] is False
    assert result["evidence"]["artifact_versioning"] is True
    assert result["evidence"]["queue_dlq_configured"] is True
    assert "LIVE_BRONZE_SILVER_GOLD_E2E_ACCEPTANCE_REQUIRED" in result["blockers"]
    assert "VERIFIER_RELEASE_DISABLED" in result["blockers"]
    assert calls == [("sts", "get_caller_identity"), ("s3", "get_bucket_versioning"),
                     ("sqs", "get_queue_attributes"), ("lambda", "get_function_configuration")]


def test_gate_detects_nonversioned_storage_and_account_mismatch():
    calls = []
    result = check(FakeSession(calls, versioned=False), expected_account="000000000000")
    assert "VERSIONED_ARTIFACT_BUCKET_REQUIRED" in result["blockers"]
    assert "AWS_ACCOUNT_MISMATCH" in result["blockers"]


def test_gate_refuses_missing_and_invalid_resource_configuration():
    result = evaluate(region="ap-southeast-2", bucket="", queue="", verifier="")
    assert result["ready"] is False
    assert result["blockers"] == ["AWS_RESOURCE_CONFIGURATION_INCOMPLETE"]
    invalid = evaluate(region="ap-southeast-2", bucket="b", queue="http://not-sqs", verifier="f")
    assert invalid["blockers"] == ["QUEUE_URL_INVALID"]
