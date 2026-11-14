"""Bounded I/O and job fence for the EXISTING incremental coordinator."""
import io
import json
from dataclasses import asdict

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.storage.versioned_artifacts import ObjectIdentity, StorageIntegrityError


class IncrementalClient:
    def __init__(self, storage, contract, silver):
        self.storage, self.contract, self.silver = storage, contract, silver
        self.prefix = f"silver/dataset_id={contract['dataset_id']}/dataset_version_id={contract['dataset_version']}/"

    def _key(self, key):
        if not key.startswith(self.prefix) or '\\' in key or any(p in {'', '.', '..'} for p in key.split('/')):
            raise ValueError('Incremental object is outside the current dataset/version')

    def _bounded(self, payload, key):
        if not 0 < len(payload) <= self.storage.max_bytes:
            raise ValueError('Incremental artifact byte bound exceeded')
        if key.endswith('.parquet'):
            import pyarrow.parquet as pq
            p = pq.ParquetFile(io.BytesIO(payload))
            size = sum(p.metadata.row_group(r).column(c).total_uncompressed_size
                       for r in range(p.metadata.num_row_groups) for c in range(p.metadata.num_columns))
            if p.metadata.num_rows > 100000 or p.metadata.num_columns > 256 or size > 128*1024*1024:
                raise ValueError('Incremental state requires a separately approved larger-input adapter')

    def get_object(self, *, Bucket, Key):
        self.storage.checkpoint()
        if Key == self.silver.key:
            payload = self.storage.read(self.silver)
        else:
            self._key(Key)
            metadata = self.storage.client.head_object(Bucket=self.storage.bucket, Key=Key)
            if not metadata.get('VersionId') or metadata.get('VersionId') == 'null' or not 0 < metadata.get('ContentLength',0) <= self.storage.max_bytes:
                raise StorageIntegrityError('Immutable incremental source unavailable')
            response = self.storage.client.get_object(Bucket=self.storage.bucket, Key=Key, VersionId=metadata['VersionId'])
            body = response['Body']
            try:
                payload = body.read(self.storage.max_bytes+1)
            finally:
                body.close()
            if response.get('VersionId') != metadata['VersionId'] or len(payload) != metadata['ContentLength']:
                raise StorageIntegrityError('Incremental read identity changed')
            self.storage.consumed += len(payload)
            if self.storage.consumed > self.storage.max_total_bytes:
                raise StorageIntegrityError('Incremental read budget exhausted')
        self._bounded(payload, Key)
        self.storage.checkpoint()
        return {'Body': io.BytesIO(payload)}

    def put_object(self, *, Bucket, Key, Body, IfNoneMatch):
        self._key(Key)
        self._bounded(Body, Key)
        if IfNoneMatch != '*':
            raise ValueError('Incremental overwrite is forbidden')
        self.storage.checkpoint()
        response = self.storage.client.put_object(Bucket=self.storage.bucket, Key=Key, Body=Body, IfNoneMatch='*')
        if response.get('VersionId') in (None,'','null'):
            raise StorageIntegrityError('Versioned incremental storage required')
        self.storage.checkpoint()
        return response


class ApplyGuard:
    def __init__(self, boundary, storage, contract, silver, *, source_contract=None):
        self.boundary, self.contract = boundary, contract
        self.source_contract = source_contract if source_contract is not None else contract
        self.incremental_client = IncrementalClient(storage, contract, silver)

    def authorize_silver_source(self, key, application, dq, file_id):
        """Replace the fixed-key lineage proof only with a published receipt.

        Neither a caller's candidate-shaped key nor DQ metadata alone suffices.
        The same checks run before any immutable delivery manifest is created.
        """
        from app.db import database
        from app.db import processing_integration_repository as registry
        contract = self.contract
        silver = self.incremental_client.silver
        if self.source_contract != contract:
            from app.processing_worker.integrated_executor import reusable_initial_append
            if not reusable_initial_append(self.source_contract, contract):
                raise ValueError('Untrusted Silver reuse contract')
        if (key != silver.key or file_id != contract['file_id']
            or application['dataset_version_file_id'] != contract['association']
            or application['applied_rule_version'] != contract['rule_version']
            or application['source_dq_run_id'] != dq['dq_run_id']
            or dq.get('silver_sha256') != silver.sha256):
            raise ValueError('Published Silver lineage differs')
        with database.get_connection() as conn:
            self.fence(conn)
            head = registry.head(conn, 'SILVER', file_id, contract['association'])
            if (not head or head['input_contract'] != self.source_contract or not head['verification_report']
                or head['artifact_identities'].get('data') != asdict(silver)):
                raise ValueError('Current verified Silver receipt required')

    def fence(self, conn):
        lease = self.boundary.lease
        conn.execute('SELECT task20_require_input(%s,%s,%s)', (lease.job_id,lease.lease_token,Jsonb(self.contract)))

    def publish_incremental(self, conn, application_id):
        self.fence(conn)
        lease = self.boundary.lease
        conn.execute('SELECT task20_publish_incremental(%s,%s,%s,%s)',
                     (lease.job_id,lease.lease_token,Jsonb(self.contract),application_id))
        return conn.cursor(row_factory=dict_row).execute('SELECT * FROM delivery_applications WHERE application_id=%s', (application_id,)).fetchone()
