"""Step 408B: reviewed SCD1 FACT -> DIMENSION JOIN draft, never executable.

Accepts only SELECT <qualified columns> FROM model_<fact> AS f
INNER JOIN model_<dimension> AS d ON equality [AND equality...] LIMIT N.
The relationship ID is a required explicit caller input. A relationship must
come from an already-authorized current snapshot, not arbitrary SQL text.

This is an AST-only modeling tool. Provider execution stays disabled and
publication/ownership checks must be repeated in any future execution gateway.
"""
from dataclasses import dataclass
from sqlglot import exp
from app.processing.sql_validation import SqlBlocked, parse

MAX_TEXT=4096
MAX_COLUMNS=32
MAX_ROWS=500
SAFE_NODES={exp.Select,exp.From,exp.Table,exp.TableAlias,exp.Identifier,
            exp.Column,exp.Limit,exp.Literal,exp.Join,exp.EQ,exp.And}


@dataclass(frozen=True)
class WarehouseJoinDraft:
    normalized_sql: str
    relationship_id: int
    fact_model_id: int
    fact_model_version_id: int
    dimension_model_id: int
    dimension_model_version_id: int
    projected_columns: tuple[tuple[str,str], ...]
    row_limit: int
    executable: bool=False


def _blocked(code, detail):
    raise SqlBlocked(code,detail)


def _table(table, expected, alias):
    if (type(table) is not exp.Table or table.catalog or table.db
            or table.name!=f"model_{expected}" or table.alias!=alias):
        _blocked("TABLE","Only explicit logical model IDs with table aliases are allowed.")


def _model(catalog, mid, expected):
    models=catalog.get("models")
    if not isinstance(models,list):
        _blocked("CATALOG","An approved model catalog is required.")
    selected=[m for m in models if isinstance(m,dict) and type(m.get("model_id")) is int and m["model_id"]==mid]
    if len(selected)!=1:
        _blocked("MODEL","Both relationship models must exist exactly once.")
    model=selected[0]
    if (model.get("model_type")!=expected or model.get("status")!="APPROVED"
            or type(model.get("model_version_id")) is not int or model["model_version_id"]<1):
        _blocked("MODEL","Only approved and versioned FACT/DIMENSION models are allowed.")
    cols=model.get("columns")
    if not isinstance(cols,list) or not cols:
        _blocked("COLUMN","Model column contracts are unavailable.")
    names=[c.get("name") for c in cols if isinstance(c,dict)
           and c.get("role") in ("IDENTITY","MEASURE","ATTRIBUTE","TIME")
           and isinstance(c.get("name"),str)]
    if len(names)!=len(cols) or len(names)!=len(set(names)):
        _blocked("COLUMN","Model column contracts are invalid or ambiguous.")
    return model,set(names)


def _equalities(node):
    if type(node) is exp.And:
        return _equalities(node.left)+_equalities(node.right)
    if type(node) is exp.EQ and type(node.left) is exp.Column and type(node.right) is exp.Column:
        return [(node.left,node.right)]
    _blocked("JOIN","Only direct column-to-column equality join keys are supported.")


def plan_join_draft(sql_text: str, *, workspace_id: int, catalog: dict,
                    relationships: list[dict], relationship_id: int) -> WarehouseJoinDraft:
    if type(workspace_id) is not int or workspace_id<1:
        raise ValueError("Invalid workspace ID")
    if type(relationship_id) is not int or relationship_id<1:
        raise ValueError("A reviewed relationship ID must be selected")
    if not isinstance(catalog,dict) or catalog.get("workspace_id")!=workspace_id:
        _blocked("SCOPE","Warehouse catalog scope is invalid.")
    if not isinstance(relationships,list):
        _blocked("RELATIONSHIP","Current reviewed relationships are required.")
    matching=[r for r in relationships if isinstance(r,dict)
              and r.get("relationship_id")==relationship_id
              and r.get("workspace_id")==workspace_id]
    if len(matching)!=1:
        _blocked("RELATIONSHIP","Relationship does not exist in this workspace's current reviewed snapshot.")
    relation=matching[0]
    if relation.get("cardinality")!="MANY_TO_ONE" or relation.get("temporal_policy")!="CURRENT_MEMBER":
        _blocked("TEMPORAL","Historical or unreviewed join semantics are not supported in this step.")
    fact_id=relation.get("fact_model_id")
    dimension_id=relation.get("dimension_model_id")
    fact,fc=_model(catalog,fact_id,"FACT")
    dimension,dc=_model(catalog,dimension_id,"DIMENSION")
    if (relation.get("fact_model_version_id")!=fact["model_version_id"]
            or relation.get("dimension_model_version_id")!=dimension["model_version_id"]):
        _blocked("VERSION","The reviewed relationship refers to superseded model versions.")
    if not isinstance(sql_text,str) or not sql_text.strip() or len(sql_text)>MAX_TEXT:
        _blocked("TEXT_LIMIT","Warehouse SQL must contain 1–4096 characters.")
    tree=parse(sql_text)
    if any(type(n) not in SAFE_NODES for n in tree.walk()):
        _blocked("UNSUPPORTED","Only simple projection, one INNER equality join and LIMIT are supported.")
    base=tree.args.get("from_")
    joins=tree.args.get("joins") or []
    if type(base) is not exp.From or len(joins)!=1 or type(joins[0]) is not exp.Join:
        _blocked("JOIN","Exactly one explicit reviewed FACT-to-DIMENSION join is required.")
    join=joins[0]
    if join.args.get("side") or join.args.get("method") or join.args.get("using"):
        _blocked("JOIN","Only INNER equality joins are supported.")
    if join.args.get("kind") not in (None,"INNER"):
        _blocked("JOIN","Only INNER equality joins are supported.")
    _table(base.this,fact_id,"f")
    _table(join.this,dimension_id,"d")

    pairs=relation.get("key_pairs")
    if not isinstance(pairs,list) or not 1<=len(pairs)<=32:
        _blocked("JOIN","Reviewed join key mappings are unavailable.")
    expected=[]
    for pair in pairs:
        if not isinstance(pair,dict) or set(pair)!={"fact_column","dimension_column"}:
            _blocked("JOIN","Reviewed key mapping is malformed.")
        left,right=pair["fact_column"],pair["dimension_column"]
        if left not in fc or right not in dc or (left,right) in expected:
            _blocked("JOIN","Reviewed join key is unavailable.")
        expected.append((left,right))
    seen=[]
    for left,right in _equalities(join.args.get("on")):
        if left.table=="f" and right.table=="d":
            seen.append((left.name,right.name))
        elif left.table=="d" and right.table=="f":
            seen.append((right.name,left.name))
        else:
            _blocked("JOIN","Join operands must be qualified FACT and DIMENSION columns.")
    if len(seen)!=len(expected) or len(set(seen))!=len(seen) or set(seen)!=set(expected):
        _blocked("JOIN","Every reviewed composite business key must be matched exactly.")

    selected=tree.expressions
    if not selected or len(selected)>MAX_COLUMNS or any(type(c) is not exp.Column for c in selected):
        _blocked("COLUMN","Select 1–32 explicitly qualified model columns.")
    projection=[]
    for c in selected:
        if c.is_star or (c.table=="f" and c.name not in fc) or (c.table=="d" and c.name not in dc) or c.table not in ("f","d"):
            _blocked("COLUMN","Columns must be qualified and belong to the corresponding approved model.")
        projection.append((c.table,c.name))
    if len(projection)!=len(set(projection)):
        _blocked("COLUMN","Duplicate projected columns are blocked.")

    limit=tree.args.get("limit")
    if type(limit) is not exp.Limit or type(limit.expression) is not exp.Literal:
        _blocked("LIMIT","An explicit literal LIMIT is mandatory.")
    literal=limit.expression
    if literal.is_string or not literal.this.isascii() or not literal.this.isdigit() or not 1<=int(literal.this)<=MAX_ROWS:
        _blocked("LIMIT","Query LIMIT must be between 1 and 500.")
    return WarehouseJoinDraft(
        normalized_sql=tree.sql(dialect="bigquery"),
        relationship_id=relationship_id,
        fact_model_id=fact_id,fact_model_version_id=fact["model_version_id"],
        dimension_model_id=dimension_id,dimension_model_version_id=dimension["model_version_id"],
        projected_columns=tuple(projection),row_limit=int(literal.this))
