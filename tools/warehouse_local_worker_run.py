"""Interactive one-duty warehouse worker startup for local development.

Run once in a dedicated terminal per duty. Never pass the DB password or
signing key in argv, a checked-in file, or a PowerShell environment variable.
The database login and exact ACL are verified before opening the loopback
socket. This does not enable the public query execution API.

For each duty:
    python -m tools.warehouse_local_worker_run provider_submission --port 19081

The tool prints a freshly generated signing key ONCE for a password manager.
The operator must save it privately for the independent health probe and API
client. It is never printed in a diagnostic or persisted by this tool.
Use --existing-key to prompt privately for an existing key instead.

Ctrl+C stops this worker; restarting with --existing-key keeps the same
client key. All connections and replies are restricted to 127.0.0.1.
"""
from __future__ import annotations

import argparse
import base64
from getpass import getpass
import json
import secrets
import sys

from psycopg.conninfo import make_conninfo

from app.db.warehouse_canonical_worker_connection import (
    CanonicalWarehouseWorkerConnection,
)
from app.db.warehouse_restricted_connection import ROLE_BY_DUTY
from app.warehouse.worker_rpc_protocol import DUTY_OPERATIONS
from app.warehouse.worker_rpc_server import create_server, _rpc_key
from tools.warehouse_runtime_role_acceptance import accept

PORTS = {
    "provider_submission": 19081,
    "recovery_attestation": 19082,
    "accounting": 19083,
    "result_authorization": 19084,
}


def local_dsn(duty: str, password: str) -> str:
    if duty not in DUTY_OPERATIONS:
        raise ValueError("WORKER_DUTY_NOT_ALLOWED")
    if type(password) is not str or not password or "\x00" in password:
        raise ValueError("WORKER_DATABASE_PASSWORD_REQUIRED")
    return make_conninfo(
        host="127.0.0.1",port="5432",dbname="ai_data_workspace",
        user=ROLE_BY_DUTY[duty],password=password,connect_timeout="5",
    )


def startup(duty: str, port: int, *, existing_key: bool = False,
            password_reader=getpass, key_reader=getpass,
            output=print, serve=True):
    if duty not in PORTS or port != PORTS[duty]:
        raise ValueError("EXPLICIT_LOCAL_DUTY_PORT_REQUIRED")
    password=password_reader(f"Database password for {ROLE_BY_DUTY[duty]}: ")
    dsn=local_dsn(duty,password)
    # Fail before showing any signing key when the existing DB login is
    # invalid, overprivileged, or the cutover/ACL contract has drifted.
    accept(duty,dsn)
    factory=CanonicalWarehouseWorkerConnection(duty=duty,dsn=dsn)
    if existing_key:
        encoded=key_reader("Existing RPC key (base64, input hidden): ")
        key=_rpc_key(encoded)
    else:
        key=secrets.token_bytes(32)
        encoded=base64.b64encode(key).decode("ascii")
        output("SAVE THIS RPC KEY IN YOUR PASSWORD MANAGER; DO NOT SHARE IT:")
        output(encoded)
        output("KEY WILL NOT BE DISPLAYED AGAIN.")
    server=create_server(duty=duty,secret=key,port=port,
                         boundary=factory.boundary)
    output(json.dumps({
        "worker_started": True,"duty":duty,"bind":"127.0.0.1",
        "port":port,"independent_db_login_verified":True,
        "query_execution_enabled":False,
    },sort_keys=True))
    if not serve:
        server.server_close()
        return
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("duty",choices=tuple(PORTS))
    parser.add_argument("--port",type=int,required=True)
    parser.add_argument("--existing-key",action="store_true")
    args=parser.parse_args()
    try:
        startup(args.duty,args.port,existing_key=args.existing_key)
        return 0
    except KeyboardInterrupt:
        print("WAREHOUSE_WORKER_STOPPED")
        return 0
    except Exception as exc:
        # Never surface DSNs or transport exception strings: they can
        # include credentials. Known validation error codes are non-secret.
        if type(exc) in (ValueError,RuntimeError):
            print("WAREHOUSE_WORKER_START_BLOCKED:",str(exc),file=sys.stderr)
        else:
            print("WAREHOUSE_WORKER_START_BLOCKED:",type(exc).__name__,
                  file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
