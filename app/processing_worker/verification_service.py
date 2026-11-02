"""Artifact verification prior to database publication.

This module does not authorize real processing or access AWS directly.
"""

from app.processing_worker.artifact_contract import verify_candidate


class RegistryVerifier:
    """Trusted verifier service boundary. Its DB identity must be verifier-only.

    Worker supplies a candidate UUID, never a key, manifest, hash or evidence DTO.
    The injected connection provider belongs to the verifier service, not an API
    or worker principal. No SDK/client construction occurs here.
    """
    def __init__(self, storage, connection, *, isolated_test=False):
        self.storage, self.connection = storage, connection
        self.isolated_test = isolated_test

    def verify(self, candidate_id):
        import io
        import pyarrow.parquet as pq
        from dataclasses import asdict
        from psycopg.types.json import Jsonb
        from app.db.processing_integration_repository import registry
        from app.storage.versioned_artifacts import ObjectIdentity, StorageIntegrityError, VersionedArtifacts, validate_csv_source
        storage = VersionedArtifacts(self.storage.client, self.storage.bucket,
            max_bytes=self.storage.max_bytes, max_total_bytes=self.storage.max_total_bytes,
            checkpoint=self.storage.checkpoint)
        with self.connection() as conn:
            from app.processing_worker.runtime_security import require_runtime
            require_runtime(conn, 'verifier', isolated_test=self.isolated_test)
            registered = registry(conn, candidate_id)
        if not registered or not registered['manifest_identity'] or not registered['artifact_identities']:
            raise ValueError('Registered version-bound candidate required')
        if (registered['stage'] != registered['input_contract'].get('stage')
            or registered['file_id'] != registered['input_contract'].get('file_id')
            or registered['dataset_version_file_id'] != registered['input_contract'].get('association')):
            raise ValueError('Registered stage scope mismatch')
        # Authorize all source/upstream identities BEFORE making any storage call.
        # Registry INSERT permissions alone must not turn the verifier into a
        # deputy for reading arbitrary keys. Final verification repeats the fence.
        with self.connection() as conn:
            conn.execute('SELECT task20_require_input(%s,%s,%s)',
                (registered['job_id'], registered['lease_token'], Jsonb(registered['input_contract'])))
        manifest = ObjectIdentity.decode(registered['manifest_identity'])
        prefix = f'artifact-candidates/{candidate_id}/'
        if manifest.key != prefix+'manifest.json':
            raise ValueError('Manifest is outside registered candidate namespace')
        if manifest.size > 1048576:
            raise StorageIntegrityError('Manifest bound exceeded')
        objects = {role: ObjectIdentity.decode(value) for role, value in registered['artifact_identities'].items()}
        if any(not o.key.startswith(prefix) for o in objects.values()):
            raise ValueError('Artifact is outside registered candidate namespace')
        if not 1 <= len(objects) <= 32:
            raise ValueError('Invalid registered artifact roles')
        allowed = {value.key: value for value in objects.values()}
        payloads = {}
        def read(key):
            if key not in allowed:
                raise ValueError('Unregistered artifact access')
            payloads[key] = storage.read(allowed[key])
            return payloads[key]
        evidence = verify_candidate(storage.read(manifest), manifest_sha256=registered['manifest_sha256'],
            intent_id=registered['intent_id'], candidate_id=candidate_id, stage=registered['stage'],
            input_fingerprint=registered['input_fingerprint'], expected_roles=frozenset(objects),
            read_artifact=read, max_artifact_bytes=storage.max_bytes)
        inspected, frames = {}, {}
        for role, identity in objects.items():
            if role == 'skip' and registered['stage'] == 'GOLD':
                import json
                with self.connection() as conn:
                    dq = conn.execute('SELECT valid_rows FROM data_quality_runs WHERE dq_run_id=%s',
                        (registered['input_contract']['dq_run'],)).fetchone()
                proof = json.loads(payloads[identity.key])
                if not dq or dq[0] != 0 or proof != {'kind':'EMPTY_VALID_INPUT','dq_run':registered['input_contract']['dq_run']}:
                    raise ValueError('Invalid Gold skip evidence')
                inspected[role] = {'rows': 0, 'kind': 'EMPTY_VALID_INPUT'}
                continue
            parquet = pq.ParquetFile(io.BytesIO(payloads[identity.key]))
            if parquet.metadata.num_rows > 100000 or parquet.metadata.num_columns > 256:
                raise ValueError('Parquet dimensions exceed execution bounds')
            uncompressed = sum(parquet.metadata.row_group(r).column(c).total_uncompressed_size
                for r in range(parquet.metadata.num_row_groups) for c in range(parquet.metadata.num_columns))
            if uncompressed > 128*1024*1024:
                raise ValueError('Parquet expansion exceeds execution bounds')
            table = parquet.read()
            if len(set(table.column_names)) != len(table.column_names):
                raise ValueError('Duplicate artifact columns')
            inspected[role] = {'rows': table.num_rows, 'columns': table.column_names,
                               'schema': str(table.schema)}
            frames[role] = table.to_pandas()
        stage = registered['stage']
        if stage == 'BRONZE':
            if set(objects) != {'data','lexical'} or inspected['data']['rows'] != inspected['lexical']['rows']:
                raise ValueError('Incomplete Bronze artifact set')
            import pandas as pd
            source = storage.read(ObjectIdentity.decode(registered['input_contract']['source']))
            validate_csv_source(source)
            inferred = pd.read_csv(io.BytesIO(source))
            lexical = pd.read_csv(io.BytesIO(source), dtype=str, keep_default_na=False, na_filter=False)
            pd.testing.assert_frame_equal(frames['lexical'], lexical, check_dtype=False, check_exact=True)
            if not frames['data']['_source_file_id'].eq(registered['file_id']).all():
                raise ValueError('Bronze lineage mismatch')
            pd.testing.assert_frame_equal(frames['data'].drop(columns=['_source_file_id','_bronze_ingested_at']),
                                           inferred, check_dtype=False, check_exact=True)
        elif stage == 'SILVER':
            with self.connection() as conn:
                prior = conn.execute("""SELECT c.verification_report FROM processing_stage_receipts r
                    JOIN processing_artifact_candidates c ON c.candidate_id=r.candidate_id WHERE receipt_id=%s""",
                    (registered['input_contract']['upstream_receipt'],)).fetchone()[0]
            if set(objects) not in ({'data'}, {'data','quarantine'}) or sum(v['rows'] for v in inspected.values()) != prior['parquet']['data']['rows']:
                raise ValueError('Silver DQ row accounting differs from pinned Bronze')
            import pandas as pd
            from app.processing.silver_transformer import apply_common_cleaning, apply_transformations
            from app.processing.silver_validator import apply_business_rules
            from app.processing.silver_processor import _prepare_for_parquet
            contract = registered['input_contract']
            original = pd.read_parquet(io.BytesIO(storage.read(ObjectIdentity.decode(contract['upstream_artifacts']['data']))))
            rules = [(None,contract['dataset_version'],r['column_name'],r['rule_type'],r['rule_config'],True) for r in contract['rules']]
            transformed, errors, nulls = apply_transformations(df=apply_common_cleaning(original), rules=rules, lexical_frame=None)
            validated = apply_business_rules(df=transformed,rules=rules,transformation_errors=errors,original_null_masks=nulls)
            for role, valid in [('data',True),('quarantine',False)]:
                expected = _prepare_for_parquet(validated[validated['_dq_is_valid']==valid].copy())
                if len(expected) or role in frames:
                    pd.testing.assert_frame_equal(frames[role].reset_index(drop=True), expected.reset_index(drop=True), check_dtype=False, check_exact=True)
        else:
            from app.processing.gold_planner import build_gold_plan
            contract = registered['input_contract']
            with self.connection() as conn:
                valid = conn.execute('SELECT valid_rows FROM data_quality_runs WHERE dq_run_id=%s', (contract['dq_run'],)).fetchone()[0]
            fields = ('profile_id','file_id','column_name','inferred_type','null_count','distinct_count',
                      'duplicate_count','min_value','max_value','negative_count')
            plan = build_gold_plan([tuple(p[k] for k in fields) for p in contract['profiles']])
            required = {'skip'} if valid == 0 else {'base', *('mart_'+p.artifact_name for p in plan.artifacts)}
            if set(objects) != required or (valid and inspected['base']['rows'] != valid):
                raise ValueError('Gold publication does not match pinned plan/DQ')
            if valid:
                import pandas as pd
                from app.processing.gold_mart_builder import build_mart
                source = pd.read_parquet(io.BytesIO(storage.read(ObjectIdentity.decode(contract['upstream_artifacts']['data']))))
                expected = source.drop(columns=[c for c in ('_dq_violations','_dq_violation_count','_dq_is_valid') if c in source])
                pd.testing.assert_frame_equal(frames['base'].drop(columns=['_gold_published_at','_gold_attempt_id']),
                                               expected, check_dtype=False, check_exact=True)
                for artifact in plan.artifacts:
                    pd.testing.assert_frame_equal(frames['mart_'+artifact.artifact_name],
                        build_mart(df=frames['base'],plan=artifact), check_dtype=False, check_exact=True)
        report = {'manifest_sha256': evidence.manifest_sha256, 'manifest_identity': asdict(manifest),
                  'artifacts': {r: asdict(v) for r,v in objects.items()}, 'parquet': inspected}
        with self.connection() as conn:
            conn.execute('SELECT task20_verify(%s,%s,%s,%s)',
                (registered['job_id'], registered['lease_token'], candidate_id, Jsonb(report)))
        return report


def verify_stage_artifacts(
    manifest_bytes,
    *,
    manifest_sha256,
    intent_id,
    candidate_id,
    stage,
    input_fingerprint,
    expected_roles,
    read_artifact,
    max_artifact_bytes,
):
    """Validate artifact bytes against a trusted stage intent.

    This function returns verification evidence only.
    It does not update PostgreSQL or publish an artifact.
    """
    return verify_candidate(
        manifest_bytes,
        manifest_sha256=manifest_sha256,
        intent_id=intent_id,
        candidate_id=candidate_id,
        stage=stage,
        input_fingerprint=input_fingerprint,
        expected_roles=expected_roles,
        read_artifact=read_artifact,
        max_artifact_bytes=max_artifact_bytes,
    )
