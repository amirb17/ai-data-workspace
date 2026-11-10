"""Step 410D: fail-closed freshness check around provider metadata reads.

An owned, publication-pinned resolver is invoked BEFORE and AFTER independent
provider inspection. Each invocation must re-check PostgreSQL ownership,
approved relationships, heads and serving receipts in a NEW transaction.

This remains a diagnostic, not a permit. Even a matching post-read snapshot
cannot prevent a later publication change or verify table rows. Any future
execution gateway must enforce its own atomic dispatch-time policy.
"""
from dataclasses import dataclass
from typing import Callable

from app.warehouse.sql_physical_binding import InternalDraftBinding
from app.warehouse.sql_provider_metadata_verifier import (
    MetadataAttestation, verify_serving_table_metadata,
)


@dataclass(frozen=True)
class FreshMetadataCheck:
    attestations: tuple[MetadataAttestation, ...]
    publication_pins_current_after_read: bool = True
    row_contents_verified: bool = False
    query_execution_authorized: bool = False


def verify_current_publication_metadata(
    *,
    resolve_current: Callable[[], InternalDraftBinding],
    workspace_id: int,
    expected_schemas: dict[int, tuple[tuple[str, str, str], ...]],
    client,
    project: str,
    location: str = "asia-south1",
) -> FreshMetadataCheck:
    """Require exact complete re-resolution on both sides of get_table calls.

    resolve_current must be the authenticated PostgreSQL-backed resolver
    with reviewed IDs and immutable source state pins fixed by the caller.
    Do NOT pass a cached binding lambda; a future execution API must
    independently enforce that contract and treat this as informational.
    """
    if not callable(resolve_current):
        raise ValueError("A fresh authorized PostgreSQL resolver is required")
    initial = resolve_current()
    if type(initial) is not InternalDraftBinding or initial.executable is not False:
        raise ValueError("A non-executable current publication binding is required")
    attestations = verify_serving_table_metadata(
        initial,
        workspace_id=workspace_id,
        expected_schemas=expected_schemas,
        client=client,
        project=project,
        location=location,
    )
    # No provider calls after this re-resolution. Revalidate all immutable
    # fields, not just ID/revision: serving receipt, physical table, digest
    # and projection metadata are part of the binding identity.
    latest = resolve_current()
    if type(latest) is not InternalDraftBinding or latest != initial:
        raise ValueError("WAREHOUSE_PUBLICATION_CHANGED_DURING_VERIFICATION")
    return FreshMetadataCheck(attestations)
