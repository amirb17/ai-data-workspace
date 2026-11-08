
"""Parse runtime PostgreSQL secrets without exposing credentials."""

import json
from urllib.parse import quote


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate database secret field")
        result[key] = value
    return result


def database_url_from_secret(secret_string: str, expected_role: str) -> str:
    """Build a PostgreSQL URL from the deployed Secrets Manager JSON."""

    if not isinstance(secret_string, str) or len(secret_string.encode('utf-8')) > 65536:
        raise ValueError("Invalid database secret JSON")
    if expected_role not in {"datarise_worker", "datarise_verifier", "datarise_dispatch"}:
        raise ValueError("Unsupported database runtime role")
    try:
        document = json.loads(secret_string, object_pairs_hook=_unique_fields)
    except (TypeError, ValueError):
        raise ValueError("Invalid database secret JSON") from None

    if not isinstance(document, dict):
        raise ValueError("Database secret must be a JSON object")

    required = ("username", "password", "host", "port", "dbname")

    if any(field not in document for field in required):
        raise ValueError("Database secret is missing required fields")

    username = document["username"]
    password = document["password"]
    host = document["host"]
    port = document["port"]
    dbname = document["dbname"]

    if username != expected_role:
        raise ValueError("Database secret role mismatch")

    if not isinstance(password, str) or not password or '\x00' in password:
        raise ValueError("Invalid database password")

    if (
        not isinstance(host, str)
        or not host
        or any(char in host for char in "/:@?# ")
        or not all(
            part and len(part) <= 63
            and all(c.isascii() and (c.isalnum() or c == "-") for c in part)
            and part[0].isalnum()
            and part[-1].isalnum()
            for part in host.split(".")
        )
    ):
        raise ValueError("Invalid database hostname")

    if isinstance(port, bool) or not isinstance(port, int):
        raise ValueError("Invalid database port")

    if not 1 <= port <= 65535:
        raise ValueError("Invalid database port")

    if not isinstance(dbname, str) or not dbname.strip() or '\x00' in dbname:
        raise ValueError("Invalid database name")

    return (
        f"postgresql://{quote(username, safe='')}:"
        f"{quote(password, safe='')}@"
        f"{host}:{port}/"
        f"{quote(dbname, safe='')}"
    )


def load_database_url(secrets_client, secret_id: str, expected_role: str) -> str:
    """Fetch once; callers own client cleanup and credential refresh policy."""
    if secrets_client is None or not isinstance(secret_id, str) or not secret_id.strip():
        raise ValueError("Database secret reference and client are required")
    try:
        response = secrets_client.get_secret_value(SecretId=secret_id)
    except Exception:
        raise RuntimeError("Database credential retrieval failed") from None
    if not isinstance(response, dict) or not isinstance(response.get('SecretString'), str):
        raise ValueError("Invalid database secret response")
    return database_url_from_secret(response['SecretString'], expected_role)
