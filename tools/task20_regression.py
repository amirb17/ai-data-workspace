"""Dependency-free, process-isolated regression shards; AWS stays blocked.

Usage: uv run --no-sync python tools/task20_regression.py <new evidence directory>
Each child uses the established UUID-schema fixtures and task20_local_tests.py.
The four Task 20 modules are validated separately. Real-cloud cases remain
collected and explicitly skipped by their own opt-in markers.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

sys.dont_write_bytecode = True
root = Path(__file__).resolve().parents[1]
os.chdir(root)
output = Path(sys.argv[1]).resolve()
output.mkdir(parents=True, exist_ok=False)
excluded = sorted(Path('tests').glob('test_task20*.py'))
selected = sorted(set(Path('tests').glob('test_*.py')) - set(excluded))
groups = [[], [], []]
loads = [0, 0, 0]


def weight(path):
    source = path.read_text(encoding='utf-8-sig')
    count = max(1, len(re.findall(r'^def test_', source, re.M)))
    database = 'def pg(' in source or bool(re.search(r'import .*\bpg\b', source))
    return count * (20 if database else 1)


for path in sorted(selected, key=lambda p: (-weight(p), str(p))):
    slot = min(range(3), key=lambda i: loads[i])
    groups[slot].append(path.as_posix())
    loads[slot] += weight(path)
paths = [*Path('app').rglob('*.py'), *Path('migrations').glob('*.sql'),
         *selected, Path('tools/task20_local_tests.py')]
hashes = {p.as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
manifest = dict(selected=[p.as_posix() for p in selected],
                separately_validated=[p.as_posix() for p in excluded],
                groups=groups, start_hashes=hashes, shards=[])
processes = []
streams = []
try:
    for index, files in enumerate(groups, 1):
        location = output / f'shard-{index}'
        location.mkdir()
        command = [sys.executable, 'tools/task20_local_tests.py', '--evidence-dir', str(location),
                   *files, '-q', '--tb=short', '-ra', '-p', 'no:cacheprovider',
                   '--junitxml='+str(location/'results.xml')]
        stream = (location / 'output.txt').open('w', encoding='utf-8')
        streams.append(stream)
        process = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT,
                                   env=os.environ | {'PYTHONDONTWRITEBYTECODE': '1'})
        processes.append(process)
        manifest['shards'].append(dict(command=command, pid=process.pid,
                                       directory=str(location), returncode=None))
    (output/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    print('Started 3 independent shards:', [len(g) for g in groups], 'modules', flush=True)
    while any(p.poll() is None for p in processes):
        for p, entry in zip(processes, manifest['shards']):
            entry['returncode'] = p.poll()
        (output/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
        time.sleep(1)
    for p, entry in zip(processes, manifest['shards']):
        entry['returncode'] = p.returncode
    manifest['changed_during_run'] = [name for name, digest in hashes.items()
        if not Path(name).exists() or hashlib.sha256(Path(name).read_bytes()).hexdigest() != digest]
    (output/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    print('Shard exit codes:', [p.returncode for p in processes], flush=True)
finally:
    for stream in streams:
        stream.close()
raise SystemExit(int(any(p.returncode for p in processes)))
