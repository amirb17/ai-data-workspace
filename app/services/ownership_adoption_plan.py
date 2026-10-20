"""Pure adoption planning only. No database connection, executor, or access alias."""
from copy import deepcopy
from hashlib import sha256
import json
from uuid import UUID, uuid5

NAMESPACE = UUID('e4c13efb-12b9-5c68-9dfb-81d4f1a92f75')


class AdoptionConflict(ValueError):
    pass


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def prepare_manifest(snapshot, source_principal, target_user_id):
    target = snapshot['target']
    if target['user_id'] != target_user_id or not target['email_verified'] or target['account_status'] != 'ACTIVE':
        raise AdoptionConflict('Target must be the independently verified active account')
    if source_principal == target['owner_key']:
        raise AdoptionConflict('Source and target must differ')
    if any(r['owner'] == target['owner_key'] for r in snapshot['workspaces'] + snapshot['datasets']):
        raise AdoptionConflict('Target already owns resources; review conflicts explicitly')
    workspaces = sorted(r['workspace_id'] for r in snapshot['workspaces'] if r['owner'] == source_principal)
    if not workspaces:
        raise AdoptionConflict('No source workspaces; an empty batch is not adoption')
    scope = {str(ws): [] for ws in workspaces}
    for row in sorted(snapshot['datasets'], key=lambda r: r['dataset_id']):
        included = row['workspace_id'] in workspaces
        if included != (row['owner'] == source_principal):
            raise AdoptionConflict('Dataset and workspace ownership do not agree')
        if included:
            scope[str(row['workspace_id'])].append(row['dataset_id'])
    identity = {'source_principal': source_principal, 'target_user_id': target_user_id,
                'target_principal': target['owner_key'], 'resource_scope': scope}
    manifest = {**identity, 'adoption_id': str(uuid5(NAMESPACE, digest(identity))),
                'invariants': deepcopy(snapshot['invariants'])}
    return {**manifest, 'manifest_sha256': digest(manifest)}


def plan(manifest, snapshot, events=(), operation='ADOPT'):
    """Check an approved fixed manifest, never rediscover resources during replay."""
    if operation not in ('ADOPT', 'REVERSE'):
        raise AdoptionConflict('Unsupported operation')
    unsigned = {k: v for k, v in manifest.items() if k != 'manifest_sha256'}
    if digest(unsigned) != manifest['manifest_sha256']:
        raise AdoptionConflict('Manifest changed')
    identity = {k: manifest[k] for k in ('source_principal', 'target_user_id', 'target_principal', 'resource_scope')}
    if str(uuid5(NAMESPACE, digest(identity))) != manifest['adoption_id']:
        raise AdoptionConflict('Batch identity changed')
    target = snapshot['target']
    if (target['user_id'], target['owner_key'], target['email_verified'], target['account_status']) != (
            manifest['target_user_id'], manifest['target_principal'], True, 'ACTIVE'):
        raise AdoptionConflict('Target identity changed')
    prior = {}
    for event in events:
        if event['adoption_id'] != manifest['adoption_id']:
            continue
        if (event.get('operation') not in ('ADOPT', 'REVERSE') or event.get('status') != 'COMMITTED'
                or any(event.get(k) != v for k, v in identity.items())
                or event['manifest_sha256'] != manifest['manifest_sha256'] or event['operation'] in prior):
            raise AdoptionConflict('Conflicting or duplicate provenance')
        prior[event['operation']] = event
    if 'REVERSE' in prior and 'ADOPT' not in prior:
        raise AdoptionConflict('Reversal without adoption')
    if operation == 'ADOPT' and 'REVERSE' in prior:
        raise AdoptionConflict('Reversed batch needs a new explicitly reviewed adoption cycle')
    already = operation in prior
    if operation == 'ADOPT' and not prior:
        if prepare_manifest(snapshot, manifest['source_principal'], manifest['target_user_id']) != manifest:
            raise AdoptionConflict('Source scope or business state changed')
    if operation == 'REVERSE' and 'ADOPT' not in prior:
        raise AdoptionConflict('Cannot reverse an unexecuted adoption')
    expected = manifest['target_principal'] if ('ADOPT' in prior and 'REVERSE' not in prior) else manifest['source_principal']
    desired = manifest['target_principal'] if operation == 'ADOPT' else manifest['source_principal']
    updates = []
    workspaces = {r['workspace_id']: r for r in snapshot['workspaces']}
    datasets = {r['dataset_id']: r for r in snapshot['datasets']}
    for ws_text, ids in manifest['resource_scope'].items():
        ws = int(ws_text)
        if ws not in workspaces or workspaces[ws]['owner'] != expected:
            raise AdoptionConflict('Workspace ownership is partial, foreign or changed')
        updates.append({'table': 'workspaces', 'field': 'owner', 'id': ws, 'before': expected, 'after': desired})
        for dataset in ids:
            row = datasets.get(dataset)
            if not row or row['workspace_id'] != ws or row['owner'] != expected:
                raise AdoptionConflict('Dataset membership or ownership changed')
            updates.append({'table': 'datasets', 'field': 'owner', 'id': dataset, 'before': expected, 'after': desired})
    if already:
        return {'status': 'ALREADY_ADOPTED' if operation == 'ADOPT' else 'ALREADY_REVERSED',
                'updates': [], 'provenance_inserts': []}
    if snapshot['invariants'] != manifest['invariants']:
        raise AdoptionConflict('Business state changed; revalidate the manifest before execution or rollback')
    event = {k: manifest[k] for k in ('adoption_id', 'source_principal', 'target_user_id', 'target_principal',
                                    'resource_scope', 'manifest_sha256')}
    event.update(operation=operation, status='COMMITTED',
                 workspace_count=len(manifest['resource_scope']),
                 dataset_count=sum(len(ids) for ids in manifest['resource_scope'].values()),
                 initiated_reason='V1_LEGACY_RESOURCE_ADOPTION' if operation == 'ADOPT' else 'POST_TRANSFER_VERIFICATION_FAILED')
    return {'status': 'WOULD_ADOPT' if operation == 'ADOPT' else 'WOULD_REVERSE', 'updates': updates,
            'provenance_inserts': [event], 'executed_at': 'SERVER_TIME_ONLY_IF_APPROVED_TRANSACTION_EXECUTES'}


def simulate(manifest, snapshot, events=(), operation='ADOPT'):
    """In-memory proof only: returns copies; never persists resources or provenance."""
    result = plan(manifest, snapshot, events, operation)
    after = deepcopy(snapshot)
    for change in result['updates']:
        key = 'workspace_id' if change['table'] == 'workspaces' else 'dataset_id'
        next(r for r in after[change['table']] if r[key] == change['id'])['owner'] = change['after']
    return after, [*deepcopy(events), *result['provenance_inserts']], result
