"""Immutable, checksummed APPEND objects. All paths remain internal."""
import hashlib
import io
import json
import pandas as pd
from app.config import get_boto3_session, S3_BUCKET_NAME
from app.processing.append_engine import effective_schema, LINEAGE
from app.processing.upsert_engine import UPSERT_LINEAGE,validate_upsert_candidate


class IncrementalArtifacts:
    def __init__(self,client=None):
        self.client=client or get_boto3_session().client('s3')

    def read(self,key):
        if not key or '..' in key or '\\' in key or key.startswith('/'):
            raise ValueError('Invalid internal artifact key')
        body=self.client.get_object(Bucket=S3_BUCKET_NAME,Key=key)['Body']
        try: return body.read()
        finally: body.close()

    def put(self,key,body):
        self.client.put_object(Bucket=S3_BUCKET_NAME,Key=key,Body=body,IfNoneMatch='*')
        if self.read(key)!=body:
            raise ValueError('Stored artifact checksum differs')
        return hashlib.sha256(body).hexdigest()

    def parquet(self,df):
        stream=io.BytesIO(); df.to_parquet(stream,index=False,engine='pyarrow'); return stream.getvalue()

    def json(self,value):
        return json.dumps(value,sort_keys=True,separators=(',',':'),default=str).encode()

    def frame(self,key,checksum):
        body=self.read(key)
        if hashlib.sha256(body).hexdigest()!=checksum:
            raise ValueError('Artifact integrity failed')
        return pd.read_parquet(io.BytesIO(body))

    def validate_state(self,key,checksum,application,columns,expected_rows,expected_schema,policy=None):
        body=self.read(key)
        if hashlib.sha256(body).hexdigest()!=checksum: raise ValueError('Candidate manifest integrity failed')
        manifest=json.loads(body)
        for field in ('application_id','dataset_id','dataset_version_id','policy_id','upload_request_id'):
            if manifest[field]!=application[field]: raise ValueError('Candidate ownership differs')
        prefix=key.rsplit('/',1)[0]+'/'
        if not manifest['data_key'].startswith(prefix) or not manifest['outcomes_key'].startswith(prefix):
            raise ValueError('Candidate artifacts outside immutable prefix')
        outcomes_body=self.read(manifest['outcomes_key'])
        if hashlib.sha256(outcomes_body).hexdigest()!=manifest['outcomes_sha256']:
            raise ValueError('Candidate outcomes integrity failed')
        payload=json.loads(outcomes_body)
        outcomes=payload['rejected']
        counts=manifest['counts']
        is_upsert=policy is not None and policy['load_strategy']=='UPSERT'
        if manifest.get('load_strategy')=='UPSERT' and not is_upsert:
            raise ValueError('UPSERT validation requires its trusted policy')
        total=sum(v for k,v in counts.items() if k!='conflict_rows')
        if len(outcomes)!=counts['incremental_rejected_rows'] or any(v<0 for v in counts.values()) or total!=application['valid_rows']:
            raise ValueError('Candidate outcome accounting differs')
        frame=self.frame(manifest['data_key'],manifest['data_sha256'])
        if len(frame)!=expected_rows or manifest['row_count']!=expected_rows:
            raise ValueError('Candidate row count differs')
        lineage=UPSERT_LINEAGE if is_upsert else LINEAGE
        if list(frame.columns)!=columns+lineage or effective_schema(frame,columns)!=expected_schema or manifest['effective_schema']!=expected_schema:
            raise ValueError('Candidate business schema differs')
        if frame[lineage].isna().any().any(): raise ValueError('Candidate lineage incomplete')
        if is_upsert:
            if manifest.get('business_keys')!=policy['business_keys'] or manifest.get('event_time_column')!=policy['event_time_column'] or manifest.get('normalization_version')!=policy['normalization_version']:
                raise ValueError('Candidate key/event policy differs')
            validate_upsert_candidate(frame,policy,application,counts,payload['ledger'])
        return frame
