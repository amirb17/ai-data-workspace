"""Cooperative stage admission only; no processing and no background heartbeat."""
from collections.abc import Callable
from contextlib import ExitStack, contextmanager

from app.db import processing_job_repository as jobs


class ExecutionStopped(RuntimeError):
    """Do not admit any further work through this execution boundary."""


class ExecutionBoundary:
    def __init__(self, lease: jobs.JobLease, remaining_time_ms: Callable[[], int], *,
                 reserve_ms=5000, renewal_seconds=120):
        if type(reserve_ms) is not int or reserve_ms < 0:
            raise ValueError('Invalid completion reserve')
        if type(renewal_seconds) is not int or not 1 <= renewal_seconds <= 900:
            raise ValueError('Invalid renewal interval')
        if reserve_ms >= renewal_seconds*1000 or not callable(remaining_time_ms):
            raise ValueError('A time budget within the renewal interval is required')
        self.lease = lease
        self.remaining_time_ms = remaining_time_ms
        self.reserve_ms = reserve_ms
        self.renewal_seconds = renewal_seconds
        self._stopped = False

    def _time(self, required_ms):
        remaining = self.remaining_time_ms()
        if type(remaining) is not int or remaining <= required_ms+self.reserve_ms:
            raise ExecutionStopped('Insufficient invocation time')
        return remaining

    def checkpoint(self, *, required_ms=0, renew=False) -> jobs.JobExecutionScope:
        """Call between bounded chunks; failure permanently closes this boundary."""
        if self._stopped:
            raise ExecutionStopped('Execution boundary is stopped')
        try:
            if type(required_ms) is not int or not 0 <= required_ms < self.renewal_seconds*1000-self.reserve_ms:
                raise ValueError('Stage budget must fit within the renewal interval')
            self._time(required_ms)
            scope = jobs.get_execution_scope(self.lease.job_id, self.lease.lease_token)
            if scope is None:
                raise ExecutionStopped('Execution ownership is unavailable')
            if renew:
                remaining = self._time(required_ms)
                renewal = min(self.renewal_seconds, (remaining-self.reserve_ms)//1000)
                if renewal < 1:
                    raise ExecutionStopped('Insufficient renewal time')
                lease = jobs.renew_job_lease(self.lease.job_id, self.lease.lease_token,
                                             lease_seconds=renewal)
                if lease is None:
                    raise ExecutionStopped('Lease renewal was rejected')
                self.lease = lease
            scope = jobs.get_execution_scope(self.lease.job_id, self.lease.lease_token,
                                             minimum_lease_ms=required_ms+self.reserve_ms)
            if scope is None:
                raise ExecutionStopped('Lease or source is no longer eligible')
            self._time(required_ms)  # Preparation/DB latency consumes the same invocation.
            return scope
        except Exception:
            self._stopped = True
            raise

    @contextmanager
    def stage(self, *, required_ms: int):
        """Acquire existing locks in processing order, then recheck admission.

        delivery_lock may perform its existing interrupted-attempt recovery.
        No stage callback runs here. Call checkpoints within long chunks too.
        """
        from app.db.dataset_repository import get_upload_processing_association
        from app.services.dataset_processing_service import dataset_lock
        from app.services.delivery_lifecycle_service import lifecycle_lock
        from app.services.delivery_execution_service import delivery_lock

        scope = self.checkpoint(required_ms=required_ms, renew=True)
        try:
            with ExitStack() as stack:
                stack.enter_context(dataset_lock(scope.workspace_id, scope.dataset_id))
                stack.enter_context(lifecycle_lock(scope.upload_id))
                association = get_upload_processing_association(scope.dataset_id, scope.file_id)
                # Revalidate before entering a lock that can recover attempts.
                self.checkpoint(required_ms=required_ms)
                if association is not None:
                    stack.enter_context(delivery_lock(association[0]))
                if self.checkpoint(required_ms=required_ms, renew=True) != scope:
                    raise ExecutionStopped('Execution scope changed')
                yield scope
                self.checkpoint()
        except BaseException:
            self._stopped = True
            raise
