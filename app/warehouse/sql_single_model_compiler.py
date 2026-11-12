"""Step 412A: deterministic, internal-only, single-publication SQL compiler.

Never execute or authorize these bytes. A future gateway must re-resolve the
owned current binding, verify live provider metadata, and admit/claim a durable
query budget before dispatching to any execution provider.
"""
import hashlib
import json
import re

from app.query_governance.providers.base import CompiledQuery
from app.warehouse.sql_draft_planner import WarehouseSqlDraft, plan_draft
from app.warehouse.sql_physical_binding import InternalDraftBinding

_COLUMN = re.compile(r"^c(?:0|[1-9][0-9]*)$")
_LOGICAL = re.compile(r"^[a-zA-Z_][a-zA-Z_0-9]{0,127}$")


def compile_single_model_sql(
    *, workspace_id: int, draft: WarehouseSqlDraft,
    binding: InternalDraftBinding,
) -> CompiledQuery:
    """Compile ONLY verified v0 logical projections to immutable BigQuery SQL.

    No user-selected physical identifiers, expressions, free-form joins, or
    externally supplied request tokens are accepted. Catalog is used only to
    recheck the AST against the independently resolved binding, not to grant
    access. All identifiers are constrained and always quoted.
    """
    if type(workspace_id) is not int or workspace_id < 1:
        raise ValueError("Positive workspace required")
    if type(draft) is not WarehouseSqlDraft or draft.executable is not False:
        raise ValueError("Trusted non-executable single-table draft required")
    if type(binding) is not InternalDraftBinding or binding.executable is not False:
        raise ValueError("Internal non-executable publication binding required")
    if len(binding.models) != 1 or not 1 <= len(binding.columns) <= 32:
        raise ValueError("Exactly one published model and bounded projections required")
    model = binding.models[0]
    if (model.model_id,model.model_version_id) != (
            draft.model_id,draft.model_version_id):
        raise ValueError("Draft and approved published version differ")
    if (type(draft.row_limit) is not int or not 1 <= draft.row_limit <= 500
            or type(draft.projected_columns) is not tuple
            or len(draft.projected_columns) != len(binding.columns)
            or len(set(draft.projected_columns)) != len(draft.projected_columns)):
        raise ValueError("Bounded exact projection and limit required")
    if any(
        col.alias != "" or col.logical_name != logical
        or not isinstance(col.physical_name,str)
        or _COLUMN.fullmatch(col.physical_name) is None
        or _LOGICAL.fullmatch(logical) is None
        for logical,col in zip(draft.projected_columns,binding.columns)
    ):
        raise ValueError("Unsafe or non-matching logical/physical projection")
    # Reparse source SQL with the existing strict AST allowlist; require exact
    # normalized statement to reject hand-assembled altered draft objects.
    catalog = {
        "workspace_id": workspace_id,
        "models": [{
            "model_id": model.model_id,
            "model_version_id": model.model_version_id,
            "model_type": model.model_type,
            "status": "APPROVED",
            "columns": [
                {"name":name,"role":"ATTRIBUTE"} for name in draft.projected_columns
            ],
        }],
    }
    verified = plan_draft(draft.normalized_sql,workspace_id=workspace_id,catalog=catalog)
    if (verified.normalized_sql != draft.normalized_sql
            or verified.model_id != draft.model_id
            or verified.model_version_id != draft.model_version_id
            or verified.projected_columns != draft.projected_columns
            or verified.row_limit != draft.row_limit):
        raise ValueError("Logical warehouse draft was altered after validation")

    projection = ", ".join(
        f"`{col.physical_name}` AS `{col.logical_name}`"
        for col in binding.columns
    )
    sql = f"SELECT {projection} FROM `{model.physical_table}` LIMIT {draft.row_limit}"
    # CompiledQuery independently validates the physical identifier grammar,
    # scope and bounded SQL. The token covers ALL relevant immutable pins.
    fingerprint = {
        "domain":"datarise-warehouse-sql-single-v1",
        "workspace":workspace_id,"draft":draft.normalized_sql,
        "limit":draft.row_limit,
        "model":vars(model),
        "projections":[vars(col) for col in binding.columns],
        "sql":sql,
    }
    payload=json.dumps(fingerprint,sort_keys=True,separators=(",",":")).encode("utf-8")
    intent=hashlib.sha256(payload).hexdigest()
    request=hashlib.sha256(b"datarise-warehouse-query-token-v1:"+
                           bytes.fromhex(intent)).hexdigest()
    return CompiledQuery(
        workspace_id=workspace_id,request_token=request,
        intent_sha256=intent,sql=sql,
        physical_tables=(model.physical_table,),compiler_version=2,
    )
