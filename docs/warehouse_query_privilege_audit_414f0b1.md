# 414F0-B1 — read-only PostgreSQL warehouse privilege inventory

This checkpoint introduces a diagnostic SQL inventory only, with no privilege changes.
It builds on the existing Task 20 role declarations, which are **not** sufficient
proof of least-privilege access to newer warehouse query metadata (414D/E).

## Usage (reviewed development DB only)

First confirm the database/schema and login, without printing the connection URL.
Use the existing local `DATABASE_URL` and a connection with catalog visibility.
An example using a local Python command (run from repository root):

```powershell
@'
from pathlib import Path
import psycopg
from app.db import database
assert database.DATABASE_URL, "DATABASE_URL is missing"
sql = Path("deployment/operations/warehouse_query_privilege_audit_414f0b1.sql").read_text()
with psycopg.connect(database.DATABASE_URL, autocommit=True) as conn:
    conn.execute("BEGIN READ ONLY")
    try:
        for statement in sql.split(";"):
            if statement.strip():
                with conn.cursor() as cur:
                    cur.execute(statement)
                    print("COLUMNS:", [d.name for d in cur.description])
                    for row in cur.fetchall():
                        print(row)
    finally:
        conn.execute("ROLLBACK")
'@ | python
```

**Important:** This reports only the schema selected by the current connection.
Verify `audited_schema` is correct. Do not apply to RDS until reviewed separately.
Missing roles/tables and unexpected effective privileges require investigation.
In a development database where runtime roles are not provisioned yet, many
checks will show missing identities: this is an inventory result, not a test failure.

## What it does and does not prove

Records the five expected roles' attributes, cross-role membership, effective
table privileges and column writes for 414D/E query security metadata.
It does NOT test authenticated logins, TLS, ownership/trigger/function
permissions, RLS, schema usage, live IAM, deployed credentials, out-of-band
table deletion or permission revocation under concurrent transactions.

The next B2 step must design and test actual privileges under isolated
PostgreSQL test identities, then review runtime connections and negative
permission tests before deploying any grants. The audit is not an authorization
token, a deploy action or provider dispatch. **Live BigQuery remains NO-GO.**
