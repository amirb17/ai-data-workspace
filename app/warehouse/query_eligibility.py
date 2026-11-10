"""Provider-neutral warehouse SQL eligibility gates.

These functions are deliberately pure: no SQL execution, external credentials,
catalog introspection, or claims that a registered table is queryable.
"""
from dataclasses import dataclass
from enum import StrEnum


class BlockReason(StrEnum):
    NO_MODELS = "NO_MODELS"
    UNAPPROVED_MODEL = "UNAPPROVED_MODEL"
    SOURCE_NOT_CURRENT = "SOURCE_NOT_CURRENT"
    SERVING_NOT_VERIFIED = "SERVING_NOT_VERIFIED"
    PUBLICATION_NOT_CURRENT = "PUBLICATION_NOT_CURRENT"
    WAREHOUSE_EXECUTION_DISABLED = "WAREHOUSE_EXECUTION_DISABLED"


@dataclass(frozen=True)
class WarehouseQueryEligibility:
    allowed: bool
    reasons: tuple[BlockReason, ...]
    workspace_id: int
    model_ids: tuple[int, ...]


def assess_eligibility(
    *,
    workspace_id: int,
    models: list[dict],
    execution_enabled: bool,
) -> WarehouseQueryEligibility:
    """Require explicit readiness for every selected model.

    This is only one precondition for future execution; even a true result
    cannot authorize a provider job without user ownership, SQL validation,
    publication pinning and cost/time/row constraints at execution time.
    """
    if type(workspace_id) is not int or workspace_id < 1:
        raise ValueError("Invalid workspace")
    if type(execution_enabled) is not bool:
        raise ValueError("Execution flag must be explicit")
    if not isinstance(models, list):
        raise ValueError("Models must be provided as an explicit list")

    reasons: set[BlockReason] = set()
    model_ids: list[int] = []
    if not models:
        reasons.add(BlockReason.NO_MODELS)
    for model in models:
        if not isinstance(model, dict):
            raise ValueError("Invalid model metadata")
        model_id = model.get("model_id")
        if type(model_id) is not int or model_id < 1 or model_id in model_ids:
            raise ValueError("Model IDs must be distinct positive integers")
        model_ids.append(model_id)
        if model.get("workspace_id") != workspace_id or model.get("status") != "APPROVED":
            reasons.add(BlockReason.UNAPPROVED_MODEL)
        if model.get("sources_current") is not True:
            reasons.add(BlockReason.SOURCE_NOT_CURRENT)
        if model.get("serving_verified") is not True:
            reasons.add(BlockReason.SERVING_NOT_VERIFIED)
        if model.get("publication_current") is not True:
            reasons.add(BlockReason.PUBLICATION_NOT_CURRENT)
    if not execution_enabled:
        reasons.add(BlockReason.WAREHOUSE_EXECUTION_DISABLED)
    return WarehouseQueryEligibility(
        allowed=not reasons,
        reasons=tuple(sorted(reasons, key=lambda item: item.value)),
        workspace_id=workspace_id,
        model_ids=tuple(model_ids),
    )
