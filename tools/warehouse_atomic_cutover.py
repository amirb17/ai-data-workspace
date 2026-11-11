"""Atomic development-only warehouse query cutover.

This tool binds the 13 isolated query foreign keys to canonical public records
and rewires query guard/revocation triggers to read the *canonical* application
tables. It never copies users, sessions, workspace or publication data.

DEFAULT IS ROLLBACK: run python -m tools.warehouse_atomic_cutover to rehearse.
--apply is intentionally NOT implemented until the full least-privilege grants,
application routing, and negative authorization acceptance are integrated.
Never deploy this script to RDS without a distinct production review.

A transaction-level advisory lock prevents parallel cutovers. Exact expected
catalog IDs, zero query protocol rows, source schema, and PostgreSQL database are
verified before any DDL. PostgreSQL DDL remains transactionally rollbackable.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import re
import sys

import psycopg
from psycopg import sql

from tools.warehouse_deployment_boundary import _dsn
from tools.warehouse_query_fk_rebind_plan import (
    CANONICAL, ISOLATED, TARGETS, generate_reviewed_fk_ddl, plan,
)

# Untrusted user-derived table, function or schema names never enter a query.
# Only these already deployed application tables are rebound to canonical data.
CANONICAL_TABLES = frozenset({
    "users", "auth_sessions", "workspaces", "datasets",
    "query_governance_reservations", "query_governance_usage",
    "warehouse_models", "warehouse_model_versions",
    "warehouse_model_sources", "warehouse_model_configurations",
    "warehouse_resources", "warehouse_publications",
    "warehouse_publication_heads", "warehouse_serving_materializations",
})
REVOKE_FUNCTION = "warehouse_query_dispatch_revoke_notice"
REVOKE_TABLES = frozenset({
    "auth_sessions", "users", "workspaces", "datasets",
    "warehouse_models", "warehouse_model_versions",
    "warehouse_resources", "warehouse_publication_heads",
})


def _replace_canonical_identifiers(source: str) -> tuple[str, set[str]]:
    """Token-aware unquoted SQL identifier rewrite, preserving literals/comments.

    PL/pgSQL source may include quoted string comparisons to TG_TABLE_NAME.
    A naive regex changes those and disables revocation; this scanner avoids
    text in SQL single/double quote literals or line/block comments. The
    function's enclosing dollar quote is removed before this helper is used.
    """
    output = []
    seen = set()
    position = 0
    state = "CODE"
    while position < len(source):
        char = source[position]
        following = source[position:position + 2]
        if state == "CODE":
            if following == "--":
                state = "LINE"
                output.append(following)
                position += 2
                continue
            if following == "/*":
                state = "BLOCK"
                output.append(following)
                position += 2
                continue
            if char in ("'", '"'):
                state = "SINGLE" if char == "'" else "DOUBLE"
                output.append(char)
                position += 1
                continue
            match = re.match(r"[a-zA-Z_][a-zA-Z_0-9]*", source[position:])
            if match:
                name = match.group()
                # Do not change qualified identifiers, including aliases.
                previous = source[:position].rstrip()
                if name.lower() in CANONICAL_TABLES and not previous.endswith("."):
                    output.append('public.' + name)
                    seen.add(name.lower())
                else:
                    output.append(name)
                position += len(name)
                continue
        elif state in ("SINGLE", "DOUBLE"):
            output.append(char)
            position += 1
            if char == ("'" if state == "SINGLE" else '"'):
                if position < len(source) and source[position] == char:
                    output.append(source[position])
                    position += 1
                else:
                    state = "CODE"
            continue
        elif state == "LINE":
            if char == "\n":
                state = "CODE"
        elif state == "BLOCK":
            if following == "*/":
                output.append(following)
                position += 2
                state = "CODE"
                continue
        output.append(char)
        position += 1
    if state in ("SINGLE", "DOUBLE", "BLOCK"):
        raise RuntimeError("FUNCTION_SOURCE_UNBALANCED_QUOTES")
    return "".join(output), seen


def _function_source_rewrite(definition: str) -> tuple[str, set[str]]:
    """Only edit the dollar-quoted function body, not its SQL declaration."""
    markers = list(re.finditer(r"\$[a-zA-Z_0-9]*\$", definition))
    if len(markers) < 2:
        raise RuntimeError("FUNCTION_BODY_DELIMITER_NOT_FOUND")
    marker = markers[0].group()
    close = next((match for match in markers[1:] if match.group() == marker), None)
    if close is None:
        raise RuntimeError("FUNCTION_BODY_DELIMITER_MISMATCH")
    body, seen = _replace_canonical_identifiers(
        definition[markers[0].end():close.start()]
    )
    return definition[:markers[0].end()] + body + definition[close.start():], seen


def _trigger_rows(conn):
    return conn.execute("""
        SELECT t.relname, tr.tgname, pg_get_triggerdef(tr.oid, true),
               p.proname, pn.nspname
          FROM pg_trigger tr
          JOIN pg_class t ON t.oid=tr.tgrelid
          JOIN pg_namespace n ON n.oid=t.relnamespace
          JOIN pg_proc p ON p.oid=tr.tgfoid
          JOIN pg_namespace pn ON pn.oid=p.pronamespace
         WHERE n.nspname=%s AND NOT tr.tgisinternal
           AND p.proname=%s
         ORDER BY t.relname,tr.tgname
    """, (ISOLATED, REVOKE_FUNCTION)).fetchall()


def rehearse(conn) -> dict:
    # The only supported operation is a transaction that will be rolled back.
    db, role = conn.execute(
        "SELECT current_database(), session_user"
    ).fetchone()
    if db != "ai_data_workspace" or role != "postgres":
        raise RuntimeError("REVIEWED_LOCAL_DEVELOPMENT_ADMIN_REQUIRED")
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        ("datarise-query-atomic-cutover",),
    )
    inventory = plan(conn)
    ddl = generate_reviewed_fk_ddl(conn, inventory)
    if len(ddl) != 52:
        raise RuntimeError("EXPECTED_13_FK_REBINDINGS_REQUIRED")
    # Require all revocation hooks in the isolated schema, and no corresponding
    # canonical trigger names that we might collide with or overwrite.
    attached = _trigger_rows(conn)
    if len(attached) != len(REVOKE_TABLES):
        raise RuntimeError("ISOLATED_REVOCATION_TRIGGER_COUNT_DRIFT")
    if {row[0] for row in attached} != REVOKE_TABLES:
        raise RuntimeError("ISOLATED_REVOCATION_TRIGGER_SCOPE_DRIFT")
    for table, name, _definition, _function, _schema in attached:
        conflict = conn.execute("""
            SELECT 1 FROM pg_trigger tr
            JOIN pg_class t ON t.oid=tr.tgrelid
            JOIN pg_namespace n ON n.oid=t.relnamespace
            WHERE n.nspname=%s AND t.relname=%s AND tr.tgname=%s
        """, (CANONICAL, table, name)).fetchone()
        if conflict:
            raise RuntimeError("CANONICAL_REVOCATION_TRIGGER_CONFLICT")

    # Rewrite only isolated query guard functions, leaving baseline app
    # procedures unchanged. Security invoker stays security invoker.
    functions = conn.execute("""
        SELECT p.oid, p.proname, pg_get_functiondef(p.oid),
               p.prosecdef, p.prokind, pg_get_function_identity_arguments(p.oid)
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname=%s AND p.prokind='f'
          AND p.proname LIKE 'warehouse_query_%%'
        ORDER BY p.proname
    """, (ISOLATED,)).fetchall()
    changed = []
    for oid, name, definition, definer, kind, args in functions:
        if definer or kind != "f":
            raise RuntimeError("SECURITY_DEFINER_OR_PROCEDURE_NOT_APPROVED")
        rewritten, seen = _function_source_rewrite(definition)
        if seen:
            conn.execute(rewritten)
            changed.append((name, seen))
        # Preserve approved hardening: runtime functions resolve query tables
        # only from dedicated schema. All canonical references must be explicit.
        conn.execute(
            sql.SQL("ALTER FUNCTION {}.{}({}) SET search_path TO pg_catalog, {}, pg_temp").format(
                sql.Identifier(ISOLATED), sql.Identifier(name),
                sql.SQL(args), sql.Identifier(ISOLATED)
            )
        )
    if not changed:
        raise RuntimeError("NO_CANONICAL_FUNCTION_REWRITES_FOUND")

    # Rebind all constraints as one transaction. The candidate SQL is
    # generated from verified catalog identifiers; no user SQL is accepted.
    for statement in ddl:
        conn.execute(statement)

    # Move only the 8 named revocation trigger hooks, replacing isolated
    # test-data hooks with canonical application hooks. The trigger function
    # remains in the protected warehouse schema.
    for table, name, definition, function, fn_schema in attached:
        if fn_schema != ISOLATED:
            raise RuntimeError("REVOCATION_FUNCTION_SCHEMA_DRIFT")
        conn.execute(
            sql.SQL("DROP TRIGGER {} ON {}.{}").format(
                sql.Identifier(name), sql.Identifier(ISOLATED),
                sql.Identifier(table)
            )
        )
        # pg_get_triggerdef gives a full CREATE TRIGGER statement; replace
        # precisely its ON <schema>.<table> segment. No other text changed.
        source_name = sql.SQL("{}.{}").format(
            sql.Identifier(ISOLATED), sql.Identifier(table)
        ).as_string(conn)
        target_name = sql.SQL("{}.{}").format(
            sql.Identifier(CANONICAL), sql.Identifier(table)
        ).as_string(conn)
        if "ON " + source_name not in definition:
            raise RuntimeError("TRIGGER_DEFINITION_SCHEMA_DRIFT")
        new_def = definition.replace("ON " + source_name, "ON " + target_name, 1)
        conn.execute(new_def)

    # Assert every FK target and trigger hook moved in this same tx.
    remaining = conn.execute("""
        SELECT count(*) FROM pg_constraint c
        JOIN pg_class t ON t.oid=c.conrelid
        JOIN pg_namespace n ON n.oid=t.relnamespace
        JOIN pg_class ref ON ref.oid=c.confrelid
        JOIN pg_namespace rn ON rn.oid=ref.relnamespace
        WHERE c.contype='f' AND n.nspname=%s
          AND t.relname LIKE 'warehouse_query_%%'
          AND rn.nspname=%s AND ref.relname=ANY(%s)
    """, (ISOLATED, ISOLATED,
          list({target for _, target in TARGETS.values()}))).fetchone()[0]
    if remaining:
        raise RuntimeError("ISOLATED_QUERY_FK_REBIND_INCOMPLETE")
    if len(_trigger_rows(conn)) != 0:
        raise RuntimeError("ISOLATED_REVOCATION_HOOKS_REMAIN")
    public_count = conn.execute("""
        SELECT count(*) FROM pg_trigger tr
        JOIN pg_class t ON t.oid=tr.tgrelid
        JOIN pg_namespace n ON n.oid=t.relnamespace
        JOIN pg_proc p ON p.oid=tr.tgfoid
        WHERE n.nspname='public' AND p.proname=%s
          AND p.pronamespace=(SELECT oid FROM pg_namespace WHERE nspname=%s)
          AND NOT tr.tgisinternal
    """, (REVOKE_FUNCTION, ISOLATED)).fetchone()[0]
    if public_count != len(REVOKE_TABLES):
        raise RuntimeError("PUBLIC_REVOCATION_HOOK_COUNT_MISMATCH")
    return {
        "foreign_keys_rebound": len(TARGETS),
        "guard_functions_rewritten": len(changed),
        "canonical_names_rewritten": sorted(set().union(*(s for _, s in changed))),
        "revocation_triggers_moved": len(attached),
        "transaction": "ROLLBACK_ONLY",
        "query_execution_enabled": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply:
        parser.error("APPLY_DISABLED_UNTIL_RUNTIME_ACL_AND_NEGATIVE_AUTH_TESTS")
    try:
        with psycopg.connect(_dsn(), autocommit=False) as conn:
            try:
                result = rehearse(conn)
                print(json.dumps(result, indent=2, sort_keys=True))
                print("DRY_RUN_PASS: every DDL statement rolled back")
                return 0
            finally:
                conn.rollback()
    except Exception as exc:
        print("ATOMIC_CUTOVER_REHEARSAL_BLOCKED:", type(exc).__name__,
              str(exc) if isinstance(exc, RuntimeError) else "",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
