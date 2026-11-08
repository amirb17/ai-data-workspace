
import io
import json
from uuid import uuid4

import pytest

from app.processing_worker.verifier_client import (
    LambdaVerifierClient,
    RemoteVerificationError,
)


class FakeLambda:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def invoke(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def response(payload, *, error=None, status=200):
    result = {
        "StatusCode": status,
        "Payload": io.BytesIO(json.dumps(payload).encode()),
    }
    if error:
        result["FunctionError"] = error
    return result


def test_verified_candidate_is_accepted():
    candidate_id = uuid4()
    fake = FakeLambda(
        response({
            "verified": True,
            "candidate_id": str(candidate_id),
        })
    )

    verifier = LambdaVerifierClient(fake, "trusted-verifier")

    assert verifier(candidate_id) is None

    call = fake.calls[0]
    assert call["FunctionName"] == "trusted-verifier"
    assert call["InvocationType"] == "RequestResponse"
    assert json.loads(call["Payload"]) == {
        "candidate_id": str(candidate_id)
    }


def test_verifier_rejection_is_not_accepted():
    fake = FakeLambda(response({"verified": False}))
    verifier = LambdaVerifierClient(fake, "trusted-verifier")

    with pytest.raises(RemoteVerificationError):
        verifier(uuid4())


def test_verifier_function_error_is_rejected():
    fake = FakeLambda(
        response({}, error="Unhandled")
    )
    verifier = LambdaVerifierClient(fake, "trusted-verifier")

    with pytest.raises(RemoteVerificationError):
        verifier(uuid4())


def test_wrong_candidate_is_rejected():
    fake = FakeLambda(
        response({
            "verified": True,
            "candidate_id": str(uuid4()),
        })
    )
    verifier = LambdaVerifierClient(fake, "trusted-verifier")

    with pytest.raises(RemoteVerificationError):
        verifier(uuid4())


def test_invalid_candidate_type_is_rejected():
    fake = FakeLambda(response({}))
    verifier = LambdaVerifierClient(fake, "trusted-verifier")

    with pytest.raises(TypeError):
        verifier("not-a-uuid")

    assert fake.calls == []


def test_invalid_function_name_is_rejected():
    with pytest.raises(ValueError):
        LambdaVerifierClient(FakeLambda({}), "")
