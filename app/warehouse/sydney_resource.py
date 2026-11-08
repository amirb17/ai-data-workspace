
"""Fail-closed validation of an existing Sydney warehouse resource.

No registration, database access, cloud client, or activation.
"""

from app.warehouse.sydney_binding import Registry


def resolve_sydney_resource(
    resource: dict,
    registry: Registry,
    *,
    workspace_id: int,
    model_id: int,
    version_id: int,
    expected_approval: str,
    publication_id: int,
    revision: int,
    schema_sha256: str,
    source_fingerprint: str,
) -> dict:
    """Resolve an already-authorized, registered production resource."""

    if not isinstance(resource, dict):
        raise ValueError("Registered resource required")

    if not isinstance(registry, Registry):
        raise ValueError("Reviewed registry required")

    expected_scope = {
        "workspace_id": workspace_id,
        "warehouse_model_id": model_id,
        "warehouse_model_version_id": version_id,
    }

    for key, value in expected_scope.items():
        if type(value) is not int or value <= 0:
            raise ValueError("Positive trusted scope required")

        if type(resource.get(key)) is not int or resource[key] != value:
            raise PermissionError("Warehouse resource scope mismatch")

    if (
        type(resource.get("resource_id")) is not int
        or resource["resource_id"] <= 0
        or resource.get("provider") != "BIGQUERY"
        or resource.get("environment") != "production"
        or resource.get("location") != "australia-southeast1"
        or resource.get("status") != "REGISTERED"
    ):
        raise PermissionError("Registered Sydney resource required")

    generation = resource.get("generation")
    if type(generation) is not int or generation <= 0:
        raise ValueError("Valid resource generation required")

    resolved = registry.resolve(
        workspace_id,
        expected_fingerprint=expected_approval,
        model_id=model_id,
        version_id=version_id,
        generation=generation,
        publication_id=publication_id,
        revision=revision,
        schema_sha256=schema_sha256,
        source_fingerprint=source_fingerprint,
    )

    expected_reference = {
        "reference_version": 1,
        "project": resolved["project"],
        "dataset": resolved["dataset"],
        "table": f"m{model_id}_v{version_id}_g{generation}",
    }

    if resource.get("physical_reference") != expected_reference:
        raise PermissionError("Physical resource identity mismatch")

    return resolved
