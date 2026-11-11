"""C2 bounded named destinations. Pure policy, never cloud authorization."""
from dataclasses import dataclass, asdict
import hashlib
import re
from app.warehouse.query_recovery_attestation import digest

STORAGE_BYTES=8*1024*1024
WORKSPACE_STORAGE_BYTES=64*1024*1024
FIXED_TYPES=frozenset({'INTEGER','NUMERIC','BOOLEAN','DATE','TIMESTAMP'})

@dataclass(frozen=True)
class NamedResultPolicy:
    reader_principal: str
    revision: str
    read_seconds: int = 3600
    def __post_init__(self):
        if (not re.fullmatch(r'[a-z0-9-]+@[a-z0-9-]+\.iam\.gserviceaccount\.com',self.reader_principal)
                or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',self.revision)
                or type(self.read_seconds) is not int or not 1<=self.read_seconds<=3600):
            raise ValueError('BOUNDED_NAMED_RESULT_POLICY_REQUIRED')


def projection_schema(columns):
    kinds={'INT64':'INTEGER','BOOL':'BOOLEAN','DECIMAL':'NUMERIC'}
    return [(c.logical_name,kinds.get(c.physical_type,c.physical_type)) for c in columns]

def validate_fixed_schema(schema, rows):
    if (type(rows) is not int or not 1<=rows<=500 or not 1<=len(schema)<=32
            or any(not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]{0,127}',n) or t not in FIXED_TYPES for n,t in schema)
            or len({n for n,_ in schema})!=len(schema)):
        raise ValueError('NAMED_RESULTS_REQUIRE_BOUNDED_FIXED_WIDTH_PROJECTION')
    # Conservative allowance: 64KiB table metadata plus 256 bytes per cell
    # and row. Fixed scalar types cannot contain arbitrary strings/arrays.
    if 65536+rows*(256+len(schema)*256)>STORAGE_BYTES:
        raise ValueError('NAMED_RESULT_STORAGE_BOUND_EXCEEDED')


def result_identity(project, workspace, job_id):
    return dict(projectId=project,datasetId=f'dr_results_dev_w{workspace}',
        tableId='result_'+hashlib.sha256(job_id.encode()).hexdigest())


def validate_result_contract(result, project, source_dataset, job_id):
    required={'version','destination','schema','maximum_rows','maximum_storage_bytes',
        'workspace_storage_bytes','read_until_ms','policy'}
    if type(result) is not dict or set(result)!=required or type(result.get('version')) is not int or result['version']!=1:
        raise ValueError('EXACT_NAMED_RESULT_CONTRACT_REQUIRED')
    workspace=source_dataset.removeprefix('dr_dev_w')
    if not workspace.isdigit() or result['destination']!=result_identity(project,int(workspace),job_id):
        raise ValueError('NAMED_DESTINATION_IDENTITY_MISMATCH')
    if (result['maximum_storage_bytes']!=STORAGE_BYTES or result['workspace_storage_bytes']!=WORKSPACE_STORAGE_BYTES
            or type(result['read_until_ms']) is not int or result['read_until_ms']<=0):
        raise ValueError('NAMED_STORAGE_AND_EXPIRY_REQUIRED')
    NamedResultPolicy(**result['policy'])
    validate_fixed_schema(result['schema'],result['maximum_rows'])
    return result


def properties(obj):
    raw=obj if type(obj) is dict else getattr(obj,'_properties',None)
    if type(raw) is not dict or len(str(raw).encode())>262144:
        raise ValueError('BOUNDED_NAMED_METADATA_REQUIRED')
    return raw


def verify_dataset(client, destination, location):
    raw=properties(client.get_dataset(destination['projectId']+'.'+destination['datasetId'],retry=None,timeout=10))
    if (raw.get('datasetReference')!={k:destination[k] for k in ('projectId','datasetId')}
            or raw.get('location')!=location or raw.get('storageBillingModel')!='LOGICAL'
            or raw.get('defaultTableExpirationMs') not in (None,'0',0)
            or raw.get('defaultPartitionExpirationMs') not in (None,'0',0)):
        raise ValueError('DEDICATED_RESULT_DATASET_POLICY_MISMATCH')
    return raw


def verify_materialization(client, expected):
    result=expected['result'];dest=result['destination']
    dataset=verify_dataset(client,dest,expected['location'])
    table=properties(client.get_table('.'.join(dest[k] for k in ('projectId','datasetId','tableId')),retry=None,timeout=10))
    fields=[dict(name=n,type=t,mode='NULLABLE') for n,t in result['schema']]
    numeric=lambda v: type(v) is int and v>=0 or type(v) is str and v.isascii() and v.isdigit()
    if (table.get('tableReference')!=dest or table.get('type')!='TABLE' or table.get('location')!=expected['location']
            or not isinstance(table.get('etag'),str) or not table['etag'] or len(table['etag'])>256
            or table.get('schema')!={'fields':fields} or table.get('expirationTime') is not None
            or any(table.get(k) for k in ('timePartitioning','rangePartitioning','externalDataConfiguration','view','streamingBuffer'))
            or not numeric(table.get('numRows')) or int(table['numRows'])>result['maximum_rows']
            or not numeric(table.get('numBytes')) or int(table['numBytes'])>result['maximum_storage_bytes']):
        raise ValueError('NAMED_RESULT_MATERIALIZATION_MISMATCH')
    # Hash only stable, security-relevant facts, never arbitrary provider output.
    proof=dict(destination=dest,etag=table['etag'],schema=table['schema'],rows=int(table['numRows']),
        logical_bytes=int(table['numBytes']),location=table['location'],billing=dataset['storageBillingModel'])
    return proof,digest(proof)


class IndependentNamedResultReader:
    """F5 transport wrapper. Supplied clients are trusted startup/test bindings."""
    def __init__(self, expected, proof_sha, metadata_client, reader):
        self.expected,self.proof_sha,self.metadata,self.reader=expected,proof_sha,metadata_client,reader
        principal=getattr(reader,'principal',None)
        if (principal!=expected['result']['policy']['reader_principal']
                or principal==expected['provider_policy']['creator_email']
                or principal==getattr(metadata_client,'principal',None)):
            raise ValueError('INDEPENDENT_RESULT_READER_REQUIRED')
    def get_query_results_once(self, *, project,location,job_id,max_results):
        if ((project,location,job_id)!=(self.expected['project'],self.expected['location'],self.expected['job_id'])
                or type(max_results) is not int or not 1<=max_results<=min(100,self.expected['result']['maximum_rows'])):
            raise ValueError('ORIGINAL_NAMED_RESULT_SCOPE_REQUIRED')
        for before in (True,False):
            _,sha=verify_materialization(self.metadata,self.expected)
            if sha!=self.proof_sha:raise ValueError('NAMED_RESULT_CHANGED')
            if before:
                value=self.reader.get_query_results_once(project=project,location=location,job_id=job_id,max_results=max_results)
        return value
