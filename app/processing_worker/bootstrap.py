
"""Fail-closed production worker bootstrap.

AWS clients, versioned storage and trusted verifier are supplied by a
separately reviewed deployment composition root. No credentials are created.
"""

import os
from collections.abc import Callable

from app.processing_worker.integrated_executor import IntegratedExecutor
from app.processing_worker.sqs_transport import SqsTransport
from app.processing_worker.worker import SerialWorker


def build_worker(
    *,
    sqs_client,
    queue_url: str,
    storage_factory: Callable,
    verifier: Callable,
    visibility_seconds: int = 300,
    job_budget_ms: int = 3_600_000,
) -> SerialWorker:
    """Construct an enabled serial worker only after deployment approval."""

    if os.getenv("DATARISE_PROCESSING_DEPLOYMENT_APPROVED") != "true":
        raise PermissionError("Processing deployment is not approved")

    if os.getenv("DATARISE_PROCESSING_READERS") != "true":
        raise PermissionError("Processing readers are not approved")

    if not queue_url or not queue_url.strip():
        raise ValueError("SQS queue URL is required")

    if not callable(storage_factory):
        raise TypeError("A versioned storage factory is required")

    if not callable(verifier):
        raise TypeError("A separately authorized verifier is required")

    if type(visibility_seconds) is not int or not 1 <= visibility_seconds <= 900:
        raise ValueError("Invalid SQS visibility timeout")

    if type(job_budget_ms) is not int or job_budget_ms <= 5000:
        raise ValueError("Invalid processing job budget")

    transport = SqsTransport(
        sqs_client,
        queue_url,
        enabled=True,
        visibility_seconds=visibility_seconds,
    )

    executor = IntegratedExecutor(
        storage_factory,
        verifier,
        enabled=True,
        isolated_test=False,
    )

    return SerialWorker(
        transport,
        boundary_executor=executor,
        job_budget_ms=job_budget_ms,
    )
