"""Synchronous job execution boundary; no processing-stage implementation."""
from collections.abc import Callable
from contextvars import Context
from uuid import UUID

from app.db import processing_job_repository as jobs


class JobRetryRequired(RuntimeError):
    """This delivery must remain unacknowledged."""


def _not_implemented(lease: jobs.JobLease) -> None:
    raise NotImplementedError('Processing job execution is not implemented')


def execute_job(job_id: str, *, executor: Callable[[jobs.JobLease], None] | None = None,
                boundary_executor=None, remaining_time_ms=None) -> None:
    """Return only when this delivery may be ACKed; exceptions require retry.

    Use an independent context so inherited repository transactions cannot make
    claim/completion provisional. The executor receives only a trusted lease.
    It must finish synchronously, returning None only after its work succeeds.
    """
    if boundary_executor is not None:
        if executor is not None or not callable(remaining_time_ms):
            raise ValueError('Boundary execution requires its own executor and invocation clock')
        from app.processing_worker.boundary import ExecutionBoundary
        def controlled(lease):
            boundary = ExecutionBoundary(lease, remaining_time_ms)
            boundary.checkpoint(renew=True)
            result = boundary_executor(boundary)
            # Catch even a checkpoint failure swallowed by the injected caller.
            boundary.checkpoint()
            return result
        executor = controlled
    persisted_id = UUID(job_id)
    Context().run(_execute_job, persisted_id, _not_implemented if executor is None else executor)


def _execute_job(job_id: UUID, executor: Callable[[jobs.JobLease], None]) -> None:
    result = jobs.claim_job_result(job_id)
    if result.outcome in {jobs.ClaimOutcome.ALREADY_SUCCEEDED, jobs.ClaimOutcome.TERMINAL_FAILED,
                          jobs.ClaimOutcome.SOURCE_INELIGIBLE}:
        return
    if result.outcome in {jobs.ClaimOutcome.BUSY, jobs.ClaimOutcome.NOT_DUE, jobs.ClaimOutcome.NOT_FOUND}:
        # No independently durable scheduler exists; never ACK these outcomes.
        raise JobRetryRequired(result.outcome.value)
    if result.outcome != jobs.ClaimOutcome.CLAIMED or result.lease is None or result.lease.job_id != job_id:
        raise JobRetryRequired('Invalid claim result')
    lease = result.lease
    try:
        if executor(lease) is not None:
            raise TypeError('Executor must complete synchronously and return None')
    except Exception:
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
