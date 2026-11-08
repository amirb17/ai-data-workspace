"""Offline build-input checks. No package installation or registry access."""
import argparse
import hashlib
from pathlib import Path
import re
import tomllib


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def render_export(project, lock, body):
    return ('# Generated with uv export --locked --offline --no-dev --no-editable --no-emit-project --no-header\n'
            '# project-sha256: '+sha(project)+'\n# lock-sha256: '+sha(lock)+
            '\n# export-sha256: '+sha(body)+'\n'+body)


def verify_lock(project, lock, requirements):
    p = Path(project).read_text(encoding='utf-8')
    u = Path(lock).read_text(encoding='utf-8')
    text = Path(requirements).read_text(encoding='utf-8')
    lines = text.splitlines(keepends=True)
    if len(lines) < 5 or not tomllib.loads(p)['project']['dependencies']:
        raise ValueError('Incomplete dependency inputs')
    expected = ['# project-sha256: '+sha(p)+'\n', '# lock-sha256: '+sha(u)+'\n',
                '# export-sha256: '+sha(''.join(lines[4:]))+'\n']
    if lines[1:4] != expected:
        raise ValueError('Dependency export is stale or modified')
    logical = ''.join(lines[4:]).replace('\\\n', ' ')
    count = 0
    for row in logical.splitlines():
        row = row.strip()
        if not row or row.startswith('#'):
            continue
        parts = row.split('--hash=sha256:')
        if len(parts) < 2 or not re.fullmatch(r'[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?==[A-Za-z0-9_.+!-]+(?:\s*;[^\\]*)?\s*', parts[0]):
            raise ValueError('Dependencies must be pinned registry packages with hashes')
        if any(not re.fullmatch(r'[a-f0-9]{64}\s*', value) for value in parts[1:]):
            raise ValueError('Invalid dependency hash')
        count += 1
    if count == 0:
        raise ValueError('Empty dependency export')
    return count


def check_identity(base, revision, requirements_hash, requirements):
    if not re.fullmatch(r'[^\s@]+@sha256:[a-f0-9]{64}', base or ''):
        raise ValueError('A reviewed digest-pinned base image is required')
    if not re.fullmatch(r'[a-f0-9]{40}', revision or ''):
        raise ValueError('An approved source revision is required')
    actual = sha(Path(requirements).read_text(encoding='utf-8'))
    if requirements_hash != actual:
        raise ValueError('Build dependency identity differs')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', default='pyproject.toml')
    parser.add_argument('--lock', default='uv.lock')
    parser.add_argument('--requirements', default='deployment/docker/requirements.lock')
    parser.add_argument('--base')
    parser.add_argument('--revision')
    parser.add_argument('--requirements-hash')
    args = parser.parse_args()
    verify_lock(args.project, args.lock, args.requirements)
    if any((args.base, args.revision, args.requirements_hash)):
        check_identity(args.base, args.revision, args.requirements_hash, args.requirements)
    print('Build inputs verified; no image built')
