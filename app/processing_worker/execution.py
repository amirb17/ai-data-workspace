"""Synchronous job execution boundary; no processing-stage implementation."""
from collections.abc import Callable
from contextvars import Context
from datetime import datetime
import logging
from pathlib import PurePath
from traceback import extract_tb
from uuid import UUID

from app.db import processing_job_repository as jobs

logger = logging.getLogger(__name__)


class JobRetryRequired(RuntimeError):
    """This delivery must remain unacknowledged."""


def _not_implemented(lease: jobs.JobLease) -> None:
    raise NotImplementedError('Processing job execution is not implemented')


def execute_job(job_id: str, *, executor: Callable[[jobs.JobLease], None] | None = None,
                boundary_executor=None, remaining_time_ms=None, lease_observer=None,
                claim_observer: Callable[[jobs.ClaimOutcome, datetime | None], None] | None = None) -> None:
    """Return only when this delivery may be ACKed; exceptions require retry.

    Use an independent context so inherited repository transactions cannot make
    claim/completion provisional. The executor receives only a trusted lease.
    It must finish synchronously, returning None only after its work succeeds.
    Optional trusted observation receives only outcome/retry time, never a token;
    it does not claim, complete or authorize execution independently.
    """
    if boundary_executor is not None:
        if executor is not None or not callable(remaining_time_ms):
            raise ValueError('Boundary execution requires its own executor and invocation clock')
        from app.processing_worker.boundary import ExecutionBoundary
        def controlled(lease):
            boundary = ExecutionBoundary(lease, remaining_time_ms, lease_observer=lease_observer)
            boundary.checkpoint(renew=True)
            result = boundary_executor(boundary)
            # Catch even a checkpoint failure swallowed by the injected caller.
            boundary.checkpoint()
            return result
        executor = controlled
    persisted_id = UUID(job_id)
    Context().run(_execute_job, persisted_id, _not_implemented if executor is None else executor, claim_observer)


def _execute_job(job_id: UUID, executor: Callable[[jobs.JobLease], None], claim_observer=None) -> None:
    result = jobs.claim_job_result(job_id)
    if claim_observer is not None:
        claim_observer(result.outcome, result.retry_at)
    if result.outcome in {jobs.ClaimOutcome.ALREADY_SUCCEEDED, jobs.ClaimOutcome.TERMINAL_FAILED,
                          jobs.ClaimOutcome.SOURCE_INELIGIBLE}:
        return
    if result.outcome in {jobs.ClaimOutcome.BUSY, jobs.ClaimOutcome.NOT_DUE, jobs.ClaimOutcome.NOT_FOUND}:
        # No independently durable scheduler exists; never ACK these outcomes.
        raise JobRetryRequired(result.outcome.value)
    if result.outcome != jobs.ClaimOutcome.CLAIMED or result.lease is None or result.lease.job_id != job_id:
        raise JobRetryRequired('Invalid claim result')
    lease = result.lease
    _execute_lease(job_id, lease, executor)


def execute_claimed_job(lease: jobs.JobLease, *, boundary_executor, remaining_time_ms):
    """Trusted process composition only: execute an already committed claim.

    No claim/lease substitution or new publication path. The supervisor alone
    renews; every child checkpoint still validates the current source and token.
    """
    from app.processing_worker.boundary import ExecutionBoundary
    if not isinstance(lease, jobs.JobLease) or not callable(boundary_executor) or not callable(remaining_time_ms):
        raise ValueError('Trusted claimed execution dependencies required')
    class SupervisedBoundary(ExecutionBoundary):
        def checkpoint(self, *, required_ms=0, renew=False):
            return super().checkpoint(required_ms=required_ms, renew=False)
    def controlled(current):
        boundary = SupervisedBoundary(current, remaining_time_ms)
        boundary.checkpoint()
        result = boundary_executor(boundary)
        boundary.checkpoint()
        return result
    return Context().run(_execute_lease, lease.job_id, lease, controlled)


def _execute_lease(job_id, lease, executor):
    try:
        if executor(lease) is not None:
            raise TypeError('Executor must complete synchronously and return None')
    except Exception as exc:
        # Operational diagnosis without logging user data, SQL, paths, secrets or lease tokens.
        # Only class names and vetted in-package code coordinates leave the process.
        frames = [frame for frame in extract_tb(exc.__traceback__)
                  if '/app/app/' in frame.filename.replace('\\', '/')]
        origin = frames[-1] if frames else None
        logger.warning('Processing execution refused (class=%s, module=%s, function=%s, line=%s)',
                       type(exc).__name__,
                       PurePath(origin.filename).name if origin else 'unknown',
                       origin.name if origin else 'unknown',
                       origin.lineno if origin else 0)
        try:
            jobs.finish_job(job_id, lease.lease_token, failure_code='PROCESSING_FAILED',
                            retry=True, retry_delay_seconds=30)
        except Exception:
            raise JobRetryRequired('Execution failure could not be recorded') from None
        # Even a durable terminal failure does not ACK this failing invocation.
        # A later delivery can observe TERMINAL_FAILED and acknowledge it.
        raise
    completed = jobs.finish_job(job_id, lease.lease_token)
    if completed is None or completed['status'] != 'SUCCEEDED':
        raise JobRetryRequired('Completion was not durably accepted')
