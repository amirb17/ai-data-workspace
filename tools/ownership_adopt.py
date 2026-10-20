"""Operator command: READ ONLY unless --execute and exact approval digest supplied.

No HTTP endpoint. Human approval is mandatory outside this command.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.services.ownership_adoption_service import adoption
from app.db.database import DATABASE_URL
import psycopg
from psycopg.conninfo import make_conninfo


def connect():
    return psycopg.connect(make_conninfo(DATABASE_URL,hostaddr='127.0.0.1'),options='-c search_path=public,pg_catalog')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--operator',type=int,required=True)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--approved-manifest-sha256')
    parser.add_argument('--reverse',action='store_true')
    args=parser.parse_args()
    value=json.loads(args.manifest.read_text())
    manifest=value.get('manifest',value)
    if args.execute and args.approved_manifest_sha256 != manifest['manifest_sha256']:
        parser.error('Execution requires the exact independently approved manifest SHA-256')
    # V1 command is deliberately narrower than the reusable tested service.
    if (manifest['target_user_id'] != 34965 or manifest['source_principal'] != 'dev:datarise-local-dev'
            or len(manifest['resource_scope']) != 12 or sum(map(len,manifest['resource_scope'].values())) != 30):
        parser.error('Unexpected V1 adoption scope')
    with connect() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        if conn.execute('SELECT current_database(),current_schema()').fetchone()!=('ai_data_workspace','public'):
            parser.error('V1 adoption command requires the reviewed development database and public schema')
    result=adoption(manifest,args.operator,execute=args.execute,operation='REVERSE' if args.reverse else 'ADOPT',connection_factory=connect)
    print(json.dumps(result))
    raise SystemExit(0 if result['status'] in ('READY_TO_ADOPT','READY_TO_REVERSE','ADOPTED','ALREADY_ADOPTED','REVERSED','ALREADY_REVERSED') else 1)


if __name__=='__main__':main()
