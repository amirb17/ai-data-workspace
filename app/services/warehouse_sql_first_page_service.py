"""414F5: first-page-only warehouse result retrieval orchestration.

Internal simulation boundary. No provider SDK, runtime binding or public API.
The approved_schema parameter MUST come from a reviewed, server-owned
compiler contract: this function does not establish that contract itself.
"""
from dataclasses import dataclass
from typing import Protocol

from app.services.warehouse_sql_result_eligibility_service import (
    inspect_settled_result_eligibility,
    ResultEligibilityConflict,
)
from app.warehouse.query_result_pages import validate_result_page, ResultPageConflict


class FirstPageTransport(Protocol):
    def get_first_page_once(
        self, *, project: str, location: str, job_id: str, max_results: int,
    ) -> dict:
        """One bounded existing-job result lookup, retries disabled."""


@dataclass(frozen=True)
class FirstPageRead:
    job_id: str
    columns: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]
    provider_has_more: bool
    bytes_encoded: int
    classification: str = "BOUNDED_FIRST_PAGE_ONLY"
    provider_dispatched: bool = False
    execution_authorized: bool = False


class ResultFetchUnavailable(RuntimeError):
    pass


def read_first_warehouse_page_simulated(
    *, attempt_id: int, approved_schema: tuple[tuple[str,str],...],
    transport: FirstPageTransport,
) -> FirstPageRead:
    """Recheck DB facts before and after exactly one bounded fake transport IO.

    Cursors are never exposed to callers. This is deliberately a single-page
    simulation, not a production user permission gate or multi-page service.
    """
    if not callable(getattr(transport, "get_first_page_once", None)):
        raise TypeError("Explicit reviewed first-page transport required")
    scope = inspect_settled_result_eligibility(attempt_id=attempt_id)
    try:
        result = transport.get_first_page_once(
            project=scope.project, location=scope.location,
            job_id=scope.job_id, max_results=scope.max_page_rows,
        )
    except Exception:
        raise ResultFetchUnavailable("RESULT_LOOKUP_UNAVAILABLE") from None

    # Prevent stale successful settlement or policy evidence from being used
    # when the immutable source has changed between lookup and consumption.
    current = inspect_settled_result_eligibility(attempt_id=attempt_id)
    if current != scope:
        raise ResultEligibilityConflict("RESULT_SCOPE_CHANGED_DURING_LOOKUP")
    page = validate_result_page(
        job_id=scope.job_id, approved_schema=approved_schema,
        response=result, requested_rows=scope.max_page_rows,
    )
    return FirstPageRead(
        scope.job_id,page.columns,page.rows,
        page.next_page_token is not None,page.bytes_encoded,
    )


def read_first_warehouse_page_server_pinned_simulated(
    *, attempt_id: int, transport: FirstPageTransport,
) -> FirstPageRead:
    """No caller schema; only versioned, approved, original draft fields."""
    from app.services.warehouse_sql_server_result_schema_service import (
        inspect_server_result_schema, ResultSchemaConflict,
    )
    before=inspect_server_result_schema(attempt_id=attempt_id)
    outcome=read_first_warehouse_page_simulated(
        attempt_id=attempt_id, approved_schema=before.columns,
        transport=transport,
    )
    after=inspect_server_result_schema(attempt_id=attempt_id)
    if after != before:
        raise ResultSchemaConflict("RESULT_SCHEMA_CHANGED_DURING_LOOKUP")
    return outcome
