"""414F3 — atomic terminal attestation to existing query-governance ledger.

Trusted backend-only PostgreSQL transaction. No provider IO, no result
retrieval, no new job or cloud/publication cleanup authority.
"""
from dataclasses import dataclass

from app.db import query_governance_repository as accounting
from app.db import warehouse_query_send_repository as repo
from app.services.warehouse_sql_terminal_evidence_service import (
    _inspect_terminal_evidence_locked,
    TerminalEvidenceConflict,
)


class WarehouseAccountingConflict(ValueError):
    pass


@dataclass(frozen=True)
class AccountingResult:
    attempt_id: int
    reservation_id: int
    outcome: str
    accounted_bytes: int
    accounting_basis: str
    provider_job_id: str
    reused: bool
    publication_release_authorized: bool = False
    provider_dispatched: bool = False


def settle_verified_warehouse_terminal(*, attempt_id: int) -> AccountingResult:
    """Commit at most one governance usage row for an attested terminal job.

    Uses one PostgreSQL transaction for all evidence, reservation locks,
    ledger accounting and status changes. Repeated calls are idempotent only
    when the previously written usage matches the immutable attestation.
    The terminal journal and publication dependencies remain permanently held.
    """
    if type(attempt_id) is not int or attempt_id < 1:
        raise ValueError('Server attempt ID required')

    with repo.transaction(duty='accounting') as conn:
        evidence = _inspect_terminal_evidence_locked(conn, attempt_id)
        checkpoint = repo.checkpoint(conn, attempt_id)
        # Governance complete/fail locks the reservation FOR UPDATE before
        # mutating ledger state. Do not require UPDATE on immutable attempt
        # metadata for an accounting-only principal.
        reservation = repo.lock_identity(conn, checkpoint, lock=False)
        expected_outcome = ('SUCCEEDED' if evidence.observation == 'SUCCEEDED'
                            else 'FAILED' if evidence.observation in ('FAILED', 'CANCELLED')
                            else None)
        if evidence.classification == 'ALREADY_SETTLED_INSPECT_ONLY':
            usage = repo.one(
                conn, 'SELECT * FROM query_governance_usage WHERE reservation_id=%s',
                (reservation['reservation_id'],),
            )
            attested = repo.one(conn, """
                SELECT a.observation,a.verdict,a.bytes_processed,a.bytes_billed,
                       a.billing_complete,a.payload_sha256,a.policy_sha256,
                       p.expected_sha256,p.expected,
                       j.observation AS journal_observation,j.phase,j.event
                  FROM warehouse_query_recovery_attestations a
                  JOIN warehouse_query_provider_policies p USING(attempt_id)
                  JOIN warehouse_query_send_journal j
                    ON j.attempt_id=a.attempt_id AND j.revision=a.journal_revision
                 WHERE a.attempt_id=%s AND j.revision=(
                   SELECT MAX(revision) FROM warehouse_query_send_journal
                   WHERE attempt_id=%s)
                   AND j.event='OBSERVATION'
            """, (attempt_id,attempt_id))
            if (not usage or not attested or attested['verdict']!='VERIFIED_METADATA'
                    or not attested['billing_complete']
                    or not attested['payload_sha256']
                    or attested['event'] != 'OBSERVATION'
                    or attested['journal_observation'] != attested['observation']
                    or attested['phase'] != (
                        'SUCCEEDED_OBSERVED' if attested['observation'] == 'SUCCEEDED'
                        else 'FAILED_OBSERVED' if attested['observation'] == 'FAILED'
                        else 'CANCELLED_OBSERVED' if attested['observation'] == 'CANCELLED'
                        else None)
                    or attested['policy_sha256']!=attested['expected_sha256']
                    or type(attested['expected'].get('maximum_bytes_billed')) is not int
                    or type(attested['bytes_processed']) is not int
                    or type(attested['bytes_billed']) is not int
                    or not 0 <= attested['bytes_billed'] <= attested['expected']['maximum_bytes_billed']
                    or attested['bytes_processed'] < 0
                    or usage['accounted_bytes'] != max(attested['bytes_processed'], attested['bytes_billed'])
                    or usage['duration_ms'] != 0
                    or usage['outcome'] != expected_outcome
                    or usage['provider_job_id'] != evidence.provider_job_id
                    or usage['bytes_processed'] != attested['bytes_processed']
                    or usage['bytes_billed'] != attested['bytes_billed']
                    or usage['accounting_basis'] != 'ACTUAL'
                    or reservation['status'] != ('COMPLETED' if expected_outcome == 'SUCCEEDED' else 'FAILED')):
                raise WarehouseAccountingConflict('SETTLED_WAREHOUSE_EVIDENCE_CONFLICT')
            return AccountingResult(
                attempt_id,reservation['reservation_id'],usage['outcome'],
                usage['accounted_bytes'],usage['accounting_basis'],
                evidence.provider_job_id,True,
            )

        if (evidence.classification not in (
                    'VERIFIED_SUCCESS_REQUIRES_SETTLEMENT',
                    'VERIFIED_NON_SUCCESS_REQUIRES_SETTLEMENT')
                or evidence.bytes_processed is None or evidence.bytes_billed is None):
            raise WarehouseAccountingConflict('VERIFIED_BILLING_EVIDENCE_REQUIRED')
        if expected_outcome is None:
            raise TerminalEvidenceConflict('UNKNOWN_TERMINAL_STATUS')
        # Duration 0 is deliberately a no-metric sentinel until verified
        # provider timings become part of immutable attestation evidence.
        # Never infer wall clock or fabricate job execution duration.
        kwargs=dict(
            reservation_id=reservation['reservation_id'],
            owner_key=checkpoint['owner_key'],
            bytes_processed=evidence.bytes_processed,
            bytes_billed=evidence.bytes_billed,
            provider_job_id=evidence.provider_job_id,
            duration_ms=0,
        )
        if expected_outcome == 'SUCCEEDED':
            usage=accounting.complete(conn, **kwargs)
        else:
            usage=accounting.fail(conn, **kwargs)

        if usage['provider_job_id'] != evidence.provider_job_id:
            raise WarehouseAccountingConflict('SETTLEMENT_JOB_MISMATCH')
        return AccountingResult(
            attempt_id,reservation['reservation_id'],usage['outcome'],
            usage['accounted_bytes'],usage['accounting_basis'],
            evidence.provider_job_id,False,
        )
