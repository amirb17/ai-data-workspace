"""414F0-A: conservative, metadata-only publication cleanup inspection.

A PostgreSQL snapshot cannot prove cloud table retention or authorize deletion.
All outcomes deliberately deny physical deletion. This resolver documents the
blocking state under the existing per-resource retirement serialization lock.
"""
from dataclasses import dataclass

from psycopg.rows import dict_row

from app.db.database import repository_transaction, require_connection_boundary


@dataclass(frozen=True)
class PublicationCleanupInspection:
    resource_id: int
    publication_id: int
    reason: str
    current_head: bool
    query_dependency: bool
    retired: bool
    physical_deletion_authorized: bool = False
    provider_dispatched: bool = False


def inspect_publication_cleanup_without_deletion(
    *, resource_id: int, publication_id: int,
) -> PublicationCleanupInspection:
    """Internal-only read; callers cannot turn the result into a delete permit.

    No provider client, table name, user principal or policy override is
    accepted. This function is not a public endpoint. A future cleanup worker
    must introduce separately reviewed IAM and cloud-side concurrency fencing.
    """
    if (type(resource_id) is not int or resource_id < 1
            or type(publication_id) is not int or publication_id < 1):
        raise ValueError("Positive publication identity required")

    require_connection_boundary('retention_inspection')
    with repository_transaction() as conn:
        conn.execute("SET LOCAL lock_timeout='5s'")
        conn.execute("SET LOCAL statement_timeout='10s'")
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (f"datarise-publication-retirement:{resource_id}",),
        )
        publication=conn.cursor(row_factory=dict_row).execute(
            """SELECT p.publication_id,p.resource_id
               FROM warehouse_publications p
               JOIN warehouse_resources r ON r.resource_id=p.resource_id
               WHERE p.resource_id=%s AND p.publication_id=%s
               FOR SHARE OF p,r""",
            (resource_id,publication_id),
        ).fetchone()
        if publication is None:
            raise ValueError("WAREHOUSE_PUBLICATION_NOT_FOUND")
        # Exactly the same dependency authority as the retirement trigger;
        # includes permanent 0053 checkpoint holds, even after settlement.
        current=conn.execute(
            """SELECT EXISTS(
               SELECT 1 FROM warehouse_publication_heads
               WHERE resource_id=%s AND publication_id=%s
               )""",
            (resource_id,publication_id),
        ).fetchone()[0]
        held=conn.execute(
            "SELECT warehouse_query_publication_dependency_present(%s,%s)",
            (resource_id,publication_id),
        ).fetchone()[0]
        retired=conn.execute(
            """SELECT EXISTS(
               SELECT 1 FROM warehouse_query_publication_retirement_fences
               WHERE resource_id=%s AND publication_id=%s
               )""",
            (resource_id,publication_id),
        ).fetchone()[0]
        reason=("CURRENT_PUBLICATION_HEAD" if current
                else "QUERY_RETENTION_DEPENDENCY" if held
                else "RETIREMENT_FENCE_MISSING" if not retired
                else "RETIRED_METADATA_ONLY_CLOUD_ENFORCEMENT_UNVERIFIED")
        return PublicationCleanupInspection(
            resource_id,publication_id,reason,current,held,retired,
        )
