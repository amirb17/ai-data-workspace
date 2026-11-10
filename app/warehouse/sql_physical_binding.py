"""Step 409A: internal-only logical-to-physical warehouse column binding.

Only accepts a typed MetricWarehouseSource resolved by existing repository
publication-evidence checks. This function is NOT an authorization boundary,
not provider verification, and never emits or executes SQL.
"""
from dataclasses import dataclass
from app.processing.metric_warehouse_compiler import MetricWarehouseSource
from app.warehouse.sql_draft_planner import WarehouseSqlDraft
from app.warehouse.sql_join_draft_planner import WarehouseJoinDraft
from app.warehouse.sql_scd2_draft_planner import Scd2SqlDraft


@dataclass(frozen=True)
class InternalColumnBinding:
    alias: str
    logical_name: str
    physical_name: str
    physical_type: str


@dataclass(frozen=True)
class InternalModelBinding:
    model_id: int
    model_version_id: int
    resource_id: int
    generation: int
    publication_id: int
    publication_revision: int
    serving_materialization_id: int
    schema_sha256: str
    serving_sha256: str
    # Sensitive backend-only. Never serialize this object to an API response.
    physical_table: str


@dataclass(frozen=True)
class InternalDraftBinding:
    models: tuple[InternalModelBinding, ...]
    columns: tuple[InternalColumnBinding, ...]
    executable: bool = False


def _source(source, model_id, version_id, model_type):
    if type(source) is not MetricWarehouseSource:
        raise ValueError("A typed, independently resolved warehouse source is required")
    if (source.model_id, source.model_version_id, source.model_type) != (
        model_id, version_id, model_type
    ):
        raise ValueError("Warehouse model/version/type differs from publication binding")
    if source.schema_sha256 is None:
        raise ValueError("A pinned physical schema digest is mandatory")
    return InternalModelBinding(
        source.model_id,source.model_version_id,source.resource_id,source.generation,
        source.publication_id,source.publication_revision,
        source.serving_materialization_id,source.schema_sha256,
        source.serving_sha256,source.physical_table,
    )


def _columns(source, alias, requested):
    mapping={logical:(physical,kind) for logical,physical,kind in source.columns}
    result=[]
    for name in requested:
        if name not in mapping:
            raise ValueError("Projected column absent from published physical schema")
        physical,kind=mapping[name]
        result.append(InternalColumnBinding(alias,name,physical,kind))
    return tuple(result)


def bind_draft(
    draft: WarehouseSqlDraft | WarehouseJoinDraft | Scd2SqlDraft,
    *, fact: MetricWarehouseSource,
    dimension: MetricWarehouseSource | None = None,
) -> InternalDraftBinding:
    """Produce an internal descriptor, not SQL or a read permission.

    A future execution gateway must resolve *fresh* publication pins, check
    relationship ownership and provider attestation, and enforce byte/row/time
    budgets before any SQL can be generated.
    """
    if not isinstance(draft,(WarehouseSqlDraft,WarehouseJoinDraft,Scd2SqlDraft)) or draft.executable is not False:
        raise ValueError("A non-executable reviewed SQL draft is required")
    if isinstance(draft,WarehouseSqlDraft):
        if dimension is not None:
            raise ValueError("Single-table draft cannot bind a dimension")
        if fact.model_id!=draft.model_id or fact.model_version_id!=draft.model_version_id:
            raise ValueError("Single-table model pin mismatch")
        model=_source(fact,draft.model_id,draft.model_version_id,fact.model_type)
        columns=_columns(fact,"",draft.projected_columns)
        return InternalDraftBinding((model,),columns)
    if dimension is None:
        raise ValueError("Reviewed FACT-to-DIMENSION draft requires two bindings")
    left=_source(fact,draft.fact_model_id,draft.fact_model_version_id,"FACT")
    right=_source(dimension,draft.dimension_model_id,draft.dimension_model_version_id,"DIMENSION")
    columns=[]
    for alias,name in draft.projected_columns:
        if alias not in ("f","d"):
            raise ValueError("Invalid SQL draft table alias")
        columns.extend(_columns(fact if alias=="f" else dimension,alias,(name,)))
    return InternalDraftBinding((left,right),tuple(columns))
