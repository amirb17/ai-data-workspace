"""Offline-composable one-job contract. No SDK/bootstrap, polling or heartbeat.

Only a reviewed caller can inject approval and an existing IntegratedExecutor.
The default CLI is diagnostic/fail-closed, not a production Batch entrypoint.
"""
import json
import math
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum, StrEnum
from threading import Event
from time import monotonic
from uuid import UUID


class Stage(StrEnum):
    BRONZE = 'BRONZE'
    SILVER = 'SILVER'
    GOLD = 'GOLD'


class Outcome(StrEnum):
    SUCCEEDED = 'SUCCEEDED'
    ALREADY_SUCCEEDED = 'ALREADY_SUCCEEDED'
    TERMINAL_FAILED = 'TERMINAL_FAILED'
    SOURCE_INELIGIBLE = 'SOURCE_INELIGIBLE'
    BUSY = 'BUSY'
    NOT_DUE = 'NOT_DUE'
    NOT_FOUND = 'NOT_FOUND'
    INVALID_INVOCATION = 'INVALID_INVOCATION'
    DISABLED = 'DISABLED'
    UNSUPPORTED_STAGE = 'UNSUPPORTED_STAGE'
    CANCELLED = 'CANCELLED'
    DEADLINE_EXCEEDED = 'DEADLINE_EXCEEDED'
    OWNERSHIP_UNAVAILABLE = 'OWNERSHIP_UNAVAILABLE'
    RETRY_REQUIRED = 'RETRY_REQUIRED'


class ExitCode(IntEnum):
    OK = 0
    TERMINAL_FAILED = 20
    SOURCE_INELIGIBLE = 21
    INVALID_INVOCATION = 64
    UNSUPPORTED_STAGE = 65
    NOT_FOUND = 66
    RETRY = 75
    DISABLED = 78


EXITS = {Outcome.SUCCEEDED: ExitCode.OK, Outcome.ALREADY_SUCCEEDED: ExitCode.OK,
         Outcome.TERMINAL_FAILED: ExitCode.TERMINAL_FAILED,
         Outcome.SOURCE_INELIGIBLE: ExitCode.SOURCE_INELIGIBLE,
         Outcome.INVALID_INVOCATION: ExitCode.INVALID_INVOCATION,
         Outcome.UNSUPPORTED_STAGE: ExitCode.UNSUPPORTED_STAGE,
         Outcome.NOT_FOUND: ExitCode.NOT_FOUND, Outcome.DISABLED: ExitCode.DISABLED}


def _uuid(value):
    if isinstance(value, UUID): result = value
    elif isinstance(value, str):
        if len(value) != 36: raise ValueError('Canonical UUID required')
        result = UUID(value)
        if str(result) != value.lower(): raise ValueError('Noncanonical UUID')
    else: raise ValueError('UUID required')
    if result.int == 0: raise ValueError('Nonzero UUID required')
    return result


@dataclass(frozen=True, slots=True)
class Invocation:
    job_id: UUID
    submission_id: UUID | None = None

    def __post_init__(self):
        object.__setattr__(self, 'job_id', _uuid(self.job_id))
        if self.submission_id is not None:
            object.__setattr__(self, 'submission_id', _uuid(self.submission_id))


@dataclass(frozen=True, slots=True)
class Result:
    outcome: Outcome
    job_id: UUID | None = None
    submission_id: UUID | None = None
    stage: Stage | None = None
    retry_at: datetime | None = None

    @property
    def exit_code(self): return EXITS.get(self.outcome, ExitCode.RETRY)

    @property
    def completion_observed(self):
        return self.outcome in (Outcome.SUCCEEDED, Outcome.ALREADY_SUCCEEDED)

    def as_dict(self):
        # Explicit allowlist. Never serialize an exception, lease or source DTO.
        return dict(version=1, outcome=self.outcome.value, exit_code=int(self.exit_code),
                    job_id=str(self.job_id) if self.job_id else None,
                    submission_id=str(self.submission_id) if self.submission_id else None,
                    stage=self.stage.value if self.stage else None,
                    retry_at=self.retry_at.isoformat() if self.retry_at else None,
                    completion_observed=self.completion_observed)


class _UnsupportedStage(Exception): pass


def run_once(job_id, submission_id=None, *, executor=None,
             runtime_approval: Callable[[], bool] | None = None,
             budget_seconds=90, stop: Event | None = None,
             clock: Callable[[], float] = monotonic) -> Result:
    """At most one existing authorized stage; all ordinary failures are safe results.

    runtime_approval is trusted server composition, not a request flag. It must
    explicitly return True after its security preflight. No live composition is
    provided here. Injected executor/resources remain owned by the caller.
    BaseException (process crash, KeyboardInterrupt/SystemExit) is not swallowed.
    """
    try: invocation = Invocation(job_id, submission_id)
    except (ValueError, TypeError, AttributeError): return Result(Outcome.INVALID_INVOCATION)
    def result(outcome, stage=None, retry_at=None):
        return Result(outcome, invocation.job_id, invocation.submission_id, stage, retry_at)
    if (not callable(executor) or not callable(runtime_approval) or not callable(clock)
        or type(budget_seconds) is not int or not 6 <= budget_seconds <= 900
        or (stop is not None and not isinstance(stop, Event))):
        return result(Outcome.DISABLED)
    cancellation = stop if stop is not None else Event()
    try:
        started = clock()
        if type(started) not in (int, float) or not math.isfinite(started):
            return result(Outcome.DISABLED)
        deadline = started + budget_seconds
        last = started
        def remaining():
            nonlocal last
            now = clock()
            if type(now) not in (int, float) or not math.isfinite(now) or now < last:
                raise ValueError('Invalid monotonic clock')
            last = now
            return 0 if cancellation.is_set() else max(0, int((deadline-now)*1000))
        if cancellation.is_set(): return result(Outcome.CANCELLED)
        if runtime_approval() is not True: return result(Outcome.DISABLED)
    except PermissionError: return result(Outcome.DISABLED)
    except Exception: return result(Outcome.RETRY_REQUIRED)
    # Lazy imports: missing approval/injection never imports app configuration,
    # resolves credentials, opens a connection or constructs a cloud client.
    from app.processing_worker import execution
    from app.processing_worker.boundary import ExecutionStopped
    observed = None
    retry_at = None
    stage = None
    def observe(outcome, retry):
        nonlocal observed, retry_at
        observed = Outcome(outcome.value) if outcome.value != 'CLAIMED' else 'CLAIMED'
        retry_at = retry
    def controlled(boundary):
        nonlocal stage
        scope = boundary.checkpoint()
        try: stage = Stage(scope.operation)
        except ValueError: raise _UnsupportedStage() from None
        return executor(boundary)
    try:
        if remaining() <= 5000:
            return result(Outcome.CANCELLED if cancellation.is_set() else Outcome.DEADLINE_EXCEEDED)
        execution.execute_job(str(invocation.job_id), boundary_executor=controlled,
                              remaining_time_ms=remaining, claim_observer=observe)
        if observed == 'CLAIMED': return result(Outcome.SUCCEEDED, stage)
        if observed in (Outcome.ALREADY_SUCCEEDED, Outcome.TERMINAL_FAILED, Outcome.SOURCE_INELIGIBLE):
            return result(observed)
        return result(Outcome.RETRY_REQUIRED)
    except _UnsupportedStage: return result(Outcome.UNSUPPORTED_STAGE)
    except Exception as error:
        if observed in (Outcome.BUSY, Outcome.NOT_DUE, Outcome.NOT_FOUND):
            return result(observed, retry_at=retry_at)
        if cancellation.is_set(): return result(Outcome.CANCELLED, stage)
        try:
            if remaining() <= 5000: return result(Outcome.DEADLINE_EXCEEDED, stage)
        except Exception: return result(Outcome.RETRY_REQUIRED, stage)
        if isinstance(error, ExecutionStopped): return result(Outcome.OWNERSHIP_UNAVAILABLE, stage)
        return result(Outcome.RETRY_REQUIRED, stage)


def main(argv=None) -> int:
    """Explicit diagnostic CLI only: UUID [submission UUID], disabled by default."""
    values = sys.argv[1:] if argv is None else argv
    result = run_once(*values) if isinstance(values, list) and 1 <= len(values) <= 2 else Result(Outcome.INVALID_INVOCATION)
    print(json.dumps(result.as_dict(), sort_keys=True))
    return int(result.exit_code)


if __name__ == '__main__':
    raise SystemExit(main())
