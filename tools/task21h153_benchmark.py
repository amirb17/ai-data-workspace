"""Bounded synthetic processor benchmark; no DB, AWS, SDK construction or sockets."""
import argparse
import csv
import io
import json
import os
from pathlib import Path
import socket
import sys
import time
import tracemalloc
import types
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def payload_for(rows):
    if type(rows) is not int or rows not in (8, 800, 8000):
        raise ValueError('Benchmark accepts only 8, 800 or 8000 synthetic rows')
    from tests.task21h153_support import CSV
    source = list(csv.reader(io.StringIO(CSV.decode())))
    output = io.StringIO(newline='')
    writer = csv.writer(output, lineterminator='\n')
    writer.writerow(source[0])
    for block in range(rows // 8):
        for values in source[1:]:
            writer.writerow([str(int(values[0])+block*8), *values[1:]])
    return output.getvalue().encode()


def process_memory():
    """OS cumulative process high-water RSS, not stage delta/container memory."""
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
                'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
                'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        psapi = ctypes.WinDLL('psapi', use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise OSError('Process memory counters unavailable')
        return dict(peak_rss_bytes=counters.PeakWorkingSetSize, current_rss_bytes=counters.WorkingSetSize)
    import resource
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return dict(peak_rss_bytes=value if sys.platform == 'darwin' else value*1024, current_rss_bytes=None)


def measure(rows):
    from tests.task21h153_support import run_processors
    from app.processing import bronze_processor, silver_processor, gold_processor
    payload = payload_for(rows)
    original = gold_processor.get_dataset_profiles
    def lookup(module, profiles):
        module.get_dataset_profiles = lambda _: profiles
    before = process_memory()
    tracemalloc.start()
    started = time.perf_counter()
    try:
        objects, bronze, silver, gold = run_processors(payload, lookup)
        seconds = time.perf_counter()-started
        _, python_peak = tracemalloc.get_traced_memory()
        after = process_memory()
        artifacts = [o for key, o in objects.identities.items() if key.endswith('.parquet')]
        assert bronze['row_count'] == rows and silver['valid_rows'] == rows//4
        return dict(rows=rows, input_bytes=len(payload), seconds=seconds,
            valid_rows=silver['valid_rows'], rejected_rows=silver['rejected_rows'],
            gold_rows=gold['row_count'], artifact_count=len(artifacts),
            artifact_bytes=sum(o.size for o in artifacts), python_traced_peak_bytes=python_peak,
            memory_before=before, memory_after=after,
            includes_database_or_network=False, includes_import_time=False,
            note='Process RSS is cumulative native/Python high-water; tracemalloc excludes most native allocations.')
    finally:
        tracemalloc.stop()
        gold_processor.get_dataset_profiles = original


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rows', type=int, choices=[8, 800, 8000], required=True)
    args = parser.parse_args()
    os.environ['PYTHON_DOTENV_DISABLED'] = '1'
    for name in ('DATARISE_PROCESSING_DEPLOYMENT_APPROVED', 'DATARISE_PROCESSING_READERS', 'DATARISE_DISPATCH_APPROVED'):
        os.environ[name] = 'false'
    namespace = types.ModuleType('tests')
    namespace.__path__ = [str(ROOT/'tests')]
    sys.modules['tests'] = namespace
    def blocked(*a, **kw):
        raise AssertionError('Benchmark prohibits SDK/database/network access')
    import boto3
    import psycopg
    from app import config
    with patch.object(socket.socket, 'connect', blocked), patch.object(socket.socket, 'connect_ex', blocked), \
         patch.object(socket, 'getaddrinfo', blocked), patch.object(boto3, 'Session', blocked), \
         patch.object(boto3, 'client', blocked), patch.object(psycopg, 'connect', blocked), \
         patch.object(config, 'get_boto3_session', blocked):
        print(json.dumps(measure(args.rows), sort_keys=True))


if __name__ == '__main__':
    main()
