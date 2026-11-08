"""Offline evidence review only. Never invokes AWS, Terraform or host commands."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def read_json(path):
    path = Path(path)
    if path.is_symlink() or path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('Evidence file is unsuitable')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate evidence field')
            result[key] = value
        return result
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique)


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def changed_paths(before, after, prefix=''):
    if isinstance(before, dict) and isinstance(after, dict):
        result = set()
        for key in before.keys() | after.keys():
            path = prefix+'.'+key if prefix else key
            if key not in before or key not in after:
                result.add(path)
            else:
                result |= changed_paths(before[key], after[key], path)
        return result
    return set() if before == after else {prefix}


def any_marked(value):
    if isinstance(value, dict):
        return any(any_marked(v) for v in value.values())
    if isinstance(value, list):
        return any(any_marked(v) for v in value)
    return value is True


def planned_resources(module):
    yield from module.get('resources', [])
    for child in module.get('child_modules', []):
        yield from planned_resources(child)


def review_plan(plan, policy, baseline):
    """All create/delete/replacement actions are blocked, even if allowlisted.

    Only explicit, fully known, hash-bound updates to three existing runtime
    resource addresses can pass. Reports contain no before/after values.
    A passing offline review is NOT deployment approval or an AWS observation.
    """
    reasons, inventory = [], []
    if not isinstance(plan, dict) or not re.fullmatch(r'1\.[0-9]+', str(plan.get('format_version', ''))):
        raise ValueError('Unsupported plan format')
    if plan.get('complete') is not True or plan.get('errored') is not False or plan.get('deferred_changes'):
        reasons.append('Incomplete or errored plan')
    if plan.get('resource_drift'):
        reasons.append('External resource drift requires review')
    variables = plan.get('variables', {})
    if variables.get('aws_region', {}).get('value') != baseline['region']:
        reasons.append('Region differs or is unknown')
    if variables.get('worker_image_digest', {}).get('value') != baseline['worker_digest']:
        reasons.append('Worker image differs or is unknown; separate promotion review required')
    resources = list(planned_resources(plan.get('planned_values', {}).get('root_module', {})))
    accounts = [r.get('values', {}).get('account_id') for r in resources if r.get('type') == 'aws_caller_identity']
    if not accounts or any(a != baseline['account'] for a in accounts):
        reasons.append('Account identity differs or is unknown')
    instances = [r.get('values', {}).get('id') for r in resources if r.get('address') == 'aws_instance.worker[0]']
    if instances != [baseline['instance_id']]:
        reasons.append('Worker instance identity differs or is unknown')
    verifiers = [r.get('values', {}) for r in resources if r.get('address') == 'aws_lambda_function.trusted_verifier[0]']
    for value in verifiers or [{}]:
        environment = value.get('environment', [])
        flags = environment[0].get('variables', {}) if len(environment) == 1 else {}
        if any(flags.get(k) != 'false' for k in ('DATARISE_PROCESSING_DEPLOYMENT_APPROVED', 'DATARISE_PROCESSING_READERS')):
            reasons.append('Planned verifier flags are enabled or unknown')
    if any(change.get('actions') != ['no-op'] for change in plan.get('output_changes', {}).values()):
        reasons.append('Unexpected output changes require review')
    allowed_paths = {
        'aws_iam_role_policy.worker_runtime[0]': {'policy'},
        'aws_vpc_security_group_ingress_rule.secrets_from_bootstrap[0]': {'description'},
        'aws_lambda_function.trusted_verifier[0]': {'image_uri'},
    }
    approved = policy.get('approved_updates', {})
    if not isinstance(approved, dict) or set(approved) - set(allowed_paths):
        reasons.append('Unsupported review allowlist')
    seen = set()
    changes = plan.get('resource_changes')
    if not isinstance(changes, list):
        raise ValueError('Missing plan change inventory')
    for resource in changes:
        address = resource.get('address')
        if not isinstance(address, str) or not re.fullmatch(r'[A-Za-z0-9_.\[\]"-]{1,256}', address) or address in seen:
            raise ValueError('Invalid or duplicate resource address')
        seen.add(address)
        change = resource.get('change', {})
        actions = change.get('actions')
        if not isinstance(actions, list) or not 1 <= len(actions) <= 2 or any(not isinstance(action, str) or action not in {'no-op', 'read', 'create', 'update', 'delete', 'forget'} for action in actions):
            raise ValueError('Invalid plan actions')
        label = address if address in set(allowed_paths) | {'aws_instance.worker[0]', 'data.aws_caller_identity.current'} else 'unapproved-resource#'+hashlib.sha256(address.encode()).hexdigest()[:12]
        if change.get('importing') is not None or resource.get('previous_address'):
            reasons.append('State import/move requires separate review: '+label)
        if resource.get('mode') == 'data' and actions in (['read'], ['no-op']):
            if resource.get('type') not in {'aws_caller_identity', 'aws_subnet', 'aws_vpc', 'aws_iam_policy_document'}:
                reasons.append('Unapproved data source read: '+label)
            inventory.append({'address': label, 'actions': actions})
            continue
        if actions == ['no-op']:
            continue
        before, after = change.get('before'), change.get('after')
        paths = changed_paths(before, after)
        inventory.append({'address': label, 'actions': actions, 'changed_paths': sorted(paths) if address in allowed_paths else []})
        if actions != ['update'] or change.get('replace_paths'):
            reasons.append('Creation/deletion/replacement or unsupported action: '+label)
            continue
        if any_marked(change.get('after_unknown')) or any_marked(change.get('before_sensitive')) or any_marked(change.get('after_sensitive')):
            reasons.append('Unknown or sensitive change: '+label)
        if (address not in allowed_paths or resource.get('mode') != 'managed'
            or resource.get('type') != address.split('.')[0]
            or not paths or not paths <= allowed_paths.get(address, set())):
            reasons.append('Unexpected resource or attribute change: '+label)
        approval = approved.get(address, {})
        if not isinstance(approval, dict) or approval.get('after_sha256') != canonical_hash(after):
            reasons.append('Missing or mismatched human-reviewed content hash: '+label)
        if address == 'aws_lambda_function.trusted_verifier[0]':
            image = after.get('image_uri', '') if isinstance(after, dict) else ''
            if not re.fullmatch(r'[^\s@]+@sha256:[a-f0-9]{64}', image):
                reasons.append('Verifier image is not pinned')
            repository = image.split('@')[0]
            if (repository != before.get('image_uri', '').split('@')[0]
                or not repository.startswith(baseline['account']+'.dkr.ecr.'+baseline['region']+'.amazonaws.com/')):
                reasons.append('Verifier repository/account/region differs')
            environment = after.get('environment', []) if isinstance(after, dict) else []
            flags = environment[0].get('variables', {}) if len(environment) == 1 else {}
            if any(flags.get(k) != 'false' for k in ('DATARISE_PROCESSING_DEPLOYMENT_APPROVED', 'DATARISE_PROCESSING_READERS')):
                reasons.append('Processing flags are enabled or unknown')
    if set(approved) - seen:
        reasons.append('Review approvals do not match this plan')
    return {'offline_checks_passed': not reasons, 'deployment_approved': False,
            'reasons': reasons, 'inventory': inventory}


def review_host(evidence, baseline, now=None):
    allowed = {'account', 'region', 'instance_id', 'observed_at', 'status_checks_healthy',
               'management_path', 'management_approved', 'graceful_stop_verified',
               'worker_service_active', 'worker_service_enabled', 'worker_image_digest'}
    if not isinstance(evidence, dict) or set(evidence) - allowed:
        raise ValueError('Unexpected host evidence field')
    reasons = []
    for key in ('account', 'region', 'instance_id'):
        if evidence.get(key) != baseline[key]:
            reasons.append('Host identity differs or is unknown: '+key)
    try:
        timestamp = evidence.get('observed_at')
        if not isinstance(timestamp, str):
            raise ValueError('Timestamp missing')
        observed = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
        if observed.tzinfo is None:
            raise ValueError('Timezone missing')
        age = ((now or datetime.now(timezone.utc))-observed).total_seconds()
        if not 0 <= age <= 900:
            reasons.append('Host evidence is stale or in the future')
    except (ValueError, TypeError):
        reasons.append('Host evidence timestamp is invalid')
    if evidence.get('status_checks_healthy') is not True:
        reasons.append('Host status is not confirmed')
    if evidence.get('management_path') != 'organization-approved-serial-console' or evidence.get('management_approved') is not True:
        reasons.append('No organization-approved management path evidenced')
    if evidence.get('graceful_stop_verified') is not True:
        reasons.append('Graceful stop capability is not evidenced')
    if evidence.get('worker_service_active') is not False or evidence.get('worker_service_enabled') is not False:
        reasons.append('Worker active/enabled state is not confirmed false')
    if evidence.get('worker_image_digest') != baseline['worker_digest']:
        reasons.append('Configured/staged image identity differs or is unknown')
    return {'offline_checks_passed': not reasons, 'deployment_approved': False,
            'evidence_authenticated': False, 'reasons': reasons}


def review_context(root):
    root = Path(root)
    text = (root/'.dockerignore').read_text(encoding='utf-8')
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith('#')]
    includes = {'!app/', '!app/**', '!pyproject.toml', '!uv.lock', '!README.md', '!deployment/',
                '!deployment/docker/', '!deployment/docker/Dockerfile.worker', '!deployment/docker/Dockerfile.verifier',
                '!deployment/docker/verify_lock.py', '!deployment/docker/requirements.lock',
                '!deployment/docker/certs/', '!deployment/docker/certs/global-bundle.pem'}
    if not lines or lines[0] != '**' or {p for p in lines if p.startswith('!')} != includes:
        raise ValueError('Build context allowlist differs')
    for required in ('**/.env*', '**/.aws', '**/credentials', '**/*.key', '**/*.pem', '**/*.p12'):
        if required not in lines:
            raise ValueError('Build context secret exclusions are incomplete')
    if lines[-1] != '!deployment/docker/certs/global-bundle.pem':
        raise ValueError('Public CA exception ordering differs')
    count = 0
    if not (root/'app').is_dir():
        raise ValueError('Runtime source tree is missing')
    for path in (root/'app').rglob('*'):
        if '__pycache__' in path.parts or path.suffix in {'.pyc', '.pyo'}:
            continue
        if path.is_symlink() or path.name.startswith('.env') or path.name in {'.aws', 'credentials'} or path.suffix in {'.pem', '.key', '.p12'}:
            raise ValueError('Forbidden material in runtime source tree')
        count += int(path.is_file())
    from deployment.docker.verify_lock import verify_lock
    packages = verify_lock(root/'pyproject.toml', root/'uv.lock', root/'deployment/docker/requirements.lock')
    requirements_hash = hashlib.sha256((root/'deployment/docker/requirements.lock').read_text(encoding='utf-8').encode()).hexdigest()
    return {'offline_checks_passed': True, 'deployment_approved': False, 'source_files': count,
            'locked_requirements': packages, 'requirements_sha256': requirements_hash}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['plan', 'host', 'context'])
    parser.add_argument('evidence', nargs='?')
    parser.add_argument('--policy', default=str(ROOT/'deployment/operations/plan-review-policy.example.json'))
    parser.add_argument('--baseline', default=str(ROOT/'deployment/operations/baseline.json'))
    args = parser.parse_args()
    try:
        if args.mode == 'context':
            result = review_context(ROOT)
        else:
            baseline = read_json(args.baseline)
            evidence = read_json(args.evidence)
            result = review_plan(evidence, read_json(args.policy), baseline) if args.mode == 'plan' else review_host(evidence, baseline)
        print(json.dumps(result, sort_keys=True))
        return int(not result['offline_checks_passed'])
    except Exception:
        # Plans may contain credentials. Never echo payloads or parser exceptions.
        print('{"offline_checks_passed":false,"deployment_approved":false,"error":"Invalid local evidence"}')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
