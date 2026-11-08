"""Offline-composable Batch protocol; no SDK, cloud bootstrap or stage executor.

Provider observations must come from a trusted, scoped lookup, never an event
body or user-supplied provider ID. A future real provider adapter needs review.
"""
import hashlib
import json
import re
from collections.abc import Callable
from contextvars import Context
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Runtime:
    stage: str
    definition: str
    queue: str
    image_digest: str
    task_role: str
    execution_role: str

    def __post_init__(self):
        if self.stage not in {'BRONZE', 'SILVER', 'GOLD'}:
            raise ValueError('Unsupported bounded stage')
        for name in ('definition', 'queue', 'task_role', 'execution_role'):
            value = getattr(self, name)
            if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_:/.-]{1,256}', value):
                raise ValueError('Invalid trusted runtime identity')
        if not re.search(r':[1-9][0-9]*$', self.definition):
            raise ValueError('Immutable definition revision required')
        if not re.fullmatch(r'sha256:[a-f0-9]{64}', self.image_digest):
            raise ValueError('Pinned image digest required')

    @property
    def fingerprint(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


@dataclass(frozen=True, slots=True, repr=False)
class Submission:
    submission_id: UUID
    job_id: UUID
    claim_token: UUID = field(repr=False)
    runtime: Runtime
    fingerprint: str


@dataclass(frozen=True, slots=True)
class Observation:
    submission_id: UUID
    fingerprint: str
    provider_id: str
    status: str

    def __post_init__(self):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', self.provider_id) or self.status not in {'ACTIVE', 'SUCCEEDED', 'FAILED'}:
            raise ValueError('Invalid safe provider observation')


class DispatchOutcome(StrEnum):
    DISABLED = 'DISABLED'
    IDLE = 'IDLE'
    SUBMITTED = 'SUBMITTED'
    UNCERTAIN = 'UNCERTAIN'
    NOT_SUBMITTED = 'NOT_SUBMITTED'


class DefinitelyNotSubmitted(Exception):
    """Trusted adapter proves no request was accepted; never infer from timeout."""


def _approved(approval):
    # A future composition must perform role/flag checks, not accept a body flag.
    return callable(approval) and approval() is True


def dispatch_once(repository, provider, *, approval: Callable[[], bool] | None = None):
    """One committed intent, at most one injected call, no queue/polling loop.

    Database failures propagate and are not success. An uncertain response may
    strand work for review, but can never silently create unbounded submissions.
    BaseException crash leaves CLAIMED to age into UNCERTAIN, not a resend.
    """
    if not _approved(approval):
        return DispatchOutcome.DISABLED
    def operation():
        intent = repository.claim()
        if intent is None:
            return DispatchOutcome.IDLE
        try:
            observed = provider.submit(intent)
            if not isinstance(observed, Observation) or observed.submission_id != intent.submission_id or observed.fingerprint != intent.fingerprint:
                raise ValueError('Submission response correlation unavailable')
        except DefinitelyNotSubmitted:
            accepted = repository.response(intent, None, 'RETIRED')
            return DispatchOutcome.NOT_SUBMITTED if accepted else DispatchOutcome.UNCERTAIN
        except Exception:
            repository.response(intent, None, 'UNCERTAIN')
            return DispatchOutcome.UNCERTAIN
        if not repository.response(intent, observed.provider_id, 'OBSERVED'):
            # Late response: reconciliation must rediscover it; do not resend.
            return DispatchOutcome.UNCERTAIN
        return DispatchOutcome.SUBMITTED
    return Context().run(operation)


def reconcile_once(repository, provider, submission_id, *, approval=None):
    """Event notifications are wake-up hints only; re-read SQL then scoped provider.

    No notification/status is trusted as completion, and no publication occurs.
    Lost events are handled by a future bounded scan of repository.due().
    """
    if not _approved(approval):
        return 'DISABLED'
    def operation():
        intent = repository.get(UUID(str(submission_id)))
        if intent is None:
            return 'NOT_FOUND'
        # Reconcile SQL first even if provider is down. Terminal/cancelled jobs
        # need no provider lookup to suppress future scheduling/publication.
        state = repository.reconcile(intent.submission_id, [])
        if state in {'COMPLETED', 'TERMINAL', 'RETIRED', 'RETRY_READY', 'BUDGET_EXHAUSTED'}:
            return state
        observations = provider.lookup(intent)
        if not isinstance(observations, (tuple, list)) or len(observations) > 32:
            raise ValueError('Bounded observations required')
        if any(not isinstance(o, Observation) or o.submission_id != intent.submission_id or o.fingerprint != intent.fingerprint for o in observations):
            raise ValueError('Provider lookup correlation unavailable')
        return repository.reconcile(intent.submission_id, observations)
    return Context().run(operation)
