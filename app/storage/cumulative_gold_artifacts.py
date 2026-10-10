"""Immutable cumulative artifacts, verified before metadata publication."""
import hashlib
import json
from uuid import uuid4
from app.storage.incremental_artifacts import IncrementalArtifacts


class CumulativeGoldArtifacts(IncrementalArtifacts):
    def build(self, run, artifacts):
        prefix = f"gold/dataset_id={run['dataset_id']}/state_id={run['source_state_id']}/run_id={run['gold_run_id']}/candidate-{uuid4().hex}/"
        catalog = []
        for index,(frame, metadata) in enumerate(artifacts):
            key = prefix + f'artifact-{index}.parquet'
            checksum = self.put(key, self.parquet(frame))
            catalog.append({**metadata, 'gold_artifact_id': index+1, 'gold_run_id': run['gold_run_id'],
                            'dataset_id': run['dataset_id'], 'source_state_id': run['source_state_id'],
                            'storage_path': key, 'sha256': checksum, 'row_count': len(frame),
                            'dependencies': [{'dataset_id': run['dataset_id'], 'state_id': run['source_state_id']} ]})
        manifest = {'build_version': run['build_version'], 'dataset_id': run['dataset_id'],
                    'source_state_id': run['source_state_id'], 'source_sha256': run['source_sha256'],
                    'dataset_version_id': run['dataset_version_id'], 'policy_id': run['policy_id'],
                    'gold_run_id': run['gold_run_id'], 'artifacts': catalog}
        key = prefix + 'manifest.json'
        checksum = self.put(key, self.json(manifest))
        return key, checksum, catalog

    def validate(self, run, key, checksum, expected_rows, columns, *, base_only=False, frames=None):
        expected_prefix = f"gold/dataset_id={run['dataset_id']}/state_id={run['source_state_id']}/run_id={run['gold_run_id']}/"
        if not key.startswith(expected_prefix):
            raise ValueError('Gold manifest outside owned run')
        body = self.read(key)
        if hashlib.sha256(body).hexdigest() != checksum:
            raise ValueError('Gold manifest integrity failed')
        manifest = json.loads(body)
        for field in ('dataset_id','source_state_id','source_sha256','dataset_version_id','policy_id','gold_run_id','build_version'):
            if manifest.get(field) != run[field]:
                raise ValueError('Gold source ownership differs')
        catalog = manifest['artifacts']
        if not catalog or catalog[0]['artifact_type'] != 'BASE' or catalog[0]['row_count'] != expected_rows:
            raise ValueError('Gold base count differs')
        if len({a['artifact_name'] for a in catalog}) != len(catalog):
            raise ValueError('Duplicate Gold artifact identity')
        prefix = key.rsplit('/',1)[0] + '/'
        for index,artifact in enumerate(catalog):
            if (not artifact['storage_path'].startswith(prefix) or artifact['dataset_id'] != run['dataset_id']
                    or artifact['source_state_id'] != run['source_state_id'] or artifact['gold_run_id'] != run['gold_run_id']
                    or artifact['gold_artifact_id'] != index+1
                    or artifact['dependencies'] != [{'dataset_id':run['dataset_id'],'state_id':run['source_state_id']}]
                    or artifact['artifact_type'] != ('BASE' if index==0 else 'MART')):
                raise ValueError('Gold artifact ownership differs')
            if base_only and artifact['artifact_type'] != 'BASE':
                continue
            frame = self.frame(artifact['storage_path'], artifact['sha256'])
            if frames is not None:
                frames[artifact['gold_artifact_id']] = frame
            semantic = artifact['columns']
            if len(frame) != artifact['row_count'] or list(frame.columns) != [c['column_name'] for c in semantic]:
                raise ValueError('Gold schema/catalog differs')
            if [str(frame[c].dtype) for c in frame] != [c['data_type'] for c in semantic] or not artifact['grain']:
                raise ValueError('Gold grain/types incomplete')
            if artifact['artifact_type'] == 'BASE' and list(frame.columns) != columns:
                raise ValueError('Gold business schema differs')
            if artifact['artifact_type'] == 'MART' and int(frame.record_count.sum()) != expected_rows:
                raise ValueError('Gold mart counts do not reconcile')
        return catalog
