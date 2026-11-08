"""Run focused Task 20 tests with AWS blocked and optional isolated local PG.

Usage: uv run --no-sync python tools/task20_local_tests.py [--offline] <pytest args>
No dependency installation, configuration-file changes or migration application.
Database DDL belongs exclusively to each selected test's temporary-schema fixture.
"""
def main():
    import os
    import socket
    import sys
    import types
    from pathlib import Path

    sys.dont_write_bytecode = True
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    sys.path[:0] = [str(root), str(root / 'tests')]
    namespace = types.ModuleType('tests')
    namespace.__path__ = [str(root / 'tests')]
    sys.modules['tests'] = namespace
    offline = '--offline' in sys.argv
    if offline:
        sys.argv.remove('--offline')
    os.environ['RUN_DB_TESTS'] = '0' if offline else '1'
    os.environ['RUN_BIGQUERY_INTEGRATION'] = '0'
    os.environ['DATARISE_PROCESSING_DEPLOYMENT_APPROVED'] = 'false'
    os.environ['DATARISE_PROCESSING_READERS'] = 'false'
    for name in tuple(os.environ):
        if name.startswith('DATARISE_EXACT_'):
            os.environ.pop(name)

    from app import config
    # dotenv may reintroduce file-based overrides. Pin them off before subsequent
    # module imports; load_dotenv's default non-override behavior then preserves 0.
    for name in tuple(os.environ):
        if name.startswith('DATARISE_EXACT_'):
            os.environ[name] = '0'
    os.environ['DATARISE_EXACT_APPEND_V2_TEST_ONLY'] = '0'
    os.environ['DATARISE_EXACT_GOLD_V2_TEST_ONLY'] = '0'


    class OfflineClient:
        def __getattr__(self, name):
            def blocked(*args, **kwargs):
                raise AssertionError('AWS/storage access prohibited: ' + name)
            return blocked


    class OfflineSession:
        def client(self, *args, **kwargs):
            return OfflineClient()


    config.get_boto3_session = lambda: OfflineSession()
    import boto3


    def blocked(*args, **kwargs):
        raise AssertionError('Live SDK/network access prohibited')


    boto3.Session = blocked
    boto3.client = blocked
    original_connect = socket.socket.connect
    original_resolve = socket.getaddrinfo


    def local_connect(self, address):
        if not isinstance(address, tuple) or address[0] not in ('127.0.0.1', '::1', 'localhost'):
            return blocked()
        return original_connect(self, address)


    def local_resolve(host, *args, **kwargs):
        if host not in ('127.0.0.1', '::1', 'localhost'):
            return blocked()
        return original_resolve(host, *args, **kwargs)


    socket.socket.connect = local_connect
    socket.socket.connect_ex = blocked
    socket.getaddrinfo = local_resolve
    from app.db import database
    if offline:
        database.psycopg.connect = blocked
    else:
        from psycopg.conninfo import conninfo_to_dict, make_conninfo
        settings = conninfo_to_dict(database.DATABASE_URL)
        if settings.get('host') not in ('localhost', '127.0.0.1'):
            raise RuntimeError('Tests require the established local PostgreSQL configuration')
        settings['hostaddr'] = '127.0.0.1'
        settings['connect_timeout'] = '10'
        database.DATABASE_URL = make_conninfo(**settings)

    import pytest
    plugins = []
    if '--evidence-dir' in sys.argv:
        import json
        index = sys.argv.index('--evidence-dir')
        evidence = Path(sys.argv[index + 1])
        del sys.argv[index:index + 2]
        evidence.mkdir(parents=True, exist_ok=True)

        class Evidence:
            """Incremental local evidence survives an interrupted long regression."""
            def pytest_collection_finish(self, session):
                (evidence / 'collected.json').write_text(json.dumps([i.nodeid for i in session.items]), encoding='utf-8')

            def pytest_runtest_logreport(self, report):
                record = dict(nodeid=report.nodeid, when=report.when,
                              outcome=report.outcome, seconds=report.duration)
                with (evidence / 'events.jsonl').open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(record)+'\n')

        plugins.append(Evidence())
    return pytest.main(sys.argv[1:], plugins=plugins)


if __name__ == "__main__":
    raise SystemExit(main())
