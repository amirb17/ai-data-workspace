"""Read-only authenticated live health probe for one local worker process.

This command has NO DB credential and does not call cloud services or create
query attempts. Enter the worker-specific RPC key using a hidden terminal
prompt; never place it in arguments, environment, chat, or a file.
"""
from __future__ import annotations

import argparse
from getpass import getpass
import json
import sys

from app.warehouse.worker_rpc_client import WarehouseWorkerClient
from app.warehouse.worker_rpc_server import _rpc_key
from tools.warehouse_local_worker_run import PORTS


def probe(duty: str, port: int, encoded_key: str) -> dict:
    if duty not in PORTS or port != PORTS[duty]:
        raise ValueError("LOCAL_WORKER_DUTY_PORT_MISMATCH")
    client=WarehouseWorkerClient(duty=duty,port=port,
                                 secret=_rpc_key(encoded_key))
    response=client.invoke("health",{})
    if response != {
        "duty":duty,"database_identity_verified":True,
        "query_execution_enabled":False,
    }:
        raise RuntimeError("WORKER_HEALTH_IDENTITY_MISMATCH")
    return {
        "duty":duty,"port":port,
        "worker_process_verified":True,
        "restricted_database_identity_verified":True,
        "query_execution_enabled":False,
    }


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("duty",choices=tuple(PORTS))
    parser.add_argument("--port",type=int,required=True)
    args=parser.parse_args()
    try:
        key=getpass("RPC key for worker (base64, hidden input): ")
        print(json.dumps(probe(args.duty,args.port,key),sort_keys=True,
                         indent=2))
        return 0
    except Exception as exc:
        # Never print RPC keys or transport details.
        if type(exc) in (ValueError,RuntimeError):
            print("WAREHOUSE_LOCAL_WORKER_PROBE_BLOCKED:",str(exc),file=sys.stderr)
        else:
            print("WAREHOUSE_LOCAL_WORKER_PROBE_BLOCKED:",
                  type(exc).__name__,file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
