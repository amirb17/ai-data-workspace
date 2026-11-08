
"""Read-only, fail-closed Sydney warehouse resource assessment.

No registration, BigQuery calls, activation or database mutation.
"""

from app.db import warehouse_repository as metadata
from app.services.warehouse_reliability_service import ReliabilityService
from app.warehouse.sydney_binding import Registry
from app.warehouse.sydney_resource import resolve_sydney_resource
from psycopg.rows import dict_row

def resolve_sydney_resource_in_transaction(
    conn,
    workspace_id,
    model_id,
    version_id,
    resource_id,
    *,
    registry,
    expected_approval,
    publication_id,
    revision,
    schema_sha256,
    source_fingerprint,
):
    """Validate persisted Sydney authorization on an owned connection.

    Caller must already have established authenticated scope,
    approved model version, and production runtime policy.
    """

    if not isinstance(registry, Registry):
        raise ValueError("Reviewed Sydney registry required")

    if type(resource_id) is not int or resource_id <= 0:
        raise ValueError("Positive resource ID required")

    resource = metadata.resource(
        conn,
        workspace_id,
        model_id,
        version_id,
        resource_id,
    )

    if resource is None:
        raise PermissionError("Registered resource not found")

    approval_id = resource.get("sydney_approval_id")

    if type(approval_id) is not int or approval_id <= 0:
        raise PermissionError(
            "Registered Sydney approval reference required"
        )

    with conn.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            """
            SELECT
                approval_id,
                workspace_id,
                project,
                aws_region,
                location,
                loader_identity,
                query_identity,
                approval_revision
            FROM warehouse_sydney_approvals
            WHERE approval_id = %s
              AND workspace_id = %s
            FOR SHARE
            """,
            (approval_id, workspace_id),
        )
        approval = cursor.fetchone()

    if approval is None:
        raise PermissionError(
            "Persisted Sydney approval not found"
        )

    verified = registry.verify_persisted(
        workspace_id,
        approval,
        expected_fingerprint=expected_approval,
    )

    if verified["approval_id"] != approval_id:
        raise PermissionError(
            "Persisted approval identity mismatch"
        )

    resolved = resolve_sydney_resource(
        dict(resource),
        registry,
        workspace_id=workspace_id,
        model_id=model_id,
        version_id=version_id,
        expected_approval=expected_approval,
        publication_id=publication_id,
        revision=revision,
        schema_sha256=schema_sha256,
        source_fingerprint=source_fingerprint,
    )

    return {
        **resolved,
        "sydney_approval_id": verified["approval_id"],
        "approval_revision": verified["approval_revision"],
    }


def resolve_registered_sydney_resource(
    workspace_id,
    model_id,
    version_id,
    resource_id,
    user,
    *,
    registry,
    expected_approval,
    publication_id,
    revision,
    schema_sha256,
    source_fingerprint,
):
    """Resolve an owned, registered Sydney resource against reviewed approval.

    Read-only and fail-closed. Does not register resources,
    modify database state, or access BigQuery.
    """

    # 1. Require an independently reviewed registry.
    if not isinstance(registry, Registry):
        raise ValueError("Reviewed Sydney registry required")

    if type(resource_id) is not int or resource_id <= 0:
        raise ValueError("Positive resource ID required")

    # 2. Establish the authenticated warehouse scope.
    reliability = ReliabilityService(
        workspace_id,
        model_id,
        version_id,
        user,
    )

    # 3. Reuse the existing authorized PostgreSQL transaction.
    with reliability.transaction() as (
        conn,
        source,
        config,
        settings,
    ):
        # 4. Fail closed outside the approved production context.
        if (
            not settings.enabled
            or settings.environment != "production"
        ):
            raise PermissionError(
                "Production warehouse context required"
            )

        # 5. Validate resource identity, persisted approval,
        # reviewed registry, and physical Sydney binding
        # using the SAME transaction.
        return resolve_sydney_resource_in_transaction(
            conn,
            workspace_id,
            model_id,
            version_id,
            resource_id,
            registry=registry,
            expected_approval=expected_approval,
            publication_id=publication_id,
            revision=revision,
            schema_sha256=schema_sha256,
            source_fingerprint=source_fingerprint,
        )
