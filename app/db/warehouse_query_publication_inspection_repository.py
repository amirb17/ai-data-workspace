"""414C3c read-only, non-authorizing publication dependency inspection.

This checks durable ownership, linked reservation and retirement metadata.
It does not prove trusted compilation, physical table retention or cloud
authorization and must NEVER be treated as an execution permit.
"""
from dataclasses import dataclass

from psycopg.rows import dict_row


@dataclass(frozen=True)
class PublicationDependencyInspection:
    attempt_id: int
    reservation_id: int
    metadata_consistent: bool
    reason: str
    publication_ids: tuple[int, ...] = ()
    execution_authorized: bool = False


def inspect_linked_publications(
    conn, *, workspace_id: int, owner_key: str, attempt_id: int,
) -> PublicationDependencyInspection:
    from app.db import query_governance_repository as governance

    if (type(workspace_id) is not int or workspace_id < 1
            or not isinstance(owner_key, str) or not owner_key
            or type(attempt_id) is not int or attempt_id < 1):
        raise ValueError("Owned bounded warehouse query attempt required")

    governance._lock_scopes(conn, workspace_id)
    governance._require_owner(conn, workspace_id, owner_key)

    attempt=conn.cursor(row_factory=dict_row).execute(
        """SELECT * FROM warehouse_query_attempt_registry
           WHERE attempt_id=%s AND workspace_id=%s AND owner_key=%s
           FOR SHARE""",
        (attempt_id, workspace_id, owner_key),
    ).fetchone()
    if attempt is None:
        raise ValueError("WAREHOUSE_ATTEMPT_NOT_FOUND")

    link=conn.cursor(row_factory=dict_row).execute(
        """SELECT l.reservation_id,r.status,r.expires_at,r.request_token,
                  r.owner_key,r.workspace_id,r.consumer,r.provider
           FROM warehouse_query_attempt_reservation_links l
           JOIN query_governance_reservations r
             ON r.reservation_id=l.reservation_id
           WHERE l.attempt_id=%s
           FOR SHARE OF l,r""",
        (attempt_id,),
    ).fetchone()
    if not link:
        return PublicationDependencyInspection(
            attempt_id,0,False,"LINK_MISSING",
        )

    rid=link["reservation_id"]
    def deny(reason):
        return PublicationDependencyInspection(attempt_id,rid,False,reason)

    if (link["workspace_id"]!=workspace_id
            or link["owner_key"]!=owner_key
            or link["request_token"]!=attempt["request_token"].strip()
            or link["consumer"]!="SQL" or link["provider"]!="BIGQUERY"):
        return deny("RESERVATION_IDENTITY_MISMATCH")
    if link["status"] not in ("ACTIVE","EXECUTING"):
        return deny("RESERVATION_NOT_LIVE")
    if link["status"]=="ACTIVE":
        not_expired=conn.execute(
            "SELECT %s > clock_timestamp()", (link["expires_at"],)
        ).fetchone()[0]
        if not not_expired:
            return deny("RESERVATION_EXPIRED")

    pins=conn.cursor(row_factory=dict_row).execute(
        """SELECT p.resource_id,p.publication_id,p.publication_revision,
                  w.workspace_id,w.status,w.environment
           FROM warehouse_query_attempt_publication_pins p
           JOIN warehouse_resources w ON w.resource_id=p.resource_id
           WHERE p.attempt_id=%s ORDER BY p.resource_id""",
        (attempt_id,),
    ).fetchall()
    if not 1<=len(pins)<=2:
        return deny("PIN_COUNT_INVALID")

    # Insertion/retirement share these resource advisory locks.
    # This read is only consistent within its DB transaction; an external
    # provider call cannot inherit this lock after commit.
    for pin in pins:
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (f"datarise-publication-retirement:{pin['resource_id']}",),
        )
    for pin in pins:
        if (pin["workspace_id"]!=workspace_id
                or pin["status"]!="REGISTERED"
                or pin["environment"]!="development"):
            return deny("RESOURCE_REVOKED")
        if conn.execute(
            """SELECT EXISTS(
                 SELECT 1 FROM warehouse_query_publication_retirement_fences
                 WHERE resource_id=%s AND publication_id=%s
               )""",
            (pin["resource_id"],pin["publication_id"]),
        ).fetchone()[0]:
            return deny("PUBLICATION_RETIRED")
    return PublicationDependencyInspection(
        attempt_id,rid,True,"METADATA_CONSISTENT_ONLY",
        tuple(pin["publication_id"] for pin in pins),
    )
