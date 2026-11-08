
"""Synchronous, fail-closed client for the separate verifier Lambda.

Only the candidate UUID crosses this boundary.
The worker never receives verifier database credentials.
"""

import json
from uuid import UUID


class RemoteVerificationError(RuntimeError):
    """Verification was not positively acknowledged."""


class LambdaVerifierClient:
    def __init__(self, lambda_client, function_name: str):
        if lambda_client is None:
            raise ValueError("Lambda client is required")
        if not isinstance(function_name, str) or not function_name.strip():
            raise ValueError("Verifier function name is required")

        self.client = lambda_client
        self.function_name = function_name

    def __call__(self, candidate_id):
        if not isinstance(candidate_id, UUID):
            raise TypeError("Candidate ID must be a UUID")

        payload = json.dumps(
            {"candidate_id": str(candidate_id)}
        ).encode("utf-8")

        try:
            response = self.client.invoke(
                FunctionName=self.function_name,
                InvocationType="RequestResponse",
                Payload=payload,
            )
        except Exception:
            raise RemoteVerificationError("Verifier invocation unavailable") from None

        if not isinstance(response, dict):
            raise RemoteVerificationError("Verifier returned an invalid response")

        body = response.get("Payload")
        if body is None:
            raise RemoteVerificationError(
                "Verifier returned no response payload"
            )

        try:
            try:
                raw = body.read(65537)
            finally:
                body.close()
        except Exception:
            raise RemoteVerificationError("Verifier response unavailable") from None

        if (
            response.get("StatusCode") != 200
            or response.get("FunctionError")
            or not isinstance(raw, (bytes, str))
            or len(raw) > 65536
        ):
            raise RemoteVerificationError(
                "Verifier invocation did not succeed"
            )

        try:
            result = json.loads(raw)
        except (TypeError, ValueError, UnicodeDecodeError):
            raise RemoteVerificationError(
                "Verifier returned invalid JSON"
            ) from None

        if (
            not isinstance(result, dict)
            or result.get("verified") is not True
            or result.get("candidate_id") != str(candidate_id)
        ):
            raise RemoteVerificationError(
                "Verifier did not confirm this candidate"
            )

        return None
