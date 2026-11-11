"""414F2: inspect immutable terminal evidence before warehouse billing settlement.

A verified provider status alone is insufficient authority to account bytes.
This read-only DB gate binds the original job, pinned request policy, journal
revision and exact metadata attestation. No grants, provider IO or settlement.
"""
from dataclasses import dataclass

from app.db import warehouse_query_send_repository as repo


class TerminalEvidenceConflict(ValueError):
    pass


@dataclass(frozen=True)
class TerminalEvidence:
    attempt_id: int
    reservation_id: int
    provider_job_id: str
    observation: str
    bytes_processed: int | None
    bytes_billed: int | None
    classification: str
    settlement_authorized: bool = False
    publication_release_authorized: bool = False


def inspect_terminal_evidence(*, attempt_id: int) -> TerminalEvidence:
    """Internal-only verification; hold on missing, unverified, or stale data."""
    if type(attempt_id) is not int or attempt_id < 1:
        raise ValueError('Server attempt ID required')
    with repo.transaction() as conn:
        c = repo.checkpoint(conn, attempt_id)
        r = repo.lock_identity(conn, c)
        intent, latest = repo.latest(conn, attempt_id)
        pinned = repo.one(conn, """
            SELECT e.provider_job_id,e.request_sha256,e.policy_sha256,
                   p.expected_sha256,p.expected
              FROM warehouse_query_request_envelopes e
              JOIN warehouse_query_provider_policies p USING(attempt_id)
             WHERE e.attempt_id=%s
        """, (attempt_id,))
        if (not pinned or pinned['provider_job_id'] != intent['provider_job_id']
                or pinned['provider_job_id'] != c['provider_job_id']
                or pinned['expected'].get('job_id') != intent['provider_job_id']
                or pinned['policy_sha256'] != pinned['expected_sha256']):
            raise TerminalEvidenceConflict('PINNED_PROVIDER_IDENTITY_REQUIRED')
        boundary = repo.one(conn, """
            SELECT revision FROM warehouse_query_send_journal
             WHERE attempt_id=%s AND event='SEND_BOUNDARY'
             ORDER BY revision LIMIT 1
        """, (attempt_id,))
        if not boundary:
            raise TerminalEvidenceConflict('COMMITTED_SEND_BOUNDARY_REQUIRED')
        if r['status'] != 'EXECUTING':
            return TerminalEvidence(
                attempt_id, r['reservation_id'], intent['provider_job_id'],
                latest['observation'] or 'UNCERTAIN', None, None, 'ALREADY_SETTLED_INSPECT_ONLY',
            )
        report = repo.one(conn, """
            SELECT a.*,j.phase,j.observation AS journal_observation
              FROM warehouse_query_recovery_attestations a
              JOIN warehouse_query_send_journal j
                ON j.attempt_id=a.attempt_id AND j.revision=a.journal_revision
             WHERE a.attempt_id=%s AND a.journal_revision=%s
        """, (attempt_id, latest['revision']))
        if (latest['event'] != 'OBSERVATION' or not report
                or latest['phase'] not in (
                    'SUCCEEDED_OBSERVED', 'FAILED_OBSERVED', 'CANCELLED_OBSERVED'
                ) or report['observation'] != latest['observation']
                or report['journal_observation'] != latest['observation']
                or report['phase'] != latest['phase']
                or report['verdict'] != 'VERIFIED_METADATA'
                or report['policy_sha256'] != pinned['policy_sha256']
                or not report['payload_sha256']):
            return TerminalEvidence(
                attempt_id, r['reservation_id'], intent['provider_job_id'],
                latest['observation'] or 'UNCERTAIN', None, None, 'TERMINAL_EVIDENCE_INCOMPLETE_HELD',
            )
        processed, billed = report['bytes_processed'], report['bytes_billed']
        cap = pinned['expected'].get('maximum_bytes_billed')
        if (type(cap) is not int or type(billed) is not int
                or type(processed) is not int or not 0 <= billed <= cap
                or processed < 0 or not report['billing_complete']):
            return TerminalEvidence(
                attempt_id, r['reservation_id'], intent['provider_job_id'],
                report['observation'], None, None, 'BILLING_EVIDENCE_INCOMPLETE_HELD',
            )
        if report['observation'] == 'SUCCEEDED':
            classification = 'VERIFIED_SUCCESS_REQUIRES_SETTLEMENT'
        elif report['observation'] in ('FAILED', 'CANCELLED'):
            classification = 'VERIFIED_NON_SUCCESS_REQUIRES_SETTLEMENT'
        else:
            raise TerminalEvidenceConflict('UNKNOWN_TERMINAL_STATUS')
        return TerminalEvidence(
            attempt_id, r['reservation_id'], intent['provider_job_id'],
            report['observation'], processed, billed, classification,
        )
