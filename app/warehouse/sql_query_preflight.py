"""Step 411A: pure fail-closed warehouse query preflight.

Connects internal publication-pinned model bindings to the EXISTING
query-governance CompiledQuery / ExecutionPermit contracts. It does NOT
mint reservations, verify live provider state, authorize execution, or
provide an executable query plan. A dispatch gateway must independently
re-resolve authorized current publication pins and verify cloud metadata.
"""
from dataclasses import dataclass

from app.query_governance.providers.base import (
    CompiledQuery, ExecutionPermit, MAX_OUTPUT_ROWS, MAX_EXECUTION_BYTES,
)
from app.warehouse.sql_physical_binding import InternalDraftBinding


@dataclass(frozen=True)
class WarehouseQueryPreflight:
    workspace_id: int
    table_count: int
    maximum_bytes_billed: int
    output_rows: int
    ready_for_execution: bool = False
    query_execution_authorized: bool = False


def inspect_warehouse_query_scope(
    *,
    workspace_id: int,
    binding: InternalDraftBinding,
    query: CompiledQuery,
    permit: ExecutionPermit,
) -> WarehouseQueryPreflight:
    """Static consistency check only. Never accept SQL from a user as trusted.

    The query must originate from a future reviewed compiler; this function
    cannot prove that origin or validate arbitrary SQL text. No provider call,
    query execution, access grant, reservation or DB write is performed.
    """
    if type(workspace_id) is not int or workspace_id < 1:
        raise ValueError("Positive warehouse workspace required")
    if type(binding) is not InternalDraftBinding or binding.executable is not False:
        raise ValueError("Internal non-executable physical binding required")
    if type(query) is not CompiledQuery or type(permit) is not ExecutionPermit:
        raise ValueError("Existing compiled-query and durable permit types required")
    if query.workspace_id != workspace_id or permit.workspace_id != workspace_id:
        raise ValueError("Warehouse workspace scope mismatch")
    if permit.provider != "BIGQUERY":
        raise ValueError("BigQuery provider required for warehouse preflight")
    permit.authorize(query, provider="BIGQUERY")
    if query.compiler_version != 2:
        raise ValueError("Warehouse query compiler version 2 required")
    if len(binding.models) not in (1,2):
        raise ValueError("One or two publication-pinned models required")
    models=binding.models
    if len({m.model_id for m in models}) != len(models):
        raise ValueError("Duplicate warehouse model binding")
    expected=tuple(model.physical_table for model in models)
    if len(set(expected)) != len(expected) or query.physical_tables != expected:
        raise ValueError("Exact ordered publication table scope mismatch")
    if (permit.reserved_bytes < 1
            or permit.maximum_bytes_billed > MAX_EXECUTION_BYTES
            or permit.output_rows > MAX_OUTPUT_ROWS):
        raise ValueError("Bounded nonzero cost reservation required")
    # A preflight is never a permit. Every concrete execution path must
    # enforce fresh authorization and durable reservation at dispatch.
    return WarehouseQueryPreflight(
        workspace_id,len(expected),permit.maximum_bytes_billed,permit.output_rows,
    )
