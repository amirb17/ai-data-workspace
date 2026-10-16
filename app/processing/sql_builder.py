"""Typed values become SQLGlot literal nodes, never concatenated SQL fragments."""
from datetime import datetime
from decimal import Decimal, InvalidOperation
from sqlglot import exp
from app.processing.sql_validation import SqlBlocked, validate


def build(request,catalog):
    dataset=next((d for d in catalog['datasets'] if d['alias']==request.dataset),None)
    if not dataset or not dataset['queryable']: raise SqlBlocked('REFRESH_REQUIRED','Choose a current dataset.')
    columns={c['name']:c for c in dataset['columns'] if c['queryable']}
    def field(name):
        if name not in columns: raise SqlBlocked('COLUMN','Choose a current available column.')
        return exp.column(name,table=dataset['alias'],quoted=True)
    def literal(value,column):
        if value is None: raise SqlBlocked('FILTER','Enter a filter value or choose Is null.')
        kind=columns[column]['type']
        if kind in ('INTEGER','DECIMAL'):
            try:
                number=Decimal(str(value))
                if not number.is_finite(): raise InvalidOperation
            except InvalidOperation: raise SqlBlocked('FILTER','Enter a finite numeric filter value.') from None
            return exp.Literal.number(str(number))
        if kind=='BOOLEAN':
            if str(value).lower() not in ('true','false'): raise SqlBlocked('FILTER','Choose true or false.')
            return exp.Boolean(this=str(value).lower()=='true')
        if kind in ('DATETIME','DATE'):
            try: datetime.fromisoformat(str(value).replace('Z','+00:00'))
            except ValueError: raise SqlBlocked('FILTER','Enter an ISO date or timestamp.') from None
        return exp.Literal.string(str(value))
    def aggregate(operation, name):
        if operation != 'COUNT' and not name:
            raise SqlBlocked('COLUMN', 'Choose a measure for this aggregation.')
        measure = field(name) if name else exp.Star()
        kind = columns[name]['type'] if name else None
        if operation in ('SUM', 'AVG') and kind not in ('INTEGER', 'DECIMAL'):
            raise SqlBlocked('TYPE', 'SUM and AVG require a numeric field.')
        if operation in ('MIN', 'MAX') and kind not in ('INTEGER', 'DECIMAL', 'DATETIME', 'DATE'):
            raise SqlBlocked('TYPE', 'MIN and MAX require numeric or date/time fields in Builder.')
        return exp.Count(this=exp.Distinct(expressions=[measure])) if operation == 'COUNT_DISTINCT' else getattr(exp, operation.title())(this=measure)

    has_aggregation = bool(request.aggregation)
    if request.selections:
        if request.columns or request.aggregation or request.measure:
            raise SqlBlocked('COLUMN', 'Use either selections or the legacy output configuration.')
        outputs, names = [], []
        has_aggregation = any(item.aggregation for item in request.selections)
        for item in request.selections:
            if item.aggregation:
                expression = aggregate(item.aggregation, item.column)
                name = item.alias or item.aggregation.lower() + '_' + (item.column or 'rows')
            else:
                expression = field(item.column)
                name = item.alias or item.column
                if has_aggregation and item.column not in request.grouping:
                    raise SqlBlocked('GROUP', 'Add each non-aggregated selection to GROUP BY.')
            if name in names:
                raise SqlBlocked('COLUMN', 'Output names must be unique. Choose a different alias.')
            names.append(name)
            outputs.append(exp.alias_(expression, name, quoted=True))
    elif request.aggregation:
        outputs = [field(n) for n in request.grouping] + [exp.alias_(aggregate(request.aggregation, request.measure), 'metric_value', quoted=True)]
        names = request.grouping + ['metric_value']
    else:
        if not request.columns: raise SqlBlocked('COLUMN','Select at least one output column.')
        outputs=[field(n) for n in request.columns];names=request.columns
    query=exp.select(*outputs).from_(exp.Table(this=exp.to_identifier(dataset['alias'],quoted=True)))
    if request.relationship_id:
        relation=next((r for r in catalog['relationships'] if r['relationship_id']==request.relationship_id and r['child']['dataset_id']==dataset['dataset_id']),None)
        if not relation: raise SqlBlocked('JOIN','Choose a current confirmed child-to-parent relationship.')
        parent=next(d for d in catalog['datasets'] if d['dataset_id']==relation['parent']['dataset_id'])
        query=query.join(exp.Table(this=exp.to_identifier(parent['alias'],quoted=True)),on=exp.EQ(this=field(relation['child']['columns'][0]),
            expression=exp.column(relation['parent']['columns'][0],table=parent['alias'],quoted=True)),join_type='LEFT')
    predicates=[]
    for f in request.filters:
        column=field(f.column);kind=columns[f.column]['type'];op=f.operator
        allowed={'EQ','NE','IS_NULL','IS_NOT_NULL'}|({'GT','GE','LT','LE','BETWEEN'} if kind in ('INTEGER','DECIMAL','DATETIME','DATE') else {'CONTAINS','STARTS'} if kind=='TEXT' else set())
        if op not in allowed: raise SqlBlocked('FILTER','This operator is incompatible with the selected column type.')
        if op in ('IS_NULL','IS_NOT_NULL'):
            predicate=exp.Is(this=column,expression=exp.Null())
            if op=='IS_NOT_NULL': predicate=exp.Not(this=predicate)
        elif op=='BETWEEN': predicate=exp.Between(this=column,low=literal(f.value,f.column),high=literal(f.end,f.column))
        elif op in ('CONTAINS','STARTS'):
            if f.value is None: raise SqlBlocked('FILTER','Enter a text filter value.')
            # Treat user wildcards literally; SQLGlot emits a quoted string, not syntax.
            value=str(f.value).replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
            pattern=('%' if op=='CONTAINS' else '')+value+'%'
            predicate=exp.Escape(this=exp.Like(this=column,expression=exp.Literal.string(pattern)),expression=exp.Literal.string('\\'))
        else: predicate=getattr(exp,{'EQ':'EQ','NE':'NEQ','GT':'GT','GE':'GTE','LT':'LT','LE':'LTE'}[op])(this=column,expression=literal(f.value,f.column))
        predicates.append(predicate)
    if predicates: query=query.where(exp.and_(*predicates))
    if request.grouping: query=query.group_by(*(field(n) for n in request.grouping))
    if request.sort_by:
        if request.sort_by not in names: raise SqlBlocked('SORT','Sort by a selected output field.')
        query=query.order_by(exp.Ordered(this=exp.column(request.sort_by,quoted=True),desc=request.descending))
    query=query.limit(request.limit)
    text=query.sql(dialect='duckdb',pretty=True)
    validate(text,catalog)
    return text
