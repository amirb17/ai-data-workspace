"""Structural validation and conservative decision policy; never runs SQL or source data."""
from app.processing.relationship_evidence import digest
from app.schemas.metrics import MetricProposal, ColumnRef

ALGORITHM_VERSION=3
POLICY_VERSION=3
NUMERIC={'INTEGER','DECIMAL','FLOAT'}

def validate_metric(proposal,source):
    m=MetricProposal.model_validate(proposal)
    datasets={x['pin']['dataset_id']:x for x in source['eligible']}
    columns={(i,c['original_name']):c for i,x in datasets.items() for c in x['profile']['columns']}
    roles={(i,c['column_name']):c for i,x in datasets.items() for c in x['suggestion']['reasoning']['columns']}
    errors=[]; questions=list(m.unresolved_assumptions); refs=set(); used_rel=[]; strong=True
    def ref(r,role=None,numeric=False):
        nonlocal strong
        key=(r.dataset_id,r.column);refs.add(key)
        if key not in columns:raise ValueError('Invented metric column or dataset')
        c=columns[key];s=roles[key]
        if numeric and c['canonical_type'] not in NUMERIC:errors.append('Numeric aggregation requires a numeric type.')
        if role and s['suggested_role'] not in role:errors.append('Column semantic role is incompatible.')
        if c.get('sensitivity_hints'):questions.append('Potentially sensitive field needs explicit business review.')
        if s['warnings']:questions.append('Column meaning has unresolved warnings.')
        if s['confidence']<.9:strong=False
        return c
    if m.base_dataset_id not in datasets:raise ValueError('Invented base dataset')
    base=datasets[m.base_dataset_id];summary=base['profile']['summary']
    if m.grain=='ENTITY':
        if not m.grain_keys or m.grain_keys!=summary['business_key']:errors.append('Entity grain requires the exact configured key.')
        for key in m.grain_keys:
            c=ref(ColumnRef(dataset_id=m.base_dataset_id,column=key))
            if c['distinct_ratio']!=1 or c['null_ratio']!=0:questions.append('Entity grain is not proven unique and null-free; composite discovery is deferred.')
    elif m.grain_keys:errors.append('Record grain cannot declare entity keys.')
    entity=base['suggestion']['reasoning']['entity']['primary']
    if entity['confidence']<.9 or source['workspace_reasoning']['overall_confidence']<.8:strong=False
    if m.base_entity!=entity['label']:questions.append('Business entity meaning requires review.')
    nodes={};kinds={};depth={};visited=set();agg={'COUNT','COUNT_DISTINCT','SUM','AVG','MIN','MAX'}
    filters=list(m.default_filters)
    for n in m.formula.nodes:
        if n.id in nodes or any(a not in nodes for a in n.args):errors.append('Formula must be an acyclic ordered graph.');continue
        nodes[n.id]=n;depth[n.id]=1+max((depth[a] for a in n.args),default=0)
        if depth[n.id]>6:errors.append('Formula depth exceeds V1 bounds.')
        filters.extend(n.filters)
        if n.filters and n.op not in agg:errors.append('Filters are allowed only on aggregations.')
        if n.op=='COLUMN':
            if n.column is None or n.args:errors.append('COLUMN requires one column and no operands.')
            else:
                ref(n.column)
                if n.column.dataset_id!=m.base_dataset_id:errors.append('Measures must belong to the base grain dataset.')
            kinds[n.id]='ROW'
        elif n.op=='COUNT':
            if n.column or n.args:errors.append('COUNT counts base records without column/operands.')
            kinds[n.id]='AGG'
        elif n.op=='MULTIPLY':
            if n.column or len(n.args)!=2 or any(kinds[a]!='ROW' for a in n.args):errors.append('MULTIPLY requires two row expressions.')
            kinds[n.id]='ROW'
        elif n.op in agg:
            if n.column or len(n.args)!=1 or any(kinds[a]!='ROW' for a in n.args):errors.append('Aggregation requires one row expression.')
            kinds[n.id]='AGG'
        else:
            if n.column or len(n.args)!=2 or any(kinds[a]!='AGG' for a in n.args):errors.append('DIVIDE requires two aggregate expressions.')
            kinds[n.id]='AGG'
    def walk(key,numeric=False,seen=None):
        seen=set() if seen is None else seen
        # A shared row operand must still be checked when another branch requires numeric meaning.
        if (key,numeric) in seen:return
        seen.add((key,numeric))
        if key not in nodes:return
        n=nodes[key];visited.add(key)
        if n.column and numeric:ref(n.column,{'MEASURE'},True)
        for a in n.args:walk(a,numeric or n.op in {'MULTIPLY','SUM','AVG','MIN','MAX'},seen)
    walk(m.formula.root)
    if m.formula.root not in nodes or kinds.get(m.formula.root)!='AGG' or visited!=set(nodes):errors.append('Formula needs an aggregate root and no unused nodes.')
    root=nodes.get(m.formula.root)
    expected='DIVIDE' if m.metric_type in {'RATIO','RATE','PERCENTAGE'} else m.metric_type
    if root and root.op!=expected:errors.append('Metric type does not match formula root.')
    if (m.metric_type=='PERCENTAGE')!=(m.formula.scale==100) or m.unit!=('PERCENTAGE' if m.metric_type=='PERCENTAGE' else 'RATIO' if expected=='DIVIDE' else 'NUMBER'):errors.append('Unit/scale does not match formula.')
    for f in filters:
        ref(f.column,{'STATUS','CATEGORY','DIMENSION','CODE','BOOLEAN_FLAG'})
        if f.column.dataset_id!=m.base_dataset_id:errors.append('V1 filters must belong to the base dataset.')
        questions.append('Category meaning/value is unresolved; no approved categorical dictionary is available.')
    for d in m.dimensions:ref(d,{'DIMENSION','CATEGORY','STATUS','CODE','BOOLEAN_FLAG'})
    if m.time_field:
        c=ref(m.time_field,{'TIME_DIMENSION'})
        if c['canonical_type'] not in {'DATE','DATETIME'} or not m.time_bucket:errors.append('Time grouping requires typed time evidence and an explicit bucket.')
    elif m.time_bucket:errors.append('Time bucket requires an explicit time field.')
    required={m.base_dataset_id}|{i for i,_ in refs}
    relationships={r['candidate_id']:r for r in source['relationships']}
    if len(set(m.relationship_ids))!=len(m.relationship_ids):errors.append('Repeated relationship references.')
    for rid in m.relationship_ids:
        if rid not in relationships:raise ValueError('Unconfirmed, stale or invented relationship')
        r=relationships[rid];e=r['evidence'];used_rel.append({'candidate_id':rid,'relationship_version':r['relationship_version']})
        for side in ('parent','child'):
            for column in e[side]['columns']:ref(ColumnRef(dataset_id=e[side]['dataset_id'],column=column))
        # Only a many/one-to-one fact-to-parent lookup preserves the base row grain in V1.
        if e['child']['dataset_id']!=m.base_dataset_id or e['candidate_cardinality'] not in {'ONE_TO_MANY','ONE_TO_ONE'}:errors.append('Join direction/cardinality can cause fanout or ambiguous grain.')
        if required!={e['parent']['dataset_id'],e['child']['dataset_id']}:errors.append('Relationship path is disconnected or unused.')
        if not e['can_confirm'] or e['signals']['overlap']['child_to_parent_coverage']!=1:errors.append('Relationship lacks complete safe coverage.')
    if len(required)>1 and len(m.relationship_ids)!=1:errors.append('Cross-dataset definitions require a confirmed V1 single-hop lookup.')
    if len(required)==1 and m.relationship_ids:errors.append('Unused relationship would alter grain.')
    # AI confidence never grants trust. Automatic acceptance requires a deterministic canonical
    # technical definition, in addition to strong current semantics and all safety gates.
    canonical_count=(m.metric_type=='COUNT' and root and root.op=='COUNT' and len(nodes)==1 and m.grain=='RECORD' and m.name=='Record count' and not m.dimensions and not m.time_field and not filters)
    semantic=base['suggestion']['reasoning']
    cautions=semantic.get('warnings',[])+semantic.get('unresolved_questions',[])+source['workspace_reasoning'].get('warnings',[])+source['workspace_reasoning'].get('unresolved_questions',[])
    if cautions:
        strong=False
    def expression(key):
        if key not in nodes:return '?'
        n=nodes[key]
        return n.column.column if n.op=='COLUMN' and n.column else f"{n.op}({', '.join(expression(a) for a in n.args)})"
    # Optional review is for literal, transparent calculations. An arbitrary business label
    # cannot silently define profit, net/gross revenue, clinical outcomes or customer lifetime.
    transparent={expression(m.formula.root).casefold()}
    if m.metric_type=='COUNT' and m.grain=='RECORD':transparent.add('record count')
    if root and root.op in {'SUM','AVG','MIN','MAX','COUNT_DISTINCT'} and len(root.args)==1:
        operand=nodes.get(root.args[0])
        if operand and operand.op=='COLUMN' and operand.column:
            column=operand.column.column.replace('_',' ').casefold()
            prefix={'SUM':'total','AVG':'average','MIN':'minimum','MAX':'maximum','COUNT_DISTINCT':'distinct'}[root.op]
            for label in (column,operand.column.column.casefold()):
                transparent.add(f'{prefix} {label}'+(' count' if root.op=='COUNT_DISTINCT' else ''))
    if m.name.casefold() not in transparent:questions.append('Business definition requires explicit scope, units or category meaning.')
    status='INVALID' if errors else 'REVIEW_REQUIRED' if questions else 'VALID'
    decision='REVIEW_REQUIRED' if status!='VALID' else 'AUTO_ACCEPT' if canonical_count and strong and m.confidence>=.9 else 'REVIEW_RECOMMENDED'
    dependencies=[{'kind':'DATASET',**datasets[i]['pin']} for i in sorted(required)]
    dependencies += [{'kind':'RELATIONSHIP',**r} for r in used_rel]
    dependencies += [{'kind':'WORKSPACE','suggestion_id':source['workspace_suggestion_id']}]
    return {'definition':m.model_dump(),'validation_status':status,'decision':decision,'errors':sorted(set(errors)),
        'review_reasons':sorted(set(questions)),'dependencies':dependencies,'required_datasets':sorted(required),
        'required_columns':[{'dataset_id':i,'column':c} for i,c in sorted(refs)],'formula_key':digest({k:v for k,v in m.model_dump().items() if k in {'base_dataset_id','grain','grain_keys','formula','dimensions','time_field','time_bucket','default_filters','relationship_ids'}})}

def reconcile(evidence):
    """Duplicate proposals cannot hide assumptions or win through ordering."""
    import copy
    names={}
    for e in evidence:names.setdefault((e['definition']['base_dataset_id'],e['definition']['name'].casefold()),set()).add(e['formula_key'])
    unique={};severity={'VALID':0,'REVIEW_REQUIRED':1,'INVALID':2}
    for original in evidence:
        e=copy.deepcopy(original);key=e['formula_key']
        if len(names[(e['definition']['base_dataset_id'],e['definition']['name'].casefold())])>1:
            if e['validation_status']=='VALID':e['validation_status']='REVIEW_REQUIRED'
            e['decision']='REVIEW_REQUIRED';e['review_reasons'].append('Conflicting formulas for the same business metric name.')
        if key not in unique:unique[key]=e;continue
        old=unique[key]
        different=old['definition']['name'].casefold()!=e['definition']['name'].casefold()
        for field in ('errors','review_reasons'):old[field]=sorted(set(old[field]+e[field]))
        if severity[e['validation_status']]>severity[old['validation_status']]:old['validation_status']=e['validation_status']
        if different:
            if old['validation_status']=='VALID':old['validation_status']='REVIEW_REQUIRED'
            old['review_reasons'].append('Multiple business meanings proposed for one formula.')
        old['decision']='REVIEW_REQUIRED' if old['validation_status']!='VALID' else 'REVIEW_RECOMMENDED' if old['decision']!='AUTO_ACCEPT' or e['decision']!='AUTO_ACCEPT' else 'AUTO_ACCEPT'
    return list(unique.values())
