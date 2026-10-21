"""Scoped semantic configuration, pinned to immutable source contract metadata."""
import hashlib
import json
from fastapi import HTTPException
from pydantic import TypeAdapter
from app.db.database import repository_transaction
from app.db import warehouse_repository as repository
from app.schemas.warehouse_modeling import ModelingRequest, ModelConfiguration, SourceColumn, SourceSchema
from app.warehouse.config import require_enabled
from app.warehouse.access import owned_version, validate_sources
from app.warehouse.model_validation import validate_model, validate_source_policy

ADAPTER = TypeAdapter(ModelConfiguration)


def rule_digest(rules):
    return hashlib.sha256(json.dumps(rules,sort_keys=True,separators=(',', ':'),ensure_ascii=False).encode()).hexdigest()


def contract_schema(contract):
    # Legacy schema_fingerprint maps pandas float to DECIMAL. Do not claim exact
    # decimal key evidence from that lossy label. Exact decimals remain supported
    # by the pure schema/key library for future precise source contracts.
    types = {'TEXT':'STRING','INTEGER':'INTEGER','DECIMAL':'FLOAT','FLOAT':'FLOAT',
             'BOOLEAN':'BOOLEAN','DATE':'DATE','DATETIME':'TIMESTAMP'}
    rules = contract['rules']
    columns = []
    for column in contract['schema_columns']:
        raw = column['data_type']
        overrides = [r['rule_config'].get('type') for r in rules if r['column_name']==column['name'] and r['rule_type']=='DATA_TYPE']
        if len(overrides)>1:
            raise ValueError('Ambiguous approved source type rules')
        if overrides:
            raw = {'NUMERIC':'FLOAT'}.get(overrides[0],overrides[0])
        if raw not in types:
            raise ValueError('Unsupported source contract data type')
        required = any(r['column_name']==column['name'] and r['rule_type']=='NOT_NULL' and r['rule_config'].get('required') is True for r in rules)
        columns.append(SourceColumn(name=column['name'],data_type=types[raw],nullable=False if required else None))
    return SourceSchema(columns=tuple(columns))


def configure_version(workspace_id, model_id, version_id, user, request: ModelingRequest):
    require_enabled()
    with repository_transaction() as conn:
        _, version = owned_version(conn,workspace_id,model_id,version_id,user,True)
        if version['status']!='DRAFT':
            raise HTTPException(409,'Only draft versions can receive semantic configuration')
        mappings = validate_sources(conn,workspace_id,version_id,user['owner_key'])
        primary = next((m for m in mappings if m['purpose']=='PRIMARY'),None)
        if not primary:
            raise HTTPException(409,'An explicit primary source is required')
        if repository.modeling(conn,workspace_id,model_id,version_id):
            raise HTTPException(409,'Configuration is immutable; create a new model version')
        contract = repository.source_contract(conn,primary['source_dataset_id'],request.source_policy_id,request.source_rule_version)
        if not contract:
            raise HTTPException(404,'Pinned source contract/rule version not found for primary dataset')
        schema = contract_schema(contract)
        validate_model(request.configuration,schema)
        validate_source_policy(request.configuration,load_strategy=contract['load_strategy'],snapshot_coverage=contract.get('snapshot_coverage'))
        return repository.save_modeling(conn,workspace_id,model_id,version_id,primary['source_dataset_id'],contract,user['user_id'],
                                        request,schema,rule_digest(contract['rules']))


def check_configuration(conn, workspace_id, model_id, version_id):
    record = repository.modeling(conn,workspace_id,model_id,version_id)
    if not record:
        raise HTTPException(409,'Explicit FACT/DIMENSION configuration is required for approval')
    configuration = ADAPTER.validate_python(record['configuration'])
    schema = SourceSchema.model_validate(record['source_schema'])
    report = validate_model(configuration,schema)
    contract = repository.source_contract(conn,record['source_dataset_id'],record['source_policy_id'],record['source_rule_version'])
    if not contract or rule_digest(contract['rules'])!=record['source_rule_sha256'] or contract_schema(contract)!=schema:
        raise HTTPException(409,'Pinned source contract metadata changed; review a new version')
    validate_source_policy(configuration,load_strategy=contract['load_strategy'],snapshot_coverage=contract.get('snapshot_coverage'))
    return report


def read_configuration(workspace_id, model_id, version_id, user):
    require_enabled()
    with repository_transaction() as conn:
        owned_version(conn,workspace_id,model_id,version_id,user)
        validate_sources(conn,workspace_id,version_id,user['owner_key'])
        record = repository.modeling(conn,workspace_id,model_id,version_id)
        if not record:
            return {'configured':False,'configuration':None}
        # Read stored snapshots, never reinterpret old meaning with a new head/schema.
        validation = validate_model(ADAPTER.validate_python(record['configuration']),SourceSchema.model_validate(record['source_schema']))
        return {'configured':True,**record,'validation':validation}
