"""Step 412D: internal, non-authorizing compiled-query evidence comparison.

This is an integration diagnostic, not an execution gateway. Its callbacks
MUST be backed by authenticated, independently revalidated PostgreSQL
resolution and trusted compiler inputs; this module cannot authenticate a
callback. Neither this result nor a previously claimed reservation grants
permission to dispatch SQL. Actual dispatch needs its own atomic gates.
"""
from dataclasses import dataclass
from typing import Callable

from app.query_governance.providers.base import CompiledQuery
from app.warehouse.sql_physical_binding import InternalDraftBinding
from app.warehouse.sql_provider_metadata_verifier import (
    MetadataAttestation, verify_serving_table_metadata,
)


@dataclass(frozen=True)
class WarehouseCompiledEvidence:
    workspace_id: int
    model_count: int
    table_metadata_verified: bool = True
    current_publication_checked_after_metadata: bool = True
    query_execution_authorized: bool = False
    row_contents_verified: bool = False


def inspect_compiled_warehouse_evidence(
    *,
    workspace_id: int,
    query: CompiledQuery,
    resolve_current: Callable[[], InternalDraftBinding],
    compile_current: Callable[[InternalDraftBinding], CompiledQuery],
    expected_schemas: dict[int, tuple[tuple[str, str, str], ...]],
    client,
    project: str,
    location: str = "asia-south1",
) -> WarehouseCompiledEvidence:
    """Require a rebuilt, byte-identical query before and after metadata IO.

    The compiler must be the reviewed single/SCD1/SCD2 compiler using owned
    authoritative sources. This check does not mint a claim, issue a permit,
    execute provider queries, or establish atomic freshness for later dispatch.
    """
    if type(workspace_id) is not int or workspace_id < 1:
        raise ValueError("Positive warehouse workspace required")
    if type(query) is not CompiledQuery or query.workspace_id != workspace_id:
        raise ValueError("Workspace-bound compiled query required")
    if query.compiler_version != 2 or not callable(resolve_current) or not callable(compile_current):
        raise ValueError("Trusted current resolver and v2 compiler required")

    first = resolve_current()
    if type(first) is not InternalDraftBinding or first.executable is not False:
        raise ValueError("Internal non-executable publication binding required")
    if not 1 <= len(first.models) <= 2:
        raise ValueError("Bounded pinned model scope required")
    tables=tuple(model.physical_table for model in first.models)
    if query.physical_tables != tables:
        raise ValueError("Compiled query publication scope mismatch")
    original=compile_current(first)
    if type(original) is not CompiledQuery or original != query:
        raise ValueError("Compiled SQL differs from trusted current compilation")

    attested=verify_serving_table_metadata(
        first,workspace_id=workspace_id,expected_schemas=expected_schemas,
        client=client,project=project,location=location,
    )
    pins=tuple(
        (a.model_id,a.model_version_id,a.resource_id,a.publication_id,a.publication_revision)
        for a in attested
    )
    expected=tuple(
        (m.model_id,m.model_version_id,m.resource_id,m.publication_id,m.publication_revision)
        for m in first.models
    )
    if (type(attested) is not tuple or pins != expected
            or any(type(a) is not MetadataAttestation
                   or not a.table_metadata_verified
                   or a.row_contents_verified or a.query_execution_authorized
                   for a in attested)):
        raise ValueError("Incomplete or inconsistent provider attestation")

    second=resolve_current()
    if type(second) is not InternalDraftBinding or second != first:
        raise ValueError("WAREHOUSE_PUBLICATION_CHANGED_DURING_VERIFICATION")
    rebuilt=compile_current(second)
    if type(rebuilt) is not CompiledQuery or rebuilt != query:
        raise ValueError("WAREHOUSE_COMPILED_QUERY_CHANGED_DURING_VERIFICATION")

    return WarehouseCompiledEvidence(workspace_id,len(first.models))
