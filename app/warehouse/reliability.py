"""Pure receipt/reconciliation and recovery vocabulary for durable orchestration."""
from enum import StrEnum
from typing import Literal
from pydantic import Field
from app.warehouse.accepted_changes import Contract, digest, verify_manifest


class Decision(StrEnum):
    RETRY_SAFE = 'RETRY_SAFE'
    RECONCILE_FIRST = 'RECONCILE_FIRST'
    ALREADY_APPLIED = 'ALREADY_APPLIED'
    MANUAL_REVIEW = 'MANUAL_REVIEW'
    FENCED = 'FENCED'
    SOURCE_GAP = 'SOURCE_GAP'
    SOURCE_DRIFT = 'SOURCE_DRIFT'
    MODEL_DRIFT = 'MODEL_DRIFT'
    RESOURCE_DRIFT = 'RESOURCE_DRIFT'


class ProtocolError(ValueError):
    def __init__(self, code):
        self.code = Decision(code)
        super().__init__(self.code.value)


class Result(Contract):
    manifests: int = Field(ge=0)
    events: int = Field(ge=0)
    inserted: int = Field(ge=0)
    updated: int = Field(ge=0)
    unchanged: int = Field(ge=0)
    rejected: int = Field(ge=0)
    reactivated: int = Field(ge=0)
    deactivated: int = Field(ge=0)
    checksum: str = Field(pattern=r'^[a-f0-9]{64}$')


class Receipt(Contract):
    version: Literal[1] = 1
    operation_id: int = Field(gt=0)
    run_id: int = Field(gt=0)
    application_token: str = Field(pattern=r'^[a-f0-9]{64}$')
    intent_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    resource_generation: int = Field(gt=0)
    outcome: Literal['SUCCEEDED', 'FAILED', 'UNKNOWN', 'NOT_APPLIED']
    result: Result | None = None
    receipt_reference: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_-]{1,120}$')


def recover(receipt):
    if receipt is None or receipt.outcome == 'UNKNOWN':
        return Decision.RECONCILE_FIRST
    if receipt.outcome == 'SUCCEEDED':
        return Decision.ALREADY_APPLIED
    return Decision.RETRY_SAFE


def reconcile(expected, receipt, intent, operation_id):
    return (receipt.outcome == 'SUCCEEDED' and receipt.operation_id == operation_id
            and receipt.application_token == intent['application_token']
            and receipt.intent_sha256 == intent['intent_sha256']
            and receipt.resource_generation == intent['generation']
            and receipt.result == expected)


def application_token(scope, kind, evidence, request_key):
    return digest(['datarise-warehouse-application', 1, scope, kind, evidence, request_key])


class ReferenceTarget:
    """Bounded deterministic effect ledger; not a provider/physical warehouse.

    Retain the instance across simulated crashes. lookup proves an exact token's
    result (including a lost response) before orchestration attempts a reapply.
    Optional transition computes a pure SCD/fact reference result for tests.
    """
    def __init__(self):
        self.applications = {}

    def apply(self, token, manifests, transition=None):
        if not isinstance(manifests, (tuple, list)) or len(manifests) > 1000:
            raise ValueError('Bounded manifest set required')
        manifests = sorted((verify_manifest(m) for m in manifests), key=lambda m: m['position'])
        evidence = digest([[m['manifest_id'], m['checksum']] for m in manifests])
        old = self.applications.get(token)
        if old:
            if old[0] != evidence:
                raise ValueError('Application token reused with different evidence')
            return old[1]
        events = [e for m in manifests for e in m['events']]
        identities = [(e['event_id'], e['checksum']) for e in events]
        counts = {k.lower(): sum(e['outcome'] == k for e in events)
                  for k in ('INSERTED', 'UPDATED', 'UNCHANGED', 'REACTIVATED', 'DEACTIVATED')}
        state = transition(events) if transition else sorted(identities)
        result = Result(manifests=len(manifests), events=len(events), rejected=0,
                        checksum=digest(['datarise-reference-result', 1, state]), **counts)
        self.applications[token] = (evidence, result)
        return result

    def lookup(self, token):
        return self.applications.get(token, (None, None))[1]
