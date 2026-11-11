"""Interactive local credential bootstrap for exactly ONE warehouse runtime duty.

Credentials are supplied through a terminal password prompt and never passed
as CLI arguments, written to files, printed, or committed. Only an explicit
--apply performs the ALTER ROLE. Each worker uses a different credential and
is verified as its own PostgreSQL login before this command reports success.

The dedicated role must already exist with its reviewed ACL. This is strictly
for ai_data_workspace on a local PostgreSQL endpoint, NOT AWS RDS. After
installation, store the credential in your OS secret manager / deployment
secret store; the script intentionally does not persist or recover it.

Examples:
  python -m tools.warehouse_local_role_credentials query_control --apply
  python -m tools.warehouse_local_role_credentials provider_submission --apply

The database password prompt needs an interactive console; never use this
with CI logs, shared terminals, or chat-transmitted secrets.
"""
from __future__ import annotations

import argparse
from getpass import getpass
import ipaddress
import sys

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.db.warehouse_restricted_connection import ROLE_BY_DUTY
from tools.warehouse_deployment_boundary import _dsn
from tools.warehouse_runtime_role_acceptance import DUTIES, accept

_MIN_LENGTH = 24
_ALLOWED_HOSTS = frozenset(("localhost", "127.0.0.1", "::1"))


def _approved_admin(conn, settings):
    """Never allow altering roles through a remote or non-admin connection."""
    host = settings.get("host", "localhost")
    # hostaddr can bypass the host DNS target; reject anything except loopback.
    hostaddr = settings.get("hostaddr")
    if host not in _ALLOWED_HOSTS:
        raise RuntimeError("CREDENTIAL_BOOTSTRAP_REQUIRES_LOCAL_LOOPBACK")
    if hostaddr:
        try:
            if not ipaddress.ip_address(hostaddr).is_loopback:
                raise RuntimeError("CREDENTIAL_BOOTSTRAP_REQUIRES_LOCAL_LOOPBACK")
        except ValueError:
            raise RuntimeError("CREDENTIAL_BOOTSTRAP_REQUIRES_LOCAL_LOOPBACK") from None
    if settings.get("dbname") != "ai_data_workspace" or settings.get("user") != "postgres":
        raise RuntimeError("LOCAL_ADMIN_DSN_IDENTITY_REQUIRED")
    if settings.get("service") or settings.get("options"):
        raise RuntimeError("ADMIN_CONNECTION_OPTIONS_FORBIDDEN")
    context = conn.execute("""
        SELECT current_database(),session_user,current_user,
               (SELECT rolsuper FROM pg_roles WHERE rolname=current_user)
    """).fetchone()
    if context != ("ai_data_workspace", "postgres", "postgres", True):
        raise RuntimeError("LOCAL_SUPERUSER_CONTEXT_REQUIRED")


def _check_role(conn, role):
    row = conn.execute("""
        SELECT rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolinherit,
               rolreplication,rolbypassrls,
               (SELECT count(*) FROM pg_auth_members m
                WHERE m.member=r.oid OR m.roleid=r.oid)
        FROM pg_roles r WHERE r.rolname=%s
    """, (role,)).fetchone()
    if row != (True,False,False,False,False,False,False,0):
        raise RuntimeError("ROLE_SECURITY_ATTRIBUTES_DRIFT")


def _read_password(role):
    first = getpass(f"New password for {role} (minimum {_MIN_LENGTH} characters): ")
    second = getpass("Confirm new password: ")
    if first != second or len(first) < _MIN_LENGTH or "\x00" in first:
        raise RuntimeError("PASSWORD_CONFIRMATION_OR_LENGTH_INVALID")
    return first


def _role_dsn(settings, role, password):
    # Do not allow administrative connection arguments to spill into the
    # worker DSN. Explicitly construct only connection transport parameters.
    kwargs={
        "host":settings.get("host", "localhost"),
        "port":settings.get("port", "5432"),
        "dbname":"ai_data_workspace",
        "user":role,
        "password":password,
        "connect_timeout":"5",
    }
    if settings.get("hostaddr"):
        kwargs["hostaddr"]=settings["hostaddr"]
    return make_conninfo(**kwargs)


def bootstrap(duty, *, apply=False, ask_password=_read_password):
    if duty not in DUTIES:
        raise ValueError("UNSUPPORTED_QUERY_RUNTIME_DUTY")
    role = ROLE_BY_DUTY[duty]
    settings=conninfo_to_dict(_dsn())
    # Check identity and role attributes before reading a password.
    with psycopg.connect(_dsn(),autocommit=False) as conn:
        _approved_admin(conn,settings)
        _check_role(conn,role)
        if not apply:
            conn.rollback()
            return {"duty":duty,"role":role,"credentials_changed":False,
                    "query_execution_enabled":False}
        password=ask_password(role)
        # Use SQL identifier composition and a bind variable for the password:
        # no password in logs, command-line argv, or SQL string interpolation.
        conn.execute(sql.SQL("ALTER ROLE {} PASSWORD %s").format(
            sql.Identifier(role)), (password,))
        conn.commit()
    # The admin connection is now closed; independently authenticate the role
    # using its new password, and verify that its effective ACL is exact.
    result=accept(duty,_role_dsn(settings,role,password))
    return {"duty":duty,"role":role,"credentials_changed":True,
            "independent_login_verified":True,
            "effective_grants_verified":True,
            "query_execution_enabled":False,"acceptance":result}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("duty",choices=DUTIES)
    parser.add_argument("--apply",action="store_true")
    args=parser.parse_args()
    try:
        report=bootstrap(args.duty,apply=args.apply)
        # Intentionally suppress admin or worker DSNs and any secrets.
        import json
        print(json.dumps(report,sort_keys=True,indent=2))
        return 0
    except Exception as exc:
        # psycopg exceptions can contain connection details. Never echo them.
        if isinstance(exc,(RuntimeError,ValueError)):
            print("WAREHOUSE_CREDENTIAL_BOOTSTRAP_BLOCKED:",str(exc),file=sys.stderr)
        else:
            print("WAREHOUSE_CREDENTIAL_BOOTSTRAP_BLOCKED:",type(exc).__name__,
                  file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
