"""Step 408C2: strict AST validation of a reviewed SCD2 historical join.

This validator emits a logical, non-executable draft. It does NOT resolve
physical BigQuery tables or authorize a query. An execution gateway must
independently verify current ownership, source state, model/relationship
versions, materialization receipts, provider attestation and budgets.
"""
from dataclasses import dataclass
from sqlglot import exp

from app.processing.sql_validation import SqlBlocked, parse
from app.warehouse.sql_scd2_specification import Scd2JoinSpecification

MAX_TEXT=4096
MAX_ROWS=500
MAX_COLUMNS=32
SAFE_NODES={
    exp.Select, exp.From, exp.Table, exp.TableAlias, exp.Identifier,
    exp.Column, exp.Join, exp.Limit, exp.Literal, exp.And, exp.Or,
    exp.EQ, exp.GTE, exp.LT, exp.Is, exp.Null, exp.Paren,
}


@dataclass(frozen=True)
class Scd2SqlDraft:
    normalized_sql: str
    workspace_id: int
    fact_model_id: int
    fact_model_version_id: int
    dimension_model_id: int
    dimension_model_version_id: int
    projected_columns: tuple[tuple[str, str], ...]
    row_limit: int
    executable: bool=False


def _reject(code: str, message: str):
    raise SqlBlocked(code,message)


def _unparen(node):
    while type(node) is exp.Paren:
        node=node.this
    return node


def _and_parts(node):
    node=_unparen(node)
    if type(node) is exp.And:
        return _and_parts(node.left)+_and_parts(node.right)
    return [node]


def _col(node, alias: str, column: str) -> bool:
    node=_unparen(node)
    return type(node) is exp.Column and node.table==alias and node.name==column and not node.is_star


def _equal_key(node, fact_col: str, dim_col: str) -> bool:
    node=_unparen(node)
    if type(node) is not exp.EQ:
        return False
    return ((_col(node.left,"f",fact_col) and _col(node.right,"d",dim_col))
            or (_col(node.left,"d",dim_col) and _col(node.right,"f",fact_col)))


def _lower(node, event: str) -> bool:
    node=_unparen(node)
    # Do not accept GT: the opening interval must include the valid_from instant.
    return (type(node) is exp.GTE
            and _col(node.left,"f",event)
            and _col(node.right,"d","valid_from"))


def _upper(node, event: str) -> bool:
    node=_unparen(node)
    if type(node) is not exp.Or:
        return False
    left,right=_unparen(node.left),_unparen(node.right)

    def less(expr):
        return (type(expr) is exp.LT
                and _col(expr.left,"f",event)
                and _col(expr.right,"d","valid_to"))

    def is_null(expr):
        return (type(expr) is exp.Is
                and _col(expr.this,"d","valid_to")
                and type(_unparen(expr.expression)) is exp.Null)
    # Both sides must appear, with no additional OR condition.
    return (less(left) and is_null(right)) or (less(right) and is_null(left))


def validate_scd2_sql_draft(
    sql_text: str, *,
    specification: Scd2JoinSpecification,
    catalog: dict,
) -> Scd2SqlDraft:
    if not isinstance(specification,Scd2JoinSpecification):
        raise ValueError("An explicit reviewed SCD2 specification is required")
    if specification.executable is not False or specification.cardinality!="MANY_TO_ONE":
        _reject("RELATIONSHIP","SCD2 relationship is not a non-executable many-to-one specification.")
    time=specification.temporal
    if (not time.lower_inclusive or not time.upper_exclusive
            or not time.null_upper_is_unbounded
            or (time.event_time.relation,time.valid_from.relation,time.valid_to.relation)
               != ("FACT","DIMENSION","DIMENSION")
            or time.valid_from.column!="valid_from" or time.valid_to.column!="valid_to"):
        _reject("TEMPORAL","Historical validity interval does not match the reviewed policy.")
    if not isinstance(catalog,dict) or catalog.get("workspace_id")!=specification.workspace_id:
        _reject("SCOPE","Warehouse catalog and SCD2 specification have different scopes.")
    models=catalog.get("models")
    if not isinstance(models,list):
        _reject("CATALOG","Approved model catalog is missing.")
    def model(mid,version,kind):
        found=[m for m in models if isinstance(m,dict) and type(m.get("model_id")) is int and m["model_id"]==mid]
        if len(found)!=1:
            _reject("MODEL","Pinned warehouse model was not uniquely found.")
        result=found[0]
        if result.get("model_version_id")!=version or result.get("status")!="APPROVED" or result.get("model_type")!=kind:
            _reject("VERSION","Warehouse model approval or version changed.")
        cols=result.get("columns")
        if not isinstance(cols,list) or not cols:
            _reject("COLUMN","Model column contract is missing.")
        names=[c.get("name") for c in cols if isinstance(c,dict)
               and c.get("role") in ("IDENTITY","MEASURE","ATTRIBUTE","TIME")
               and isinstance(c.get("name"),str)]
        if len(names)!=len(cols) or len(names)!=len(set(names)):
            _reject("COLUMN","Model columns are ambiguous or unapproved.")
        return set(names)
    fc=model(specification.fact_model_id,specification.fact_model_version_id,"FACT")
    dc=model(specification.dimension_model_id,specification.dimension_model_version_id,"DIMENSION")
    if time.event_time.column not in fc or not specification.equality_keys:
        _reject("RELATIONSHIP","Fact event time or reviewed join keys are unavailable.")
    expected=[]
    for key in specification.equality_keys:
        if ((key.fact.relation,key.dimension.relation)!=("FACT","DIMENSION")
                or key.fact.column not in fc or key.dimension.column not in dc
                or (key.fact.column,key.dimension.column) in expected):
            _reject("JOIN","Reviewed composite key mapping is invalid.")
        expected.append((key.fact.column,key.dimension.column))

    if not isinstance(sql_text,str) or not sql_text.strip() or len(sql_text)>MAX_TEXT:
        _reject("TEXT_LIMIT","Warehouse SQL must contain 1–4096 characters.")
    tree=parse(sql_text)
    if any(type(node) not in SAFE_NODES for node in tree.walk()):
        _reject("UNSUPPORTED","Only explicit SCD2 equality and historical validity predicates are allowed.")
    base=tree.args.get("from_")
    joins=tree.args.get("joins") or []
    if type(base) is not exp.From or len(joins)!=1 or type(joins[0]) is not exp.Join:
        _reject("JOIN","Exactly one reviewed historical lookup is required.")
    join=joins[0]
    if join.args.get("side") or join.args.get("method") or join.args.get("using") or join.args.get("kind") not in (None,"INNER"):
        _reject("JOIN","Only an INNER FACT-to-DIMENSION join is supported.")
    for table,mid,alias in ((base.this,specification.fact_model_id,"f"),
                            (join.this,specification.dimension_model_id,"d")):
        if (type(table) is not exp.Table or table.catalog or table.db
                or table.name!=f"model_{mid}" or table.alias!=alias):
            _reject("TABLE","Both models must use explicit logical IDs and f/d aliases.")
    predicates=_and_parts(join.args.get("on"))
    # Exactly one predicate per mapped key and exactly one lower and upper
    # boundary. No extra OR, inequalities, or redundant weakening clauses.
    if len(predicates)!=len(expected)+2:
        _reject("TEMPORAL","Historical join must contain every key and both validity boundaries.")
    remaining=list(predicates)
    for fact_col,dim_col in expected:
        matches=[p for p in remaining if _equal_key(p,fact_col,dim_col)]
        if len(matches)!=1:
            _reject("JOIN","Every approved composite key requires exactly one equality predicate.")
        remaining.remove(matches[0])
    if len(remaining)!=2 or sum(_lower(p,time.event_time.column) for p in remaining)!=1 or sum(_upper(p,time.event_time.column) for p in remaining)!=1:
        _reject("TEMPORAL","Historical join must use inclusive valid_from and exclusive or NULL valid_to.")
    if any(not (_lower(p,time.event_time.column) or _upper(p,time.event_time.column)) for p in remaining):
        _reject("TEMPORAL","Unsupported historical predicate.")

    selected=tree.expressions
    if not selected or len(selected)>MAX_COLUMNS or any(type(col) is not exp.Column for col in selected):
        _reject("COLUMN","Select 1–32 explicitly qualified FACT/DIMENSION columns.")
    projections=[]
    for col in selected:
        if col.is_star or col.table not in ("f","d") or col.name not in (fc if col.table=="f" else dc):
            _reject("COLUMN","Unknown or unqualified warehouse column.")
        projections.append((col.table,col.name))
    if len(projections)!=len(set(projections)):
        _reject("COLUMN","Duplicate projections are unavailable.")
    limit=tree.args.get("limit")
    if type(limit) is not exp.Limit or type(limit.expression) is not exp.Literal:
        _reject("LIMIT","A literal LIMIT is mandatory.")
    literal=limit.expression
    if literal.is_string or not literal.this.isascii() or not literal.this.isdigit() or not 1<=int(literal.this)<=MAX_ROWS:
        _reject("LIMIT","LIMIT must be between 1 and 500.")

    return Scd2SqlDraft(
        normalized_sql=tree.sql(dialect="bigquery"),
        workspace_id=specification.workspace_id,
        fact_model_id=specification.fact_model_id,
        fact_model_version_id=specification.fact_model_version_id,
        dimension_model_id=specification.dimension_model_id,
        dimension_model_version_id=specification.dimension_model_version_id,
        projected_columns=tuple(projections),
        row_limit=int(literal.this),
    )
