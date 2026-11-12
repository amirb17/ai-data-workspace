"""Step 408A: deliberately minimal, non-executable warehouse SQL AST planner.

Supported grammar (v0): SELECT explicit_columns FROM model_<id> [AS alias]
LIMIT integer. No joins, aggregates, predicates, subqueries, wildcards, CTEs,
functions, physical tables, or user-selected provider identifiers.

This planner is NOT an authorization decision. A future execution gateway
must recheck ownership, source/publication pins, provider attestation and
query budgets atomically. Do not pass its output to a query engine yet.
"""
from dataclasses import dataclass

from sqlglot import exp

from app.processing.sql_validation import SqlBlocked, parse


MAX_TEXT = 4096
MAX_COLUMNS = 32
MAX_ROWS = 500
_ALLOWED_NODES = (
    exp.Select, exp.From, exp.Table, exp.TableAlias, exp.Identifier,
    exp.Column, exp.Limit, exp.Literal,
)


@dataclass(frozen=True)
class WarehouseSqlDraft:
    normalized_sql: str
    model_id: int
    model_version_id: int
    projected_columns: tuple[str, ...]
    row_limit: int
    executable: bool = False


def plan_draft(sql_text: str, *, workspace_id: int, catalog: dict) -> WarehouseSqlDraft:
    """Parse one logical table against the scoped approved catalog only.

    Catalog must be an already-authorized snapshot. This method has no DB
    access and must not be used to authorize execution.
    """
    if type(workspace_id) is not int or workspace_id < 1:
        raise ValueError("Invalid warehouse workspace")
    if not isinstance(catalog, dict) or catalog.get("workspace_id") != workspace_id:
        raise SqlBlocked("SCOPE", "Warehouse catalog does not match this workspace.")
    if not isinstance(sql_text, str) or not sql_text.strip() or len(sql_text) > MAX_TEXT:
        raise SqlBlocked("TEXT_LIMIT", "Warehouse SQL text must be between 1 and 4096 characters.")

    tree = parse(sql_text)
    # Explicitly reject anything beyond the tiny v0 grammar. Rejecting AST
    # classes is safer than inspecting user SQL with regex.
    if any(type(node) not in _ALLOWED_NODES for node in tree.walk()):
        raise SqlBlocked("UNSUPPORTED", "Only simple explicit column projections with LIMIT are supported.")
    if tree.args.get("distinct") or tree.args.get("joins") or tree.args.get("where"):
        raise SqlBlocked("UNSUPPORTED", "Joins, filters and DISTINCT are not supported yet.")
    base = tree.args.get("from_")
    if not isinstance(base, exp.From) or not isinstance(base.this, exp.Table):
        raise SqlBlocked("TABLE", "Select from one approved logical warehouse model.")
    table = base.this
    if table.db or table.catalog or not isinstance(table.this, exp.Identifier):
        raise SqlBlocked("NAMESPACE", "Physical tables and external namespaces are forbidden.")

    models = catalog.get("models")
    if not isinstance(models, list):
        raise SqlBlocked("CATALOG", "An approved model catalog is required.")
    matches = [model for model in models if isinstance(model, dict)
               and type(model.get("model_id")) is int
               and table.name == f'model_{model["model_id"]}']
    if len(matches) != 1:
        raise SqlBlocked("TABLE", "Unknown or ambiguous logical warehouse model.")
    model = matches[0]
    if (model.get("model_type") not in ("FACT", "DIMENSION")
            or model.get("status") != "APPROVED"
            or type(model.get("model_version_id")) is not int
            or model["model_version_id"] < 1):
        raise SqlBlocked("MODEL", "Only explicitly approved model versions can be planned.")

    columns = model.get("columns")
    if not isinstance(columns, list):
        raise SqlBlocked("COLUMN", "Approved warehouse columns are unavailable.")
    allowed = {col["name"] for col in columns
               if isinstance(col, dict) and isinstance(col.get("name"), str)
               and col.get("role") in ("IDENTITY","MEASURE","ATTRIBUTE","TIME")}
    if not allowed or len(allowed) != len(columns):
        raise SqlBlocked("COLUMN", "Approved warehouse columns are inconsistent.")

    selected = tree.expressions
    if not selected or len(selected) > MAX_COLUMNS or any(type(col) is not exp.Column for col in selected):
        raise SqlBlocked("COLUMN", "Select between 1 and 32 explicit warehouse columns.")
    alias = table.alias_or_name
    names = []
    for col in selected:
        if col.is_star or col.name not in allowed or (col.table and col.table != alias):
            raise SqlBlocked("COLUMN", "An unknown, wildcard or incorrectly qualified column was selected.")
        names.append(col.name)
    if len(set(names)) != len(names):
        raise SqlBlocked("COLUMN", "Duplicate projected columns are not supported.")

    limit = tree.args.get("limit")
    if not isinstance(limit, exp.Limit) or not isinstance(limit.expression, exp.Literal):
        raise SqlBlocked("LIMIT", "A literal row limit is mandatory.")
    value = limit.expression
    if value.is_string or not value.this.isascii() or not value.this.isdigit():
        raise SqlBlocked("LIMIT", "Row limit must be a positive decimal integer.")
    size = int(value.this)
    if not 1 <= size <= MAX_ROWS:
        raise SqlBlocked("LIMIT", "Warehouse planning permits at most 500 rows.")

    # The normalized statement still contains logical model_<id> names.
    # Never substitute provider paths in this module.
    return WarehouseSqlDraft(
        # Logical drafts are parsed and reparsed with the DuckDB dialect.
        # BigQuery's backtick quoting must not leak into this internal AST
        # round-trip; physical BigQuery SQL is generated independently.
        normalized_sql=tree.sql(dialect="duckdb"),
        model_id=model["model_id"],
        model_version_id=model["model_version_id"],
        projected_columns=tuple(names),
        row_limit=size,
    )
