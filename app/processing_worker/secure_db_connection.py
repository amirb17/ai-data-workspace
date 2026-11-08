
"""Enforce verified PostgreSQL TLS for AWS runtime connections."""

from pathlib import Path

from psycopg.conninfo import conninfo_to_dict, make_conninfo


def secure_conninfo(database_url: str, ca_path: str) -> str:
    if not database_url or not database_url.startswith(
        ("postgresql://", "postgres://")
    ):
        raise ValueError("A PostgreSQL connection URL is required")

    if not Path(ca_path).is_file():
        raise ValueError("Trusted PostgreSQL CA bundle is missing")

    try:
        params = conninfo_to_dict(database_url)
    except Exception:
        raise ValueError("Invalid PostgreSQL connection URL") from None

    if not params.get("host") or params.get("hostaddr"):
        raise ValueError("A database DNS hostname is required")

    if set(params) - {'host', 'port', 'user', 'password', 'dbname', 'sslmode', 'sslrootcert', 'gssencmode', 'connect_timeout'}:
        raise ValueError("Unsupported runtime database connection options")

    return make_conninfo(
        database_url,
        sslmode="verify-full",
        sslrootcert=ca_path,
        gssencmode="disable",
        connect_timeout=5,
        keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3,
    )
