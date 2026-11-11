"""414E2 independent jobs.get metadata validation. Never fetch rows or submit SQL."""
from dataclasses import asdict,dataclass
import hashlib
import json
import re
from google.api_core.exceptions import NotFound

MAX_METADATA_BYTES=256*1024


@dataclass(frozen=True)
class LookupPolicy:
    project:str
    location:str
    creator_email:str
    revision:str
    maximum_timeout_ms:int=60000
    principal_subject:str|None=None

    def __post_init__(self):
        if (not re.fullmatch('[a-z][a-z0-9-]{4,28}[a-z0-9]',self.project)
                or self.location!='asia-south1'
                or not re.fullmatch(r'[a-z0-9-]+@[a-z0-9-]+\.iam\.gserviceaccount\.com',self.creator_email)
                or not re.fullmatch('[a-zA-Z0-9_-]{1,64}',self.revision)
                or type(self.maximum_timeout_ms) is not int or not 1<=self.maximum_timeout_ms<=300000
                or (self.principal_subject is not None and (not isinstance(self.principal_subject,str) or not 1<=len(self.principal_subject)<=256))):
            raise ValueError('Reviewed lookup policy required')

    @property
    def sha256(self):return digest(asdict(self))


@dataclass(frozen=True)
class ExpectedJob:
    project:str
    location:str
    job_id:str
    workspace_id:int
    reservation_id:int
    intent_sha256:str
    sql_sha256:str
    tables:tuple[str,...]
    byte_ceiling:int
    execution_started_ms:int


@dataclass(frozen=True)
class MetadataReport:
    observation:str
    verdict:str
    reason:str
    payload_sha256:str|None=None
    bytes_processed:int|None=None
    bytes_billed:int|None=None
    billing_complete:bool=False


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


class _LookupAbsent(Exception):
    pass


class LookupOnlyClient:
    """Client is constructed by trusted startup only; no default credential lookup."""
    def __init__(self,client):self._client=client

    def lookup(self,scope):
        try:job=self._client.get_job(scope.job_id,project=scope.project,location=scope.location,retry=None,timeout=10)
        except NotFound:raise _LookupAbsent() from None
        # QueryJob.to_api_repr() is a submission representation: it drops creator,
        # status and statistics. get_job(full projection) retains those in _properties.
        # This private SDK boundary is covered using an actual hydrated QueryJob.
        raw=job._properties
        if type(raw) is not dict:raise ValueError('Full job metadata required')
        encoded=json.dumps(raw,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        if len(encoded)>MAX_METADATA_BYTES:raise ValueError('Metadata bound')
        detached=json.loads(encoded)
        if type(detached) is not dict:raise ValueError('Malformed metadata')
        # SDK hydration converts only these millisecond timestamps to floats.
        # Normalize exact safe integers here; accounting/config floats stay invalid.
        stats=detached.get('statistics')
        if type(stats) is dict:
            for name in ('creationTime','startTime','endTime'):
                value=stats.get(name)
                if type(value) is float:
                    if not value.is_integer() or not 0<=value<=2**53-1:
                        raise ValueError('Inexact SDK timestamp')
                    stats[name]=str(int(value))
        return detached


@dataclass(frozen=True)
class LookupRuntime:
    policy:LookupPolicy
    transport:LookupOnlyClient


class _EvidenceError(Exception):
    def __init__(self,reason,verdict='REJECTED'):self.reason,self.verdict=reason,verdict


def _require(condition,reason,*,missing=False):
    if not condition:raise _EvidenceError(reason,'UNCERTAIN' if missing else 'REJECTED')


def _number(value,*,optional=False):
    if value is None and optional:return None
    if type(value) is int and 0<=value<=2**63-1:return value
    if type(value) is str and re.fullmatch('[0-9]{1,19}',value) and int(value)<=2**63-1:return int(value)
    raise _EvidenceError('BAD_NUMERIC_EVIDENCE','UNCERTAIN' if value is None else 'REJECTED')


def verify_metadata(raw,expected,policy,*,named_result=None):
    """Missing evidence is uncertain; matching metadata is NOT a settlement permit."""
    payload=None
    try:
        payload=digest(raw)
        _require((expected.project,expected.location)==(policy.project,policy.location),'POLICY_SCOPE_MISMATCH')
        _require(raw.get('kind')=='bigquery#job','JOB_TYPE_MISMATCH')
        ref=raw.get('jobReference',{})
        _require(ref==dict(projectId=expected.project,location=expected.location,jobId=expected.job_id),'JOB_SCOPE_MISMATCH')
        _require(raw.get('user_email') is not None,'MISSING_CREATOR',missing=True)
        _require(raw['user_email']==policy.creator_email,'CREATOR_MISMATCH')
        if policy.principal_subject is not None:
            _require(raw.get('principal_subject') is not None,'MISSING_CREATOR',missing=True)
            _require(raw['principal_subject']==policy.principal_subject,'CREATOR_MISMATCH')
        conf=raw.get('configuration',{});query=conf.get('query',{})
        _require(type(conf) is dict and type(query) is dict,'BAD_CONFIGURATION')
        _require(set(conf)<=set(('jobType','query','dryRun','jobTimeoutMs','labels')),'UNREVIEWED_CONFIGURATION')
        _require(conf.get('jobType')=='QUERY' and conf.get('dryRun',False) is False,'JOB_TYPE_MISMATCH')
        labels=conf.get('labels',{})
        wanted=dict(datarise='query',workspace=str(expected.workspace_id),reservation=str(expected.reservation_id),intent=expected.intent_sha256[:63])
        _require(type(labels) is dict and all(labels.get(k)==v for k,v in wanted.items()),'LABEL_MISMATCH')
        _require(type(query.get('query')) is str,'MISSING_QUERY',missing=True)
        _require(hashlib.sha256(query['query'].encode()).hexdigest()==expected.sql_sha256,'SQL_FINGERPRINT_MISMATCH')
        extra={'destinationTable','createDisposition','writeDisposition'} if named_result is not None else set()
        _require(set(query)<=set(('query','useLegacySql','useQueryCache','maximumBytesBilled','priority','allowLargeResults'))|extra,'UNREVIEWED_QUERY_CONFIGURATION')
        if named_result is not None:
            _require(query.get('destinationTable')==named_result['destination'] and query.get('writeDisposition')=='WRITE_EMPTY'
                and query.get('createDisposition')=='CREATE_IF_NEEDED','NAMED_DESTINATION_MISMATCH')
        _require(query.get('useLegacySql') is False and query.get('useQueryCache') is False
                 and query.get('priority')=='INTERACTIVE' and query.get('allowLargeResults',False) is False,'EXECUTION_POLICY_MISMATCH')
        cap=_number(query.get('maximumBytesBilled'));timeout=_number(conf.get('jobTimeoutMs'))
        _require(1<=cap<=expected.byte_ceiling and 1<=timeout<=policy.maximum_timeout_ms,'EXECUTION_POLICY_MISMATCH')
        stats=raw.get('statistics',{});qs=stats.get('query',{})
        _require(type(stats) is dict and type(qs) is dict,'MISSING_STATISTICS',missing=True)
        _require(qs.get('statementType') is not None and qs.get('referencedTables') is not None,'MISSING_STATISTICS',missing=True)
        _require(qs['statementType']=='SELECT' and not qs.get('referencedRoutines') and not qs.get('referencedLogicalViews')
                 and not qs.get('referencedPropertyGraphs') and not qs.get('scriptStatistics') and not stats.get('scriptStatistics') and not stats.get('parentJobId')
                 and not qs.get('ddlOperationPerformed') and not qs.get('numDmlAffectedRows')
                 and qs.get('cacheHit',False) is False,'QUERY_SCOPE_MISMATCH')
        refs=qs['referencedTables'];_require(type(refs) is list and len(refs)==len(expected.tables),'TABLE_SCOPE_MISMATCH')
        actual=tuple(sorted('.'.join(r[k] for k in ('projectId','datasetId','tableId')) for r in refs))
        _require(actual==tuple(sorted(expected.tables)),'TABLE_SCOPE_MISMATCH')
        created=_number(stats.get('creationTime'));started=_number(stats.get('startTime'),optional=True);ended=_number(stats.get('endTime'),optional=True)
        _require(created>=expected.execution_started_ms-5000 and (started is None or started>=created)
                 and (ended is None or ended>=(started if started is not None else created)),'TIMESTAMP_MISMATCH')
        status=raw.get('status',{});state=status.get('state');error=status.get('errorResult')
        _require(state in ('PENDING','RUNNING','DONE'),'UNKNOWN_JOB_STATE',missing=True)
        processed=_number(qs.get('totalBytesProcessed'),optional=True);billed=_number(qs.get('totalBytesBilled'),optional=True)
        _require(billed is None or billed<=cap,'BILLING_POLICY_MISMATCH')
        complete=processed is not None and billed is not None
        if state!='DONE':
            _require(error is None and ended is None and (state!='RUNNING' or started is not None),'STATUS_MISMATCH')
            observation='IN_PROGRESS'
        else:
            _require(ended is not None,'MISSING_TERMINAL_TIME',missing=True)
            if error is not None:
                _require(type(error) is dict and type(error.get('reason')) is str and bool(error['reason']),'STATUS_MISMATCH')
                observation='CANCELLED' if error['reason']=='stopped' else 'FAILED'
            else:
                _require(not status.get('errors'),'AMBIGUOUS_TERMINAL_ERRORS',missing=True)
                _require(complete and started is not None,'MISSING_SUCCESS_BILLING',missing=True)
                observation='SUCCEEDED'
        return MetadataReport(observation,'VERIFIED_METADATA','MATCHED_JOB_METADATA',payload,processed,billed,complete)
    except _EvidenceError as error:
        return MetadataReport('UNCERTAIN',error.verdict,error.reason,payload)
    except (TypeError,ValueError,KeyError,AttributeError,OverflowError):
        return MetadataReport('UNCERTAIN','UNCERTAIN','MALFORMED_METADATA',payload)


def lookup_metadata(runtime,expected):
    return _lookup_metadata(runtime,expected,verify_metadata)


def _lookup_metadata(runtime,expected,validator):
    """Trusted internal validator composition; no public provider/policy bypass."""
    try:raw=runtime.transport.lookup(expected)
    except _LookupAbsent:return MetadataReport('NOT_FOUND','ABSENT','LOOKUP_NOT_FOUND')
    except Exception:return MetadataReport('UNCERTAIN','UNCERTAIN','LOOKUP_UNAVAILABLE')
    return validator(raw,expected,runtime.policy)
