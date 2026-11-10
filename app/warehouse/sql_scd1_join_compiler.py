"""Step 412B: pure compilation of reviewed CURRENT_MEMBER FACT/DIMENSION SQL.

No DB, provider, execution, or authorization. The caller must independently
resolve the current approved relationship and both serving receipts; no
user-provided relationship or source object is an access grant.
"""
import hashlib
import json
import re

from app.processing.metric_warehouse_compiler import MetricWarehouseSource
from app.query_governance.providers.base import CompiledQuery
from app.warehouse.relationship_contract import WarehouseRelationship
from app.warehouse.sql_join_draft_planner import WarehouseJoinDraft, plan_join_draft
from app.warehouse.sql_physical_binding import InternalDraftBinding

_NAME=re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_COLUMN=re.compile(r"^c(?:0|[1-9][0-9]*)$")


def compile_current_member_join(
    *, workspace_id: int, draft: WarehouseJoinDraft,
    binding: InternalDraftBinding, relationship: WarehouseRelationship,
    fact: MetricWarehouseSource, dimension: MetricWarehouseSource,
) -> CompiledQuery:
    """Revalidate the complete reviewed logical AST and compile only typed columns."""
    if type(workspace_id) is not int or workspace_id < 1:
        raise ValueError("Positive workspace required")
    if (type(draft) is not WarehouseJoinDraft or draft.executable is not False
            or type(binding) is not InternalDraftBinding or binding.executable is not False
            or type(relationship) is not WarehouseRelationship
            or type(fact) is not MetricWarehouseSource
            or type(dimension) is not MetricWarehouseSource):
        raise ValueError("Strict reviewed join contracts required")
    if (relationship.workspace_id != workspace_id
            or relationship.temporal_policy != "CURRENT_MEMBER"
            or relationship.cardinality != "MANY_TO_ONE"
            or relationship.fact_event_time_column is not None
            or fact.model_type != "FACT" or dimension.model_type != "DIMENSION"
            or len(binding.models) != 2
            or len(binding.columns) != len(draft.projected_columns)
            or not 1 <= len(binding.columns) <= 32
            or type(draft.row_limit) is not int or not 1 <= draft.row_limit <= 500):
        raise ValueError("Only bounded reviewed SCD1 joins are supported")

    required=(
        (fact,relationship.fact_model_id,relationship.fact_model_version_id),
        (dimension,relationship.dimension_model_id,relationship.dimension_model_version_id),
    )
    for (source,mid,vid),model in zip(required,binding.models):
        if (source.model_id,source.model_version_id)!=(mid,vid) or (
            model.model_id,model.model_version_id,model.resource_id,
            model.generation,model.publication_id,model.publication_revision,
            model.serving_materialization_id,model.schema_sha256,
            model.serving_sha256,model.physical_table
        ) != (
            source.model_id,source.model_version_id,source.resource_id,
            source.generation,source.publication_id,source.publication_revision,
            source.serving_materialization_id,source.schema_sha256,
            source.serving_sha256,source.physical_table
        ):
            raise ValueError("Reviewed relationship/source/publication pins diverged")
    if (draft.relationship_id < 1
            or (draft.fact_model_id,draft.fact_model_version_id,
                draft.dimension_model_id,draft.dimension_model_version_id)
            != (relationship.fact_model_id,relationship.fact_model_version_id,
                relationship.dimension_model_id,relationship.dimension_model_version_id)):
        raise ValueError("Draft model or relationship scope mismatch")

    def catalog_model(source):
        return {"model_id":source.model_id,"model_version_id":source.model_version_id,
                "model_type":source.model_type,"status":"APPROVED",
                "columns":[{"name":name,"role":"ATTRIBUTE"} for name,_,_ in source.columns]}
    relation={
        "relationship_id":draft.relationship_id,
        "workspace_id":workspace_id,
        "fact_model_id":relationship.fact_model_id,
        "fact_model_version_id":relationship.fact_model_version_id,
        "dimension_model_id":relationship.dimension_model_id,
        "dimension_model_version_id":relationship.dimension_model_version_id,
        "cardinality":relationship.cardinality,
        "temporal_policy":relationship.temporal_policy,
        "key_pairs":[pair.model_dump() for pair in relationship.key_pairs],
    }
    checked=plan_join_draft(
        draft.normalized_sql,workspace_id=workspace_id,
        catalog={"workspace_id":workspace_id,"models":[
            catalog_model(fact),catalog_model(dimension)]},
        relationships=[relation],relationship_id=draft.relationship_id,
    )
    if checked != draft:
        raise ValueError("Logical join draft modified after review")

    def mapping(source):
        output={}
        for name,physical,kind in source.columns:
            if not _NAME.fullmatch(name) or not _COLUMN.fullmatch(physical):
                raise ValueError("Unsafe approved warehouse column mapping")
            output[name]=(physical,kind)
        return output

    left,right=mapping(fact),mapping(dimension)
    if not relationship.key_pairs or set(pair.dimension_column for pair in relationship.key_pairs)!=set(dimension.business_keys):
        raise ValueError("Every dimension business key must be joined")
    predicates=[]
    for pair in relationship.key_pairs:
        if (pair.fact_column not in left or pair.dimension_column not in right
                or left[pair.fact_column][1]!=right[pair.dimension_column][1]
                or left[pair.fact_column][1] not in (
                    "STRING","INT64","BIGNUMERIC","BOOL","DATE","TIMESTAMP")):
            raise ValueError("Approved join keys must have identical supported types")
        predicates.append(
            f"f.`{left[pair.fact_column][0]}` = d.`{right[pair.dimension_column][0]}`"
        )
    projections=[]
    for (alias,name),column in zip(draft.projected_columns,binding.columns):
        expected=(left if alias=="f" else right).get(name)
        if (alias not in ("f","d") or expected is None
                or (column.alias,column.logical_name,column.physical_name,column.physical_type)
                != (alias,name,*expected)):
            raise ValueError("Projection must match exact approved physical column")
        projections.append(
            f"{alias}.`{column.physical_name}` AS `{alias}_{name}`"
        )
    sql=(
        f"SELECT {', '.join(projections)} "
        f"FROM `{binding.models[0].physical_table}` AS f "
        f"INNER JOIN `{binding.models[1].physical_table}` AS d "
        f"ON {' AND '.join(predicates)} LIMIT {draft.row_limit}"
    )
    fingerprint={
        "domain":"datarise-warehouse-sql-scd1-v1",
        "workspace":workspace_id,"draft":draft.normalized_sql,
        "relationship":relationship.model_dump(mode="json"),
        "relationship_id":draft.relationship_id,
        "models":[vars(m) for m in binding.models],
        "sources":[source.pin() for source in (fact,dimension)],
        "columns":[vars(c) for c in binding.columns],
        "sql":sql,
    }
    payload=json.dumps(fingerprint,sort_keys=True,separators=(",",":")).encode()
    intent=hashlib.sha256(payload).hexdigest()
    token=hashlib.sha256(
        b"datarise-warehouse-query-token-v1:"+bytes.fromhex(intent)
    ).hexdigest()
    return CompiledQuery(
        workspace_id=workspace_id,request_token=token,
        intent_sha256=intent,sql=sql,
        physical_tables=tuple(m.physical_table for m in binding.models),
        compiler_version=2,
    )
