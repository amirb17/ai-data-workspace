"""Fail-closed DuckDB AST policy. User SQL is never sent to PostgreSQL."""
from dataclasses import dataclass
from sqlglot import exp
from sqlglot.dialects.duckdb import DuckDB
from sqlglot.errors import ParseError, OptimizeError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope, Scope
from app.processing.sql_policy import LIMITS, FUNCTIONS


class SqlBlocked(ValueError):
    def __init__(self, code, message, line=None, column=None):
        self.code, self.message, self.line, self.column = code, message, line, column
        super().__init__(code)

    def diagnostic(self):
        return dict(code=self.code, message=self.message, line=self.line, column=self.column)


@dataclass(frozen=True)
class SqlPlan:
    sql: str
    dataset_ids: tuple
    relationships: tuple
    warnings: tuple


# Only structural expression nodes below, plus explicitly approved functions. Unknown future
# parser node classes fail closed rather than gaining runtime privileges on dependency updates.
STRUCTURE = set('Select From Table TableAlias Identifier Column Star Alias Literal Null Boolean '
    'Where Group Having Order Ordered Limit Offset Distinct Join With CTE Subquery Paren '
    'EQ NEQ GT GTE LT LTE And Or Not Is In Between Like ILike Add Sub Mul Div Mod Neg '
    'Case If When Else Window WindowSpec DataType DataTypeParam Interval Var Cast TryCast '
    'Filter Escape'.split())
FUNCTION_CLASSES = set('Count Sum Avg Min Max Abs Round Ceil Floor Lower Upper Length Trim '
    'Coalesce Nullif Concat Substring Replace Cast TryCast Extract TimestampTrunc DateTrunc '
    'RowNumber Rank DenseRank Lag Lead FirstValue LastValue'.split())
TYPES = set('BOOLEAN TINYINT SMALLINT INT INTEGER BIGINT HUGEINT UTINYINT USMALLINT UINT UBIGINT '
            'FLOAT DOUBLE DECIMAL VARCHAR TEXT CHAR DATE TIME TIMESTAMP TIMESTAMPTZ INTERVAL UNKNOWN'.split())


class ReadOnlyParser(DuckDB.Parser):
    def _warn_unsupported(self):
        # SQLGlot's default Command fallback logs SQL text. Reject that fallback before
        # its warning can expose literals; all accepted queries still use the real AST.
        raise SqlBlocked('READ_ONLY','Unsupported command. Use one read-only SELECT statement.')


def parse(text):
    if not text.strip() or len(text) > LIMITS.text_length:
        raise SqlBlocked('TEXT_LIMIT', 'Enter a query within the SQL text limit.')
    try:
        dialect=DuckDB()
        statements = ReadOnlyParser(dialect=dialect).parse(dialect.tokenize(text),text)
    except (ParseError, RecursionError) as exc:
        location = exc.errors[0] if isinstance(exc, ParseError) and exc.errors else {}
        raise SqlBlocked('SYNTAX', 'SQL syntax could not be parsed. Check the indicated location.',
                         location.get('line'), location.get('col')) from None
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise SqlBlocked('READ_ONLY', 'Only one read-only SELECT statement, optionally with CTEs, is supported.')
    tree = statements[0]
    nodes = list(tree.walk())
    if len(nodes) > LIMITS.ast_nodes or max((n.depth for n in nodes), default=0) > LIMITS.depth:
        raise SqlBlocked('COMPLEXITY', 'This query exceeds the supported expression complexity.')
    for node in nodes:
        kind = type(node).__name__
        if kind not in STRUCTURE and kind not in FUNCTION_CLASSES:
            raise SqlBlocked('UNSUPPORTED', 'This SQL operation or function is not supported by the read-only policy.')
        if isinstance(node, exp.Anonymous):
            raise SqlBlocked('FUNCTION', 'Only supported analytical functions are available.')
        if isinstance(node, exp.DataType) and node.this.value not in TYPES:
            raise SqlBlocked('TYPE', 'This SQL type is not supported.')
        if isinstance(node, exp.With) and node.args.get('recursive'):
            raise SqlBlocked('UNSUPPORTED', 'Recursive queries are not supported.')
        if isinstance(node, exp.Join) and (node.args.get('using') or node.args.get('method')):
            raise SqlBlocked('JOIN', 'Use explicit equality keys for a confirmed child-to-parent join.')
        if isinstance(node, (exp.Limit, exp.Offset)):
            value = node.expression
            if not isinstance(value, exp.Literal) or value.is_string or not value.this.isdigit():
                raise SqlBlocked('LIMIT', 'LIMIT and OFFSET must be non-negative integer literals.')
        if isinstance(node, exp.Table):
            if not isinstance(node.this, exp.Identifier) or node.catalog or node.db not in ('', 'workspace'):
                raise SqlBlocked('NAMESPACE', 'Use only logical datasets in this workspace; external sources and catalogs are unavailable.')
            node.set('db', None)
    return tree


def validate(text, catalog):
    tree = parse(text)
    datasets = {d['alias']: d for d in catalog['datasets']}
    ids = set()
    for scope in traverse_scope(tree):
        for table in scope.tables:
            if isinstance(scope.sources.get(table.alias_or_name), Scope): continue
            dataset = datasets.get(table.name)
            if not dataset:
                raise SqlBlocked('DATASET', f'Dataset "{table.name}" is not available in this workspace.')
            if not dataset['queryable']:
                raise SqlBlocked('REFRESH_REQUIRED', f'{dataset["name"]} analytics and profile need refresh before this query can run.')
            ids.add(dataset['dataset_id'])
    if len(ids) > LIMITS.datasets:
        raise SqlBlocked('DATASET_LIMIT', 'V1 supports at most two datasets per query.')
    schema = {name: {c['name']: 'UNKNOWN' for c in d['columns'] if c['queryable']}
              for name, d in datasets.items() if d['queryable']}
    try:
        tree = qualify(tree, dialect='duckdb', schema=schema, validate_qualify_columns=True)
    except OptimizeError:
        raise SqlBlocked('COLUMN', 'A column is unavailable, sensitive, or ambiguous. Check current dataset columns and qualify ambiguous names.') from None
    relations = []
    for scope in traverse_scope(tree):
        joins = scope.expression.args.get('joins', [])
        if not joins: continue
        if len(joins) != 1 or relations:
            raise SqlBlocked('JOIN', 'V1 supports one direct confirmed child-to-parent lookup.')
        join = joins[0]
        if join.args.get('method') or join.args.get('side') not in (None, '', 'LEFT') or join.args.get('kind') not in (None, '', 'INNER') or join.args.get('using'):
            raise SqlBlocked('JOIN', 'Use an INNER or LEFT equality join matching a confirmed relationship.')
        on = join.args.get('on')
        if not isinstance(on, exp.EQ) or not all(isinstance(v, exp.Column) for v in (on.left, on.right)):
            raise SqlBlocked('JOIN', 'Join keys must be a direct equality matching a confirmed relationship.')
        mapping = {name: datasets.get(value.name) for name,value in scope.sources.items() if isinstance(value, exp.Table)}
        left, right = mapping.get(on.left.table), mapping.get(on.right.table)
        base = scope.expression.args.get('from_')
        if not base or not isinstance(base.this, exp.Table) or not isinstance(join.this, exp.Table) or not left or not right:
            raise SqlBlocked('JOIN', 'Joins through derived queries are outside the confirmed-lookup policy.')
        base_dataset, target = mapping.get(base.this.alias_or_name), mapping.get(join.this.alias_or_name)
        match = None
        for relation in catalog['relationships']:
            p, c = relation['parent'], relation['child']
            endpoints = {(left['dataset_id'], on.left.name), (right['dataset_id'], on.right.name)}
            if (len(p['columns']) == len(c['columns']) == 1 and relation['cardinality'] in ('ONE_TO_ONE','ONE_TO_MANY')
                and base_dataset and target and base_dataset['dataset_id'] == c['dataset_id'] and target['dataset_id'] == p['dataset_id']
                and endpoints == {(p['dataset_id'],p['columns'][0]),(c['dataset_id'],c['columns'][0])}):
                match = relation; break
        if not match:
            raise SqlBlocked('JOIN', 'This join does not match a current confirmed child-to-parent relationship. Parent-to-child fanout is blocked.')
        relations.append(match)
    if len(ids)>1 and not relations:
        raise SqlBlocked('JOIN','Multiple datasets require one current confirmed direct child-to-parent join.')
    return SqlPlan(tree.sql(dialect='duckdb'), tuple(sorted(ids)), tuple(relations),
                   ('User-authored SQL; this result is not a trusted metric.',))


def format_sql(text):
    return parse(text).sql(dialect='duckdb', pretty=True, comments=True)
