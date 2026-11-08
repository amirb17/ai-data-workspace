"""Check/regenerate the runtime export using existing uv, offline and locked."""
import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from deployment.docker.verify_lock import render_export, verify_lock


def export(root=ROOT, *, write=False):
    root = Path(root)
    project = (root/'pyproject.toml').read_text(encoding='utf-8')
    lock = (root/'uv.lock').read_text(encoding='utf-8')
    result = subprocess.run(['uv', 'export', '--locked', '--offline', '--no-python-downloads',
        '--no-dev', '--no-editable', '--no-emit-project', '--format', 'requirements-txt', '--no-header'],
        cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=30)
    if result.returncode or (root/'uv.lock').read_text(encoding='utf-8') != lock:
        raise ValueError('Locked offline dependency export failed')
    expected = render_export(project, lock, result.stdout)
    output = root/'deployment/docker/requirements.lock'
    if write:
        output.write_bytes(expected.encode('utf-8'))
    elif output.read_text(encoding='utf-8') != expected:
        raise ValueError('Runtime dependency export requires explicit regeneration')
    return verify_lock(root/'pyproject.toml', root/'uv.lock', output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--write', action='store_true', help='Only rewrites the owned generated requirements file')
    args = parser.parse_args()
    try:
        print('Verified locked runtime requirements:', export(write=args.write))
    except Exception:
        print('Offline dependency export check failed; nothing installed', file=sys.stderr)
        raise SystemExit(2)
