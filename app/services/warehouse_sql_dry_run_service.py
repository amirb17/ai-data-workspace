"""Step 413A: distinct server-issued query attempts and dry-run-only estimates.

The 412A-C compilers produce deterministic query *templates*. Their request
tokens must not be used as unique execution attempts: otherwise two legitimate
runs reuse one durable query-governance reservation. Rebind a fresh random
server-side token only after authoritative compiled-evidence validation.

No reservation, claim, provider execute, recovery or job mutation occurs here.
The supplied provider must be the existing read provider whose estimate()
issues BigQuery dry-run SELECTs and checks referenced tables and byte counts.
"""
from dataclasses import dataclass, replace
import secrets
from typing import Callable

from app.query_governance.providers.base import CompiledQuery
from app.query_governance.types import EstimateConfidence, QueryEstimate
from app.services.warehouse_sql_compiled_evidence_service import (
    WarehouseCompiledEvidence,
)

_ALLOWED_CONFIDENCE = {EstimateConfidence.EXACT, EstimateConfidence.UPPER_BOUND}


@dataclass(frozen=True)
class WarehouseDryRunEstimate:
    query: CompiledQuery
    estimate: QueryEstimate
    metadata_and_publication_checked: bool = True
    admission_created: bool = False
    execution_authorized: bool = False


def prepare_warehouse_query_attempt(
    *, template: CompiledQuery,
    evidence: WarehouseCompiledEvidence,
    random_token: Callable[[], str] | None = None,
) -> CompiledQuery:
    """Allocate a fresh *server-issued* retry/idempotency identity.

    random_token is injectable for deterministic unit tests only. The real
    gateway must use the default secrets.token_hex and keep retry IDs server
    scoped. A random token alone is never authorization or a cost reservation.
    """
    if type(template) is not CompiledQuery or template.compiler_version != 2:
        raise ValueError("A compiled warehouse v2 template is required")
    if (type(evidence) is not WarehouseCompiledEvidence
            or evidence.workspace_id != template.workspace_id
            or evidence.model_count != len(template.physical_tables)
            or not evidence.table_metadata_verified
            or not evidence.current_publication_checked_after_metadata
            or evidence.query_execution_authorized
            or evidence.row_contents_verified):
        raise ValueError("Successful non-authorizing compiled evidence required")
    if random_token is not None and not callable(random_token):
        raise ValueError("Server token supplier required")
    token=(random_token or (lambda: secrets.token_hex(32)))()
    if (type(token) is not str or len(token) != 64
            or any(char not in "0123456789abcdef" for char in token)
            or token == template.request_token
            or token == template.intent_sha256):
        raise ValueError("Distinct 256-bit lowercase attempt token required")
    return replace(template,request_token=token)


def estimate_warehouse_query_attempt(
    *, template: CompiledQuery,
    evidence: WarehouseCompiledEvidence,
    provider,
) -> WarehouseDryRunEstimate:
    """One bounded provider estimate; NEVER call provider.execute or admit.

    An estimate is informational and may grow stale immediately. The future
    admission/execution gateway must redo all current authorization and cost
    checks, including a fresh dry-run before claiming a reservation.
    """
    if getattr(provider,"identity",None) != "BIGQUERY":
        raise ValueError("Existing BigQuery read provider required")
    attempt=prepare_warehouse_query_attempt(template=template,evidence=evidence)
    estimated=provider.estimate(attempt)
    if (type(estimated) is not QueryEstimate
            or estimated.confidence not in _ALLOWED_CONFIDENCE
            or type(estimated.bytes_processed) is not int
            or estimated.bytes_processed < 1):
        raise ValueError("Safe bounded BigQuery dry-run estimate required")
    return WarehouseDryRunEstimate(attempt,estimated)
