"""Provider-neutral 11B contracts, literal identity vectors and isolated metadata."""
from datetime import date, datetime, timezone
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
import pytest
from fastapi import HTTPException
from pydantic import TypeAdapter, ValidationError

from app.schemas.warehouse import SourceMapping, VersionCreate
from app.schemas.warehouse_modeling import (ModelConfiguration, ModelingRequest, SourceSchema, TimePolicy)
from app.warehouse.keys import canonical_key, canonical_value, surrogate_key, model_surrogate_key
from app.warehouse.model_validation import validate_model, validate_source_policy
from app.warehouse.time_policy import normalize_time
from app.services import warehouse_modeling_service as modeling
from app.services import warehouse_model_service as models
from tests.test_warehouse_foundation import pg, draft

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = TypeAdapter(ModelConfiguration)
NAMESPACE = '00000000-0000-0000-0000-000000000001'
VECTORS = json.loads((ROOT/'tests/fixtures/warehouse_keys_v1.json').read_text(encoding='utf-8'))


def fact(**updates):
    return dict(model_type='FACT',grain=dict(description='One row per business event',identity_columns=['id','line']),**updates)


def dimension(**updates):
    value = dict(model_type='DIMENSION',business_keys=['id'],scd_type='SCD_TYPE_2',tracked_attributes=['segment'],
                 nonhistorical_attributes=['label'],effective_time=dict(column='effective',interpretation='UTC'))
    value.update(updates)
    return value


def schema():
    return SourceSchema(columns=tuple(dict(name=n,data_type=t,nullable=None) for n,t in (
        ('id','STRING'),('line','INTEGER'),('segment','STRING'),('label','STRING'),('effective','TIMESTAMP'),('amount','DECIMAL'),('float_id','FLOAT'))))


@pytest.mark.parametrize('payload',[fact(),dimension(),dimension(scd_type='SCD_TYPE_1',effective_time=None)])
def test_valid_fact_and_dimension_metadata(payload):
    config = ADAPTER.validate_python(payload)
    result = validate_model(config,schema())
    assert result['valid'] and result['identity_null_policy']=='REJECT'
    assert all(v is None for v in result['source_key_nullability'].values())

def test_fact_analytical_roles_are_explicit_and_backward_compatible():
    legacy = ADAPTER.validate_python(fact())

    assert legacy.measures == ()
    assert legacy.attributes == ()

    configured = ADAPTER.validate_python(
        fact(
            measures=['amount'],
            attributes=['segment', 'label'],
            event_time=dict(
                column='effective',
                interpretation='UTC',
            ),
        )
    )

    assert configured.measures == ('amount',)
    assert configured.attributes == (
        'segment',
        'label',
    )

    result = validate_model(
        configured,
        schema(),
    )

    assert result['valid']
    assert result['identity_columns'] == [
        'id',
        'line',
    ]
@pytest.mark.parametrize(
    'payload',
    [
        fact(measures=['amount', 'amount']),
        fact(attributes=['segment', 'segment']),

        # Grain identity cannot also carry another role.
        fact(measures=['id']),
        fact(attributes=['line']),

        # Analytical roles must be mutually exclusive.
        fact(
            measures=['amount'],
            attributes=['amount'],
        ),

        # Event time has its own semantic role.
        fact(
            attributes=['effective'],
            event_time=dict(
                column='effective',
                interpretation='UTC',
            ),
        ),

        # Measures are explicitly numeric.
        fact(measures=['segment']),
        fact(measures=['label']),

        # All configured roles must exist in the pinned schema.
        fact(measures=['missing']),
        fact(attributes=['missing']),
    ],
)
def test_fact_analytical_role_conflicts_are_rejected(payload):
    fact(measures=['float_id']),
    fact(attributes=['float_id']),
    with pytest.raises(ValueError):
        validate_model(
            ADAPTER.validate_python(payload),
            schema(),
        )
def test_fact_measure_must_be_exact_numeric():
    config = ADAPTER.validate_python(
        fact(measures=['float_id'])
    )

    with pytest.raises(
        ValueError,
        match='Fact measures must use exact numeric source columns',
    ):
        validate_model(
            config,
            schema(),
        )
@pytest.mark.parametrize('payload',[
    {},dict(grain=dict(description='Event',identity_columns=['id'])),
    {'model_type':'FACT'},dict(model_type='FACT',grain=dict(description='   ',identity_columns=['id'])),
    dict(model_type='FACT',grain=dict(description='Event',identity_columns=[])),
    dimension(effective_time=None),dimension(tracked_attributes=[]),dimension(business_keys=[]),
    dimension(scd_type='AUTOMATIC'),dimension(model_type='REFERENCE'),dimension(arbitrary={'sql':'no'}),
    dimension(history_coverage='FROM_DECLARED_START'),dimension(null_business_key_policy='UNKNOWN_MEMBER'),
    dimension(effective_time=dict(column='effective',interpretation='SOURCE_TIMEZONE')),
    dimension(effective_time=dict(column='effective',interpretation='UTC',timezone='UTC')),
    dimension(effective_time=dict(column='effective',interpretation='SOURCE_TIMEZONE',timezone='Bad/Timezone')),
])
def test_bad_or_ambiguous_typed_contract_is_rejected(payload):
    with pytest.raises(ValidationError):
        ADAPTER.validate_python(payload)


@pytest.mark.parametrize('payload',[
    dimension(business_keys=['id','id']),dimension(tracked_attributes=['segment','segment']),
    dimension(nonhistorical_attributes=['segment']),dimension(tracked_attributes=['id']),
    dimension(tracked_attributes=['effective']),dimension(business_keys=['missing']),
    dimension(business_keys=['float_id']),dimension(effective_time=dict(column='label',interpretation='UTC')),
    dict(model_type='FACT',grain=dict(description='Event',identity_columns=['id','id'])),
    dict(model_type='FACT',grain=dict(description='Event',identity_columns=['missing'])),
])
def test_roles_types_and_missing_columns_are_rejected(payload):
    with pytest.raises(ValueError):
        validate_model(ADAPTER.validate_python(payload),schema())


def decode(tag):
    kind,value=tag
    convert={'integer':int,'decimal':Decimal,'date':date.fromisoformat,'timestamp':datetime.fromisoformat}
    return convert.get(kind,lambda x:x)(value)


@pytest.mark.parametrize('vector',VECTORS,ids=lambda v:v['name'])
def test_literal_golden_vectors(vector):
    row=dict(zip(vector['columns'],map(decode,vector['values'])))
    boundary=datetime.fromisoformat(vector['valid_from']) if 'valid_from' in vector else None
    encoded=canonical_key(vector['namespace'],vector['role'],vector['columns'],row,valid_from=boundary)
    assert encoded.decode('utf-8')==vector['canonical_utf8']
    assert hashlib.sha256(vector['canonical_utf8'].encode('utf-8')).hexdigest()==vector['sha256']
    assert surrogate_key(vector['namespace'],vector['role'],vector['columns'],row,valid_from=boundary)==vector['sha256']


def test_separate_process_literal_reference():
    code="from app.warehouse.keys import surrogate_key; print(surrogate_key('00000000-0000-0000-0000-000000000001','DIMENSION_MEMBER',['id'],{'id':'C001'}))"
    assert subprocess.check_output([sys.executable,'-c',code],cwd=ROOT,text=True).strip()=='b60ec522cb560ecc822384d9754a2ec9c6d283e7dda51abc98ad60f242dfc511'


@pytest.mark.parametrize('value',[None,1.0,float('nan'),float('inf'),Decimal('NaN'),Decimal('Infinity'),[],{},b'bytes',datetime(2026,1,1)])
def test_null_float_nested_and_naive_identity_rejected(value):
    with pytest.raises(ValueError):
        surrogate_key(NAMESPACE,'DIMENSION_MEMBER',['id'],{'id':value})


def test_exact_unicode_whitespace_type_and_collision_boundaries():
    key=lambda value:surrogate_key(NAMESPACE,'DIMENSION_MEMBER',['id'],{'id':value})
    assert len({key(v) for v in ('C001',' c001 ','c001','é','e\u0301','Ａ','A','',False,0,Decimal('0'))})==11
    assert canonical_value(None)==['null'] and canonical_value('')==['string','']
    assert key(Decimal('1.2300'))==key(Decimal('1.23'))
    assert key(Decimal('-0.0'))==key(Decimal('0'))
    with localcontext() as ctx:
        ctx.prec=2
        assert canonical_value(Decimal('12345678901234567890.123400'))==['decimal','12345678901234567890.1234']
    assert surrogate_key(NAMESPACE,'FACT',['a','b'],{'a':'x|y','b':'z'})!=surrogate_key(NAMESPACE,'FACT',['a','b'],{'a':'x','b':'y|z'})


def test_identity_order_namespace_roles_retry_and_rebuild():
    row={'id':'C001','line':1}
    original=surrogate_key(NAMESPACE,'FACT',['id','line'],row)
    assert original==surrogate_key(NAMESPACE,'FACT',['id','line'],dict(reversed(list(row.items()))))
    assert original!=surrogate_key(NAMESPACE,'FACT',['line','id'],row)
    assert original!=surrogate_key('00000000-0000-0000-0000-000000000002','FACT',['id','line'],row)
    assert original!=surrogate_key(NAMESPACE,'DIMENSION_MEMBER',['id','line'],row)
    assert len(original)==64 and original==original.lower()
    with pytest.raises(ValueError):
        surrogate_key(NAMESPACE,'FACT',['id'],row,algorithm_version=2)


def test_scd1_attributes_do_not_change_key_and_scd2_boundaries_do():
    scd1=ADAPTER.validate_python(dimension(scd_type='SCD_TYPE_1',effective_time=None))
    scd2=ADAPTER.validate_python(dimension())
    first=dict(id='C001',segment='One',effective=datetime(2026,1,1,tzinfo=timezone.utc))
    changed=dict(first,segment='Two',effective=datetime(2026,6,1,tzinfo=timezone.utc),_datarise_application_id=999)
    assert model_surrogate_key(NAMESPACE,scd1,first)==model_surrogate_key(NAMESPACE,scd1,changed)
    assert model_surrogate_key(NAMESPACE,scd2,first)!=model_surrogate_key(NAMESPACE,scd2,changed)
    assert model_surrogate_key(NAMESPACE,scd2,first)==model_surrogate_key(NAMESPACE,scd2,dict(first,segment='Correction'))
    local=ADAPTER.validate_python(dimension(effective_time=dict(column='effective',interpretation='SOURCE_TIMEZONE',timezone='Asia/Kolkata')))
    with pytest.raises(ValueError):model_surrogate_key(NAMESPACE,local,dict(first,effective=datetime(2026,1,1,5,30)))
    resolved=normalize_time(datetime(2026,1,1,5,30),local.effective_time)
    assert model_surrogate_key(NAMESPACE,local,dict(first,effective=resolved))==model_surrogate_key(NAMESPACE,scd2,first)


def test_explicit_time_policy_normalization_and_dst_rejection():
    utc=TimePolicy(column='effective',interpretation='UTC')
    india=TimePolicy(column='effective',interpretation='SOURCE_TIMEZONE',timezone='Asia/Kolkata')
    assert normalize_time(datetime(2026,1,1),utc)==datetime(2026,1,1,tzinfo=timezone.utc)
    assert normalize_time(datetime(2026,1,1,5,30),india)==datetime(2026,1,1,tzinfo=timezone.utc)
    ny=TimePolicy(column='effective',interpretation='SOURCE_TIMEZONE',timezone='America/New_York')
    for value in (datetime(2026,3,8,2,30),datetime(2026,11,1,1,30)):
        with pytest.raises(ValueError):normalize_time(value,ny)
    assert normalize_time(date(2026,1,1),india)==date(2026,1,1)


@pytest.mark.parametrize('load,coverage',[('APPEND',None),('UPSERT',None),('SNAPSHOT','PARTIAL')])
def test_partial_snapshots_never_authorize_absence_deactivation(load,coverage):
    config=ADAPTER.validate_python(dimension(snapshot_absence_policy='DEACTIVATE_ON_COMPLETE'))
    with pytest.raises(ValueError):validate_source_policy(config,load_strategy=load,snapshot_coverage=coverage)
    validate_source_policy(config,load_strategy='SNAPSHOT',snapshot_coverage='COMPLETE')


@pytest.fixture
def modeling_pg(pg):
    with pg['connect']() as conn:
        conn.execute((ROOT/'migrations/0030_warehouse_modeling.sql').read_text(encoding='utf-8'))
        conn.execute(
            (
                ROOT
                / 'migrations/0033_fact_analytical_roles.sql'
            ).read_text(encoding='utf-8')
        )
        version=conn.execute("INSERT INTO dataset_versions(dataset_id,version_number,schema_hash) VALUES (%s,1,%s) RETURNING dataset_version_id",(pg['dataset'],'b'*64)).fetchone()[0]
        columns=[dict(name=n,data_type=t) for n,t in [('id','TEXT'),('line','INTEGER'),('segment','TEXT'),('label','TEXT'),('effective','DATETIME'),('float_id','DECIMAL')]]
        conn.execute('INSERT INTO approved_rule_policies(dataset_version_id,rule_version,rules) VALUES (%s,1,%s)',(version,Jsonb([])))
        policy=conn.execute("""INSERT INTO dataset_load_policies(dataset_id,dataset_version_id,policy_version,load_strategy,business_keys,
            schema_columns,schema_hash,schema_evolution_policy,created_by) VALUES (%s,%s,1,'APPEND','[]',%s,%s,'STRICT',%s) RETURNING policy_id""",
            (pg['dataset'],version,Jsonb(columns),'b'*64,pg['owner']['user_id'])).fetchone()[0]
        foreign_v=conn.execute("INSERT INTO dataset_versions(dataset_id,version_number,schema_hash) VALUES (%s,1,%s) RETURNING dataset_version_id",(pg['foreign_dataset'],'c'*64)).fetchone()[0]
        conn.execute('INSERT INTO approved_rule_policies(dataset_version_id,rule_version,rules) VALUES (%s,1,%s)',(foreign_v,Jsonb([])))
        foreign_policy=conn.execute("""INSERT INTO dataset_load_policies(dataset_id,dataset_version_id,policy_version,load_strategy,business_keys,
            schema_columns,schema_hash,schema_evolution_policy,created_by) VALUES (%s,%s,1,'APPEND','[]',%s,%s,'STRICT',%s) RETURNING policy_id""",
            (pg['foreign_dataset'],foreign_v,Jsonb(columns),'c'*64,pg['other']['user_id'])).fetchone()[0]
    return dict(pg,source_version=version,policy=policy,foreign_policy=foreign_policy)


def configured(pg,payload=None,name='Configured'):
    m,v=draft(pg,name)
    models.add_source(pg['workspace'],m,v,pg['owner'],SourceMapping(source_dataset_id=pg['dataset']))
    request=ModelingRequest(source_policy_id=pg['policy'],source_rule_version=1,configuration=payload or dimension())
    result=modeling.configure_version(pg['workspace'],m,v,pg['owner'],request)
    return m,v,request,result


def test_configure_validate_approve_and_new_version_preserves_history(modeling_pg):
    pg=modeling_pg; w,u=pg['workspace'],pg['owner']
    m,v,request,original=configured(pg)
    assert original['source_policy_id']==pg['policy'] and original['model_type']=='DIMENSION'
    assert modeling.read_configuration(w,m,v,u)['validation']['valid']
    models.approve_version(w,m,v,u)
    for statement in ('UPDATE warehouse_model_configurations SET configuration=configuration','DELETE FROM warehouse_model_configurations',
                      'TRUNCATE warehouse_model_configurations'):
        with pytest.raises(psycopg.Error),pg['connect']() as conn:conn.execute(statement)
    with pytest.raises(HTTPException):modeling.configure_version(w,m,v,u,request)
    next_v=models.create_version(w,m,u,VersionCreate(expected_version=1))['warehouse_model_version_id']
    models.add_source(w,m,next_v,u,SourceMapping(source_dataset_id=pg['dataset']))
    changed=ModelingRequest(source_policy_id=pg['policy'],source_rule_version=1,configuration=dimension(scd_type='SCD_TYPE_1',effective_time=None))
    modeling.configure_version(w,m,next_v,u,changed)
    models.approve_version(w,m,next_v,u)
    historical=modeling.read_configuration(w,m,v,u)
    assert historical['configuration']==original['configuration'] and historical['source_schema']==original['source_schema']


def test_approval_requires_explicit_config_but_legacy_rows_survive(pg):
    # Existing approved 11A rows are not retroactively assigned FACT/DIMENSION.
    m,v=draft(pg)
    models.add_source(pg['workspace'],m,v,pg['owner'],SourceMapping(source_dataset_id=pg['dataset']))
    models.approve_version(pg['workspace'],m,v,pg['owner'])
    with pg['connect']() as conn:
        old_tables=[r[0] for r in conn.execute('SELECT tablename FROM pg_tables WHERE schemaname=current_schema() ORDER BY tablename')]
        def snapshot():
            return {table:conn.execute(sql.SQL('SELECT to_jsonb(t) FROM {} t ORDER BY to_jsonb(t)::text').format(sql.Identifier(table))).fetchall() for table in old_tables}
        before_rows=snapshot()
        before=conn.execute('SELECT to_jsonb(v) FROM warehouse_model_versions v').fetchall()
        conn.execute((ROOT/'migrations/0030_warehouse_modeling.sql').read_text(encoding='utf-8'))
        conn.execute(
    (
        ROOT
        / 'migrations/0033_fact_analytical_roles.sql'
    ).read_text(encoding='utf-8')
)
        assert conn.execute('SELECT to_jsonb(v) FROM warehouse_model_versions v').fetchall()==before
        assert snapshot()==before_rows
        assert conn.execute('SELECT count(*) FROM warehouse_model_configurations').fetchone()[0]==0
        assert conn.execute("SELECT count(*) FROM pg_index i JOIN pg_class t ON t.oid=i.indrelid WHERE t.relname='warehouse_model_configurations' AND t.relnamespace=current_schema()::regnamespace AND (NOT i.indisvalid OR NOT i.indisready)").fetchone()[0]==0
    assert not modeling.read_configuration(pg['workspace'],m,v,pg['owner'])['configured']
    next_v=models.create_version(pg['workspace'],m,pg['owner'],VersionCreate(expected_version=1))['warehouse_model_version_id']
    models.add_source(pg['workspace'],m,next_v,pg['owner'],SourceMapping(source_dataset_id=pg['dataset']))
    with pytest.raises(HTTPException):models.approve_version(pg['workspace'],m,next_v,pg['owner'])
    with pytest.raises(psycopg.Error),pg['connect']() as conn:
        conn.execute("UPDATE warehouse_model_versions SET status='APPROVED',reviewed_by=%s,reviewed_at=NOW() WHERE warehouse_model_version_id=%s",(pg['owner']['user_id'],next_v))
    # Empty-only rollback preserves existing unconfigured metadata; round-trip works.
    with pg['connect']() as conn:
        conn.execute((ROOT/'migrations/rollback/0030_warehouse_modeling.sql').read_text(encoding='utf-8'))
        conn.execute((ROOT/'migrations/0030_warehouse_modeling.sql').read_text(encoding='utf-8'))
        conn.execute(
    (
        ROOT
        / 'migrations/0033_fact_analytical_roles.sql'
    ).read_text(encoding='utf-8')
)
        assert conn.execute('SELECT to_jsonb(v) FROM warehouse_model_versions v WHERE warehouse_model_version_id=%s',(v,)).fetchall()==before


def test_fact_config_and_float_contract_rejection(modeling_pg):
    pg=modeling_pg
    m,v,_,_=configured(pg,fact())
    models.approve_version(pg['workspace'],m,v,pg['owner'])
    with pytest.raises(ValueError):configured(pg,dimension(business_keys=['float_id']),name='BadFloat')


def test_configuration_security_current_owner_and_guessed_version(modeling_pg):
    pg=modeling_pg; w,u=pg['workspace'],pg['owner']
    m,v,request,_=configured(pg)
    for call in (lambda:modeling.read_configuration(w,m,v,pg['other']),
                 lambda:modeling.configure_version(w,m,v,pg['other'],request),
                 lambda:modeling.read_configuration(pg['foreign_workspace'],m,v,pg['other'])):
        with pytest.raises(HTTPException):call()
    other_m,other_v=draft(pg,'Other')
    with pytest.raises(HTTPException):modeling.read_configuration(w,other_m,v,u)
    with pg['connect']() as conn:
        conn.execute('UPDATE workspaces SET owner=%s WHERE workspace_id=%s',(pg['successor']['owner_key'],w))
        conn.execute('UPDATE datasets SET owner=%s WHERE dataset_id=%s',(pg['successor']['owner_key'],pg['dataset']))
    assert modeling.read_configuration(w,m,v,pg['successor'])['created_by']==u['user_id']
    with pytest.raises(HTTPException):modeling.read_configuration(w,m,v,u)
    models.approve_version(w,m,v,pg['successor'])


def test_cross_workspace_policy_and_missing_column_rejected(modeling_pg):
    pg=modeling_pg; w,u=pg['workspace'],pg['owner']
    with pytest.raises(ValueError):configured(pg,dimension(tracked_attributes=['missing']))
    m,v=draft(pg,'Foreign policy')
    models.add_source(w,m,v,u,SourceMapping(source_dataset_id=pg['dataset']))
    for policy in (999999,pg['foreign_policy']):
        with pytest.raises(HTTPException):
            modeling.configure_version(w,m,v,u,ModelingRequest(source_policy_id=policy,source_rule_version=1,configuration=fact()))
    with pytest.raises(HTTPException):
        models.add_source(w,m,v,u,SourceMapping(source_dataset_id=pg['foreign_dataset']))


def test_snapshots_are_immutable_and_stale_rule_pins_block_approval(modeling_pg):
    pg=modeling_pg
    m,v,request,original=configured(pg)
    with pytest.raises(HTTPException):modeling.configure_version(pg['workspace'],m,v,pg['owner'],request)
    with pg['connect']() as conn:
        # Existing rule snapshot table lacks a universal update guard: detect drift explicitly.
        conn.execute("UPDATE approved_rule_policies SET rules=%s WHERE dataset_version_id=%s AND rule_version=1",
                     (Jsonb([dict(column_name='id',rule_type='NOT_NULL',rule_config={'required':True})]),pg['source_version']))
    with pytest.raises(HTTPException):models.approve_version(pg['workspace'],m,v,pg['owner'])
    assert modeling.read_configuration(pg['workspace'],m,v,pg['owner'])['source_schema']==original['source_schema']
    with pytest.raises(psycopg.Error),pg['connect']() as conn:
        conn.execute((ROOT/'migrations/rollback/0030_warehouse_modeling.sql').read_text(encoding='utf-8'))


def test_modeling_feature_disabled_before_database(monkeypatch):
    monkeypatch.setenv('WAREHOUSE_ENABLED','false')
    monkeypatch.setattr(modeling,'repository_transaction',lambda:pytest.fail('Disabled metadata accessed database'))
    with pytest.raises(RuntimeError):modeling.read_configuration(1,1,1,{'user_id':1,'owner_key':'none'})
    assert surrogate_key(NAMESPACE,'FACT',['id'],{'id':'still-pure'})


@pytest.mark.parametrize('invalid',[
    dimension(arbitrary='blocked'),dimension(model_type=None),dimension(scd_type=None),
    dimension(missing_member_policy=None),dimension(tracked_attributes=['id']),
    dimension(tracked_attributes=['missing']),dimension(business_keys=['float_id']),
    dimension(snapshot_absence_policy='DEACTIVATE_ON_COMPLETE'),
    dict(model_type='FACT',schema_version=1,grain=dict(description='Event',identity_columns=[]),event_time=None,identity_null_policy='REJECT'),
])
def test_database_rejects_uncontrolled_or_invalid_semantics(modeling_pg,invalid):
    pg=modeling_pg
    m,v,_,original=configured(pg)
    next_v=models.create_version(pg['workspace'],m,pg['owner'],VersionCreate(expected_version=1))['warehouse_model_version_id']
    models.add_source(pg['workspace'],m,next_v,pg['owner'],SourceMapping(source_dataset_id=pg['dataset']))
    # Include all defaults, then explicitly corrupt selected fields, bypassing service/Pydantic.
    payload=dict(original['configuration']);payload.update(invalid)
    kind='FACT' if invalid.get('model_type')=='FACT' else 'DIMENSION'
    if kind=='FACT':payload=invalid
    with pytest.raises(psycopg.Error),pg['connect']() as conn:
        conn.execute('''INSERT INTO warehouse_model_configurations(warehouse_model_version_id,workspace_id,warehouse_model_id,
            source_dataset_id,source_dataset_version_id,source_policy_id,source_rule_version,model_type,configuration,source_schema,
            source_rule_sha256,created_by) SELECT %s,workspace_id,warehouse_model_id,source_dataset_id,source_dataset_version_id,
            source_policy_id,source_rule_version,%s,%s,source_schema,source_rule_sha256,created_by
            FROM warehouse_model_configurations WHERE warehouse_model_version_id=%s''',(next_v,kind,Jsonb(payload),v))
def test_fact_analytical_roles_persist_under_additive_migration(
    modeling_pg,
):
    pg = modeling_pg

    payload = dict(
        model_type='FACT',
        grain=dict(
            description='One row per source id',
            identity_columns=['id'],
        ),

        # INTEGER is exact in the current pinned source contract.
        measures=['line'],

        attributes=[
            'segment',
            'label',
        ],

        event_time=dict(
            column='effective',
            interpretation='UTC',
        ),
    )

    m, v, _, result = configured(
        pg,
        payload,
        name='FACT analytical roles',
    )

    assert result['model_type'] == 'FACT'

    assert result['configuration']['measures'] == [
        'line'
    ]

    assert result['configuration']['attributes'] == [
        'segment',
        'label',
    ]

    models.approve_version(
        pg['workspace'],
        m,
        v,
        pg['owner'],
    )

    stored = modeling.read_configuration(
        pg['workspace'],
        m,
        v,
        pg['owner'],
    )

    assert stored['validation']['valid']

    assert stored['configuration']['measures'] == [
        'line'
    ]

    assert stored['configuration']['attributes'] == [
        'segment',
        'label',
    ]