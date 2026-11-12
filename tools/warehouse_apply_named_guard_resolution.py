"""Rollback-first repair of the warehouse named-result admission trigger (0065).

The current trigger raises NAMED_ADMISSION_NOT_CURRENT while an exact
fully-qualified join succeeds in the same transaction. Replace its body only
with a reviewed version using fully qualified canonical tables, preserving
all result identity, row, storage, and immutability checks.

    python -m tools.warehouse_apply_named_guard_resolution
    python -m tools.warehouse_apply_named_guard_resolution --apply

No BigQuery jobs are created. No user data or privileges are changed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import psycopg
from psycopg.rows import dict_row

from tools.warehouse_deployment_boundary import _dsn

SCHEMA = 'datarise_warehouse_dev'
SQL = Path(__file__).resolve().parents[1] / 'migrations' / (
    '0065_warehouse_named_guard_canonical_resolution.sql'
)
REQUIRED_MARKERS = (
    'Current named API admission required',
    'Bounded exact named result identity required',
    'Permanent result storage budget exhausted',
    'Named result contract is immutable and permanently held',
    'public.query_governance_reservations',
    'datarise_warehouse_dev.warehouse_query_api_requests',
    "'STRING','INTEGER','NUMERIC','BOOLEAN','DATE','TIMESTAMP'",
)


def execute(*, apply=False):
    original = SQL.read_text(encoding='utf-8')
    if (not original.startswith('-- 0065:')
            or '\nBEGIN;\n' not in original
            or not original.rstrip().endswith('COMMIT;')):
        raise RuntimeError('UNREVIEWED_0065_MIGRATION')
    statement = original.replace('\nBEGIN;\n', '\n', 1)
    statement = statement[:statement.rfind('COMMIT;')]
    if any(marker not in statement for marker in REQUIRED_MARKERS):
        raise RuntimeError('REVIEWED_CONTRACT_CHECKS_MISSING')

    with psycopg.connect(_dsn(), autocommit=False, row_factory=dict_row) as conn:
        try:
            identity = conn.execute(
                'SELECT current_database() AS db, session_user AS login'
            ).fetchone()
            if (identity['db'], identity['login']) != ('ai_data_workspace', 'postgres'):
                raise RuntimeError('LOCAL_POSTGRES_ADMIN_REQUIRED')
            conn.execute(
                'SET LOCAL search_path TO datarise_warehouse_dev,public,pg_catalog,pg_temp'
            )
            if conn.execute('SELECT current_schema() AS schema').fetchone()['schema'] != SCHEMA:
                raise RuntimeError('ISOLATED_WAREHOUSE_SCHEMA_REQUIRED')

            old = conn.execute("""
                SELECT pg_get_functiondef(p.oid) AS body
                FROM pg_proc p
                JOIN pg_namespace n ON n.oid=p.pronamespace
                WHERE n.nspname=%s
                  AND p.proname='warehouse_query_named_result_guard'
            """, (SCHEMA,)).fetchone()
            if old is None or not all(
                    marker in old['body'] for marker in REQUIRED_MARKERS[:4]):
                raise RuntimeError('CURRENT_NAMED_GUARD_UNEXPECTED')

            conn.execute(statement)
            actual = conn.execute("""
                SELECT pg_get_functiondef(p.oid) AS body,
                       p.proconfig AS settings, p.prosecdef AS security_definer
                FROM pg_proc p
                JOIN pg_namespace n ON n.oid=p.pronamespace
                WHERE n.nspname=%s
                  AND p.proname='warehouse_query_named_result_guard'
            """, (SCHEMA,)).fetchone()
            if (actual is None or actual['security_definer']
                    or any(marker not in actual['body']
                           for marker in REQUIRED_MARKERS)
                    or 'search_path=pg_catalog, datarise_warehouse_dev, public, pg_temp'
                       not in (actual['settings'] or ())):
                raise RuntimeError('NAMED_GUARD_SECURITY_REVIEW_FAILED')

            if apply:
                conn.commit()
            else:
                conn.rollback()
            return {
                'migration': '0065',
                'status': 'APPLIED' if apply else 'ROLLBACK_ONLY',
                'canonical_cross_schema_joins': True,
                'immutability_and_result_bounds_retained': True,
                'provider_job_submitted': False,
            }
        except BaseException:
            conn.rollback()
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(execute(apply=args.apply), indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print('WAREHOUSE_NAMED_GUARD_REPAIR_BLOCKED:',
              type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
