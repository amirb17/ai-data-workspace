"""Step 412C: pure, non-executing SCD2 temporal FACT/DIMENSION compiler.

Only exact reviewed EVENT_TIME_AS_OF specifications and validated logical SQL
may produce physical BigQuery SELECT text. Provider state, freshness, approval,
historical uniqueness, cost and authorization must still be rechecked by the
future dispatch gateway. Nothing in this module executes or permits queries.
"""
import hashlib
import json
import re

from app.processing.metric_warehouse_compiler import MetricWarehouseSource
from app.query_governance.providers.base import CompiledQuery
from app.warehouse.sql_physical_binding import InternalDraftBinding
from app.warehouse.sql_scd2_draft_planner import Scd2SqlDraft, validate_scd2_sql_draft
from app.warehouse.sql_scd2_specification import (
    ColumnRef, Equality, Scd2JoinSpecification, Scd2TemporalPredicate,
)

_LOGICAL = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_PHYSICAL = re.compile(r"^c(?:0|[1-9][0-9]*)$")
_TIME = {"DATE", "TIMESTAMP"}
_KEYS = {"STRING", "INT64", "BIGNUMERIC", "BOOL", "DATE", "TIMESTAMP"}


def compile_historical_join(
    *, workspace_id: int, draft: Scd2SqlDraft,
    binding: InternalDraftBinding, specification: Scd2JoinSpecification,
    fact: MetricWarehouseSource, dimension: MetricWarehouseSource,
) -> CompiledQuery:
    """Build deterministic half-open temporal SQL from independently pinned metadata."""
    if type(workspace_id) is not int or workspace_id < 1:
        raise ValueError("Positive workspace ID required")
    if (type(draft) is not Scd2SqlDraft or draft.executable is not False
            or type(binding) is not InternalDraftBinding or binding.executable is not False
            or type(specification) is not Scd2JoinSpecification
            or specification.executable is not False
            or type(fact) is not MetricWarehouseSource
            or type(dimension) is not MetricWarehouseSource):
        raise ValueError("Reviewed non-executable SCD2 contracts required")
    if (specification.workspace_id != workspace_id
            or draft.workspace_id != workspace_id
            or specification.cardinality != "MANY_TO_ONE"
            or fact.model_type != "FACT" or dimension.model_type != "DIMENSION"
            or len(binding.models) != 2
            or len(binding.columns) != len(draft.projected_columns)
            or not 1 <= len(binding.columns) <= 32
            or type(draft.row_limit) is not int or not 1 <= draft.row_limit <= 500):
        raise ValueError("SCD2 workspace, model, and projection scope mismatch")
    expected_ids=(
        specification.fact_model_id,specification.fact_model_version_id,
        specification.dimension_model_id,specification.dimension_model_version_id,
    )
    if expected_ids != (
        draft.fact_model_id,draft.fact_model_version_id,
        draft.dimension_model_id,draft.dimension_model_version_id,
    ):
        raise ValueError("Historical draft model versions differ")
    for source, model in zip((fact,dimension),binding.models):
        for name in (
            "model_id","model_version_id","resource_id","generation",
            "publication_id","publication_revision","serving_materialization_id",
            "schema_sha256","serving_sha256","physical_table",
        ):
            if getattr(source,name)!=getattr(model,name):
                raise ValueError("Historical publication or serving receipt changed")

    temporal=specification.temporal
    if (type(temporal) is not Scd2TemporalPredicate
            or not temporal.lower_inclusive or not temporal.upper_exclusive
            or not temporal.null_upper_is_unbounded
            or temporal.valid_from != ColumnRef("DIMENSION","valid_from")
            or temporal.valid_to != ColumnRef("DIMENSION","valid_to")
            or temporal.event_time.relation != "FACT"
            or temporal.event_time.column != fact.event_time_column
            or fact.event_time_interpretation != "UTC"
            or fact.event_time_timezone is not None):
        # Source-timezone conversion requires a separate, explicit policy
        # and is deliberately outside this first UTC-only compiler.
        raise ValueError("Unsupported or changed SCD2 temporal semantics")

    def columns(source):
        mapping={}
        for logical,physical,kind in source.columns:
            if (not isinstance(logical,str) or not _LOGICAL.fullmatch(logical)
                    or not isinstance(physical,str) or not _PHYSICAL.fullmatch(physical)
                    or kind not in _KEYS or logical in mapping):
                raise ValueError("Invalid published physical column mapping")
            mapping[logical]=(physical,kind)
        return mapping

    fc,dc=columns(fact),columns(dimension)
    event=fc.get(temporal.event_time.column)
    if not event or event[1] not in _TIME or not dimension.business_keys:
        raise ValueError("Typed SCD2 event time and dimension business keys required")

    # The independent provider metadata gate must verify that valid_from and
    # valid_to really are TIMESTAMP columns with the approved schema contract.
    # This initial compiler supports only UTC TIMESTAMP SCD2 time semantics.
    if event[1] != "TIMESTAMP":
        raise ValueError("Only UTC TIMESTAMP SCD2 compilation is currently supported")
    if (not 1 <= len(specification.equality_keys) <= 32
            or len(specification.equality_keys) != len(dimension.business_keys)):
        raise ValueError("Complete SCD2 dimension business key mapping required")
    actual_dim_keys=[]
    predicates=[]
    for pair in specification.equality_keys:
        if (type(pair) is not Equality
                or pair.fact.relation != "FACT" or pair.dimension.relation != "DIMENSION"
                or pair.fact.column not in fc or pair.dimension.column not in dc):
            raise ValueError("Invalid typed historical equality keys")
        left,right=fc[pair.fact.column],dc[pair.dimension.column]
        if left[1]!=right[1] or left[1] not in _KEYS:
            raise ValueError("Historical join business key types differ")
        actual_dim_keys.append(pair.dimension.column)
        predicates.append(f"f.`{left[0]}` = d.`{right[0]}`")
    if (len(set(actual_dim_keys)) != len(actual_dim_keys)
            or set(actual_dim_keys) != set(dimension.business_keys)):
        raise ValueError("Incomplete or duplicate SCD2 business keys")

    # The validator must still match the precise reviewed logical predicate
    # AST, not merely the submitted projection list.
    catalog={
        "workspace_id":workspace_id,
        "models":[{
            "model_id":s.model_id,"model_version_id":s.model_version_id,
            "model_type":s.model_type,"status":"APPROVED",
            "columns":[{"name":name,"role":"ATTRIBUTE"} for name,_,_ in s.columns],
        } for s in (fact,dimension)],
    }
    checked=validate_scd2_sql_draft(
        draft.normalized_sql,specification=specification,catalog=catalog,
    )
    if checked != draft:
        raise ValueError("SCD2 logical draft was changed after validation")

    projections=[]
    for (alias,logical),col in zip(draft.projected_columns,binding.columns):
        if alias not in ("f","d"):
            raise ValueError("Unknown SCD2 projection alias")
        physical=(fc if alias=="f" else dc).get(logical)
        if (physical is None or
                (col.alias,col.logical_name,col.physical_name,col.physical_type)
                != (alias,logical,*physical)):
            raise ValueError("SCD2 projected physical column changed")
        projections.append(f"{alias}.`{physical[0]}` AS `{alias}_{logical}`")

    predicates.extend((
        f"f.`{event[0]}` >= d.`valid_from`",
        f"(f.`{event[0]}` < d.`valid_to` OR d.`valid_to` IS NULL)",
    ))
    sql=(
        f"SELECT {', '.join(projections)} "
        f"FROM `{binding.models[0].physical_table}` AS f "
        f"INNER JOIN `{binding.models[1].physical_table}` AS d "
        f"ON {' AND '.join(predicates)} LIMIT {draft.row_limit}"
    )
    identity={
        "domain":"datarise-warehouse-scd2-utc-v1",
        "workspace":workspace_id,
        "specification":{
            "workspace_id":specification.workspace_id,
            "models":expected_ids,
            "keys":[(x.fact.column,x.dimension.column)
                    for x in specification.equality_keys],
            "event_time":temporal.event_time.column,
            "temporal":"[valid_from,valid_to)",
        },
        "draft":draft.normalized_sql,
        "models":[vars(m) for m in binding.models],
        "sources":[s.pin() for s in (fact,dimension)],
        "projections":[vars(c) for c in binding.columns],
        "sql":sql,
    }
    payload=json.dumps(identity,sort_keys=True,separators=(",",":")).encode()
    intent=hashlib.sha256(payload).hexdigest()
    token=hashlib.sha256(
        b"datarise-warehouse-query-token-v1:"+bytes.fromhex(intent)
    ).hexdigest()
    return CompiledQuery(
        workspace_id=workspace_id,request_token=token,intent_sha256=intent,
        sql=sql,physical_tables=tuple(m.physical_table for m in binding.models),
        compiler_version=2,
    )
