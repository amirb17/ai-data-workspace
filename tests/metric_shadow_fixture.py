"""Cloud-free SQL/SDK fixtures, independent of the Pandas metric evaluator.

DuckDB executes SQLGlot's translation of the actual emitted GoogleSQL. It is a
bounded local SQL oracle, not a claim of live BigQuery acceptance. Native decimal
division uses explicit independently specified reference values in its tests:
DuckDB's / returns DOUBLE, whereas BigQuery BIGNUMERIC / returns Decimal.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from decimal import Decimal

import duckdb
import pandas as pd
import sqlglot
from sqlglot import exp

from app.processing.metric_execution import MetricExecutionPlan
from app.processing.metric_warehouse_compiler import MetricWarehouseSource
from app.processing.relationship_evidence import digest
from app.query_governance.providers.bigquery import BigQueryReadProvider
from app.services.metric_shadow_service import MetricShadowService
from app.warehouse.providers.base import Policy
from tests.test_query_bigquery_provider import Client, Table

USER = {'user_id':1,'owner_key':'shadow-test'}


def definition(op='SUM', *, groups=(), bucket=None, join=False, entity=False, scale=1):
    column=lambda name: dict(id=name,op='COLUMN',column=dict(dataset_id=10,column=name))
    if op=='COUNT':
        nodes=[dict(id='value',op='COUNT')]
    elif op=='MULTIPLY':
        nodes=[column('x'),column('y'),dict(id='product',op='MULTIPLY',args=['x','y']),
               dict(id='value',op='SUM',args=['product'])]
    elif op=='DIVIDE':
        nodes=[column('x'),column('y'),dict(id='sx',op='SUM',args=['x']),
               dict(id='sy',op='SUM',args=['y']),dict(id='value',op='DIVIDE',args=['sx','sy'])]
    else:
        nodes=[column('x'),dict(id='value',op=op,args=['x'])]
    return dict(name='shadow fixture',description='Synthetic parity only',
        metric_type=('PERCENTAGE' if scale==100 else 'RATIO') if op=='DIVIDE' else 'SUM' if op=='MULTIPLY' else op,
        base_dataset_id=10,base_entity='synthetic',grain='ENTITY' if entity else 'RECORD',
        grain_keys=['id'] if entity else [],formula=dict(nodes=nodes,root='value',scale=scale),
        dimensions=[dict(dataset_id=11 if join and name=='region' else 10,column=name) for name in groups],
        time_field=dict(dataset_id=10,column='event') if bucket else None,time_bucket=bucket,
        relationship_ids=[9] if join else [],default_filters=[],
        unit=('PERCENTAGE' if scale==100 else 'RATIO') if op=='DIVIDE' else 'NUMBER',
        confidence=1,rationale='Synthetic',unresolved_assumptions=[])


def plan(op='SUM', **kwargs):
    d=definition(op,**kwargs)
    join=kwargs.get('join',False)
    pins=({'dataset_id':10,'state_id':100,'gold_run_id':200,'structural_key':'s','semantic_key':'m'},)
    joins=()
    if join:
        pins+=({'dataset_id':11,'state_id':110,'gold_run_id':210,'structural_key':'t','semantic_key':'n'},)
        joins=(dict(relationship_id=9,version=1,child=dict(dataset_id=10,columns=['parent']),
                    parent=dict(dataset_id=11,columns=['parent']),cardinality='ONE_TO_MANY'),)
    return MetricExecutionPlan(1,7,d,pins,joins,digest([d,pins,joins]))


def sources(*, numeric='INT64', timezone='UTC', time_kind='TIMESTAMP', join=False):
    fact=MetricWarehouseSource(dataset_id=10,state_id=100,model_id=20,model_version_id=30,
        resource_id=40,generation=1,publication_id=50,publication_revision=1,
        serving_materialization_id=60,serving_sha256='a'*64,serving_row_count=100,
        physical_table='synthetic-project.dr_dev_w1.m20_v30_g1_p50_r1',
        columns=(('id','c0','STRING'),('x','c1',numeric),('y','c2',numeric),('category','c3','STRING'),
                 ('region','c4','STRING'),('event','c5',time_kind),('parent','c6','STRING')),
        grain_keys=('id',),configuration_sha256='b'*64,source_vector_sha256='c'*64,
        schema_sha256='d'*64,event_time_column='event',
        event_time_interpretation='UTC' if timezone=='UTC' else 'SOURCE_TIMEZONE',
        event_time_timezone=None if timezone=='UTC' else timezone)
    result={10:fact}
    if join:
        result[11]=MetricWarehouseSource(dataset_id=11,state_id=110,model_id=21,model_version_id=31,
            resource_id=41,generation=1,publication_id=51,publication_revision=1,
            serving_materialization_id=61,serving_sha256='e'*64,serving_row_count=10,
            physical_table='synthetic-project.dr_dev_w1.m21_v31_g1_p51_r1',
            columns=(('parent','c0','STRING'),('region','c1','STRING')),grain_keys=(),business_keys=('parent',),
            configuration_sha256='f'*64,source_vector_sha256='1'*64,schema_sha256='2'*64,model_type='DIMENSION')
    return result


def frame(values, *, ys=None, categories=None, regions=None, dates=None, numeric='INT64'):
    n=len(values)
    return pd.DataFrame(dict(id=[f'I{i}' for i in range(n)],
        x=pd.array(values,dtype='Int64') if numeric=='INT64' else pd.Series(values,dtype=object),
        y=pd.array(ys if ys is not None else [2]*n,dtype='Int64') if numeric=='INT64'
          else pd.Series(ys if ys is not None else [Decimal(2)]*n,dtype=object),
        category=categories if categories is not None else ['A']*n,
        region=regions if regions is not None else ['East']*n,
        event=pd.to_datetime(dates if dates is not None else ['2026-01-01T00:00:00Z']*n,utc=True),
        parent=['P1']*n))


def sql_rows(sql, warehouse_sources, frames, *, decimal_reference=None, physical=None):
    tree=sqlglot.parse_one(sql,read='bigquery')
    names={s.physical_table.rsplit('.',1)[1]:f's{dataset}' for dataset,s in warehouse_sources.items()}
    for table in list(tree.find_all(exp.Table)):
        table.replace(exp.Table(this=exp.Identifier(this=names[table.name]),
                                alias=table.args.get('alias')))
    # The local oracle has 38-digit Decimal capacity; all generic SQL matrix
    # inputs fit that explicit envelope. Production BIGNUMERIC limits stay intact.
    for data_type in list(tree.find_all(exp.DataType)):
        if data_type.sql(dialect='bigquery')=='BIGNUMERIC':
            scale=12 if any(t=='BIGNUMERIC' for s in warehouse_sources.values() for _,_,t in s.columns) else 0
            data_type.replace(exp.DataType.build(f'DECIMAL(38,{scale})'))
    kinds=dict(STRING='VARCHAR',INT64='BIGINT',BIGNUMERIC='DECIMAL(38,12)',
               BOOL='BOOLEAN',DATE='DATE',TIMESTAMP='TIMESTAMPTZ')
    with duckdb.connect() as conn:
        for dataset,source in warehouse_sources.items():
            extra='is_active BOOLEAN' if source.model_type=='FACT' else 'is_current BOOLEAN, member_sk VARCHAR'
            columns=', '.join(f'{physical} {kinds[kind]}' for _,physical,kind in source.columns)
            conn.execute(f'CREATE TABLE s{dataset} ({columns}, {extra})')
            data=[]
            if physical is not None:
                for raw in physical[dataset]:
                    values=[raw[p] for _,p,_ in source.columns]
                    values += [raw['is_active']] if source.model_type=='FACT' else [raw['is_current'],raw['member_sk']]
                    data.append(tuple(values))
            for index,row in enumerate(frames[dataset].to_dict('records')):
                if physical is not None: break
                values=[]
                for logical,_,_ in source.columns:
                    value=row[logical]
                    if pd.isna(value): value=None
                    elif isinstance(value,pd.Timestamp): value=value.to_pydatetime()
                    values.append(value)
                values += [True] if source.model_type=='FACT' else [True,f'member{index}']
                data.append(tuple(values))
            if data:
                conn.executemany(f'INSERT INTO s{dataset} VALUES ({",".join("?" for _ in data[0])})',data)
        result=conn.execute(tree.sql(dialect='duckdb'))
        fields=[d[0] for d in result.description]
        rows=[dict(zip(fields,row)) for row in result.fetchall()]
    if decimal_reference is not None:
        assert len(rows)==1 and 'CASE WHEN' in sql
        rows[0]['__metric_value']=decimal_reference[0]
    return tuple(rows)


class QueryClient(Client):
    def __init__(self, warehouse_sources, frames, *, decimal_reference=None, physical=None):
        super().__init__()
        self.sources,self.frames=warehouse_sources,frames
        self.references=tuple(s.physical_table for s in warehouse_sources.values())
        self.tables={}
        self.estimate=1
        self.decimal_reference=decimal_reference
        self.physical=physical
        self.lose_response=False
        self.mutate_rows=None
        for source in warehouse_sources.values():
            table=Table(source.physical_table)
            table.labels.update(model=str(source.model_id),generation=str(source.generation),
                publication=str(source.publication_id),revision=str(source.publication_revision))
            self.tables[source.physical_table]=table

    def get_table(self,name,**kwargs):
        self.calls.append(('get_table',name,kwargs))
        return deepcopy(self.tables[name])

    def query(self,sql,*,job_config,**kwargs):
        if not job_config.dry_run:
            self.rows=sql_rows(sql,self.sources,self.frames,decimal_reference=self.decimal_reference,physical=self.physical)
            if self.mutate_rows: self.rows=self.mutate_rows(self.rows)
        job=super().query(sql,job_config=job_config,**kwargs)
        if not job_config.dry_run and self.lose_response:
            self.lose_response=False
            raise ConnectionError('synthetic committed query response lost')
        return job


class Governance:
    workspace_id=1
    def __init__(self):
        self.row=None; self.calls=[]; self.admitted=True; self.fail_complete=False
    def admit(self,**kwargs):
        self.calls.append(('admit',kwargs))
        assert kwargs['consumer']=='METRIC'
        if not self.admitted: return {'decision':'REVIEW_REQUIRED','reservation':None}
        if self.row is None:
            self.row=dict(reservation_id=1,workspace_id=1,consumer='METRIC',provider='BIGQUERY',
                request_token=kwargs['request_token'],intent_sha256=kwargs['intent_sha256'],
                reserved_bytes=kwargs['estimate'].bytes_processed,maximum_bytes_billed=256*1024*1024,
                output_rows=kwargs['requested_rows'],status='ACTIVE')
        assert self.row['request_token']==kwargs['request_token']
        return {'decision':'AUTO_EXECUTE','reservation':dict(self.row)}
    def claim(self,reservation_id):
        self.calls.append(('claim',reservation_id)); assert self.row['status']=='ACTIVE'
        self.row['status']='EXECUTING'; return dict(self.row)
    def reservation(self,reservation_id):
        assert reservation_id==self.row['reservation_id']; return dict(self.row)
    def release(self,reservation_id):
        self.calls.append(('release',reservation_id)); assert self.row['status']=='ACTIVE'
        self.row['status']='RELEASED'
    def complete(self,reservation_id,**kwargs):
        self.calls.append(('complete',reservation_id,kwargs))
        if self.fail_complete: raise RuntimeError('synthetic durable completion lost')
        self.row['status']='COMPLETED'; return dict(kwargs,reservation_id=reservation_id)
    def fail(self,reservation_id,**kwargs):
        self.calls.append(('fail',reservation_id,kwargs)); self.row['status']='FAILED'
        return kwargs


def service(warehouse_sources, frames, *, client=None, governance=None, decimal_reference=None):
    cloud=client or QueryClient(warehouse_sources,frames,decimal_reference=decimal_reference)
    gov=governance or Governance()
    provider=lambda:BigQueryReadProvider(Policy('synthetic-project',enabled=True),cloud)
    result=MetricShadowService(1,USER,source_resolver=lambda plan,user:warehouse_sources,
        governance_factory=lambda workspace,user:gov,provider_factory=provider)
    return result,cloud,gov
