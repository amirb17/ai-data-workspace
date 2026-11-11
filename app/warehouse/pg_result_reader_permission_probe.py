"""414F0-B: evidence-only PostgreSQL warehouse runtime permission probe.

Uses the actual authenticated psycopg connection supplied by the caller.
No SET ROLE, role creation, grant, DML, DDL or credential acquisition.
A passing catalog scan cannot establish live cloud permissions or replace
negative tests using dedicated restricted login credentials.
"""
from dataclasses import dataclass
import re

PROTECTED_TABLES = (
    "warehouse_query_dispatch_checkpoints",
    "warehouse_query_send_journal",
    "warehouse_query_recovery_attestations",
    "warehouse_query_request_envelopes",
    "warehouse_query_provider_policies",
    "warehouse_query_attempt_registry",
    "warehouse_query_attempt_publication_pins",
    "warehouse_publication_heads",
    "warehouse_serving_materializations",
)
# This audit is deliberately for a *result reader* role only. Other runtime
# roles have different privileges and must be audited against their own matrix.
_MUTATIONS=("INSERT","UPDATE","DELETE","TRUNCATE","REFERENCES","TRIGGER")


@dataclass(frozen=True)
class ProtectedTablePermission:
    table: str
    select: bool
    mutation_permissions: tuple[str,...]


@dataclass(frozen=True)
class RuntimePermissionEvidence:
    session_user: str
    current_user: str
    schema: str
    role_exists: bool
    direct_login: bool
    superuser: bool
    can_create_role: bool
    can_create_database: bool
    bypass_rls: bool
    schema_create: bool
    protected: tuple[ProtectedTablePermission,...]
    missing_protected_tables: tuple[str,...]
    findings: tuple[str,...]
    classification: str = "NO_GO"
    approval_authorized: bool = False


def probe_warehouse_result_reader(conn, *, expected_role: str) -> RuntimePermissionEvidence:
    """Read catalog privileges for *this* connection, not for an inferred role.

    A valid candidate is only an inspection finding. Always NO_GO until
    dedicated-account negative DML and deployment IAM acceptance are done.
    """
    if (type(expected_role) is not str or
            not re.fullmatch(r"datarise_[a-z][a-z0-9_]{2,62}",expected_role)):
        raise ValueError("TRUSTED_EXPECTED_RUNTIME_ROLE_REQUIRED")
    with conn.cursor() as cur:
        cur.execute("""
            SELECT session_user::text,current_user::text,
                   current_schema()::text,
                   current_setting('is_superuser') = 'on'
        """)
        session_user,current_user,schema,session_superuser=cur.fetchone()
        cur.execute("""
            SELECT rolcanlogin,rolsuper,rolcreaterole,rolcreatedb,rolbypassrls
            FROM pg_roles WHERE rolname=%s
        """,(expected_role,))
        candidate=cur.fetchone()
        cur.execute("""
            SELECT rolcanlogin,rolsuper,rolcreaterole,rolcreatedb,rolbypassrls
            FROM pg_roles WHERE rolname=current_user
        """)
        active=cur.fetchone()
        cur.execute("SELECT has_schema_privilege(current_user,current_schema(),'CREATE')")
        schema_create=cur.fetchone()[0]
        cur.execute("""
            SELECT c.relname,c.oid
            FROM pg_class c
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=current_schema()
              AND c.relkind IN ('r','p')
              AND c.relname=ANY(%s)
            ORDER BY c.relname
        """,(list(PROTECTED_TABLES),))
        relations=dict(cur.fetchall())
        protected=[]
        for name,oid in sorted(relations.items()):
            cur.execute("""
                SELECT has_table_privilege(current_user,%s,'SELECT'),
                       has_table_privilege(current_user,%s,'INSERT'),
                       has_table_privilege(current_user,%s,'UPDATE'),
                       has_table_privilege(current_user,%s,'DELETE'),
                       has_table_privilege(current_user,%s,'TRUNCATE'),
                       has_table_privilege(current_user,%s,'REFERENCES'),
                       has_table_privilege(current_user,%s,'TRIGGER')
            """,(oid,)*7)
            rights=cur.fetchone()
            protected.append(ProtectedTablePermission(
                name,bool(rights[0]),
                tuple(verb for verb,permitted in zip(_MUTATIONS,rights[1:]) if permitted),
            ))
    findings=[]
    if candidate is None: findings.append("EXPECTED_ROLE_MISSING")
    if current_user!=expected_role or session_user!=expected_role:
        findings.append("DIRECT_RESTRICTED_ROLE_LOGIN_REQUIRED")
    if (active is None or not active[0] or session_superuser
            or active[1] or active[2] or active[3] or active[4]):
        findings.append("PRIVILEGED_OR_UNVERIFIED_RUNTIME_SESSION")
    if schema_create: findings.append("RUNTIME_SCHEMA_CREATE_NOT_ALLOWED")
    missing=tuple(name for name in PROTECTED_TABLES if name not in relations)
    if missing: findings.append("FULL_MIGRATION_CATALOG_REQUIRED")
    if any(row.mutation_permissions for row in protected):
        findings.append("PROTECTED_WAREHOUSE_TABLE_MUTATION_ACCESS")
    if any(row.select for row in protected):
        # The result reader should use the scoped service; it should never
        # gain direct database reads of checkpoint/journal or physical pins.
        findings.append("PROTECTED_METADATA_DIRECT_SELECT_ACCESS")
    if not findings:
        findings.append("LIVE_NEGATIVE_DML_AND_CLOUD_IAM_REVIEW_REQUIRED")
    return RuntimePermissionEvidence(
        session_user,current_user,schema,candidate is not None,
        bool(active and active[0] and current_user==session_user==expected_role),
        bool(active and active[1]),bool(active and active[2]),
        bool(active and active[3]),bool(active and active[4]),
        bool(schema_create),tuple(protected),missing,tuple(findings),
    )
