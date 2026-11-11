"""414F4 — bounded BigQuery cancellation transport semantics.

Pure protocol adapter. No default cloud client, credentials, HTTP endpoints,
retries, job submission, settlement or result fetch. Only a separately
authorized worker may call it after recording a durable cancellation intent.
"""
from dataclasses import dataclass
import re
from typing import Protocol


class JobCancelTransport(Protocol):
    def cancel_job_once(self, *, project: str, location: str, job_id: str) -> object:
        """Attempt one cancellation with transport-level retries disabled."""


@dataclass(frozen=True)
class CancelAttemptObservation:
    job_id: str
    classification: str
    lookup_required: bool = True
    cancellation_confirmed: bool = False
    settlement_authorized: bool = False
    provider_dispatched: bool = False


def cancel_exact_job_once_after_intent(
    *, project: str, location: str, job_id: str, transport: JobCancelTransport,
) -> CancelAttemptObservation:
    """Issue at most one cancel; all replies remain unverified.

    A provider acknowledgement may mean the request was accepted, not that
    the job stopped. A lost response could mean cancel succeeded or failed.
    Neither outcome proves the terminal query status or billed bytes.
    This adapter is NOT independently an authorization/cancel permit.
    """
    if (type(project) is not str
            or not re.fullmatch(r'[a-z][a-z0-9-]{4,28}[a-z0-9]', project)
            or location != 'asia-south1'
            or type(job_id) is not str
            or not re.fullmatch(r'dr_query_w[1-9][0-9]*_r[1-9][0-9]*_[a-f0-9]{32}', job_id)):
        raise ValueError('EXACT_PINNED_CANCEL_SCOPE_REQUIRED')
    if not callable(getattr(transport, 'cancel_job_once', None)):
        raise TypeError('Explicit reviewed cancel transport required')
    try:
        transport.cancel_job_once(project=project,location=location,job_id=job_id)
    except Exception:
        return CancelAttemptObservation(job_id, 'CANCEL_OUTCOME_UNKNOWN')
    return CancelAttemptObservation(job_id, 'CANCEL_ACKNOWLEDGED_UNVERIFIED')
