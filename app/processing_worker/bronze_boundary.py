"""Fenced Bronze preparation, deliberately separate from job completion.

The boundary supplies the current DB-validated job scope. A trusted resolver
must supply the immutable source identity from durable registration, never from
the SQS message or an arbitrary latest S3 object. This module must NOT be
installed directly as execution.execute_job(boundary_executor=...): a returned
candidate is not evidence of a committed publication or successful job.
"""
from dataclasses import dataclass
from typing import Callable
from uuid import UUID

from app.db.processing_job_repository import JobExecutionScope
from app.processing_worker.bronze_candidate import BronzeCandidate, build_bronze_candidate
from app.storage.versioned_artifacts import ObjectIdentity


@dataclass(frozen=True, slots=True)
class BronzePreparation:
    scope: JobExecutionScope
    candidate: BronzeCandidate


def prepare_bronze(boundary, source_resolver: Callable[[JobExecutionScope], ObjectIdentity],
                   client, bucket: str, candidate_id: UUID, *, stage_budget_ms=30_000) -> BronzePreparation:
    """Build candidate under the existing lock order and lease checks.

    Does not record success or mark a dataset version published. A later,
    separately implemented durable receipt/verification/publish step must
    consume the returned candidate and be the only point allowed to complete
    the job.
    """
    if type(stage_budget_ms) is not int or not 0 < stage_budget_ms < 110_000:
        raise ValueError('Invalid bounded Bronze execution budget')
    if not callable(source_resolver) or not isinstance(candidate_id, UUID) or not isinstance(bucket, str) or not bucket:
        raise ValueError('Trusted resolver, UUID and bucket are required')
    with boundary.stage(required_ms=stage_budget_ms) as scope:
        if not isinstance(scope, JobExecutionScope) or scope.operation != 'BRONZE':
            raise PermissionError('Only a validated Bronze job scope may prepare Bronze artifacts')
        source = source_resolver(scope)
        if not isinstance(source, ObjectIdentity):
            raise PermissionError('Registered immutable source identity required')
        def checkpoint():
            if boundary.checkpoint(required_ms=0) != scope:
                raise PermissionError('Bronze job authorization changed')
        checkpoint()
        candidate = build_bronze_candidate(client, bucket, source, candidate_id,
                                           checkpoint=checkpoint)
        checkpoint()
        return BronzePreparation(scope=scope, candidate=candidate)
