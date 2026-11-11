"""414F0-B: read-only inventory for a reviewed list of PostgreSQL runtime roles.

Never creates roles, calls SET ROLE, grants rights or changes catalog state.
Role names must be supplied from an independently reviewed deployment matrix;
this function does not invent role names or infer their intended grants.
"""
from dataclasses import dataclass
import re

_ROLE=re.compile(r"datarise_[a-z][a-z0-9_]{2,62}")
_MUTATIONS=("INSERT","UPDATE","DELETE","TRUNCATE","REFERENCES","TRIGGER")


@dataclass(frozen=True)
class RolePrivileges:
    role: str
    exists: bool
    can_login: bool
    elevated: bool
    inherits_from: tuple[str,...]
    missing_tables: tuple[str,...]
    mutation_grants: tuple[tuple[str,str],...]
    schema_create: bool
    findings: tuple[str,...]


@dataclass(frozen=True)
class WarehouseRoleInventory:
    schema: str
    current_user: str
    roles: tuple[RolePrivileges,...]
    classification: str = "NO_GO"
    live_restricted_logins_verified: bool = False
    deployment_authorized: bool = False


def inspect_runtime_role_inventory(conn, *, role_names: tuple[str,...],
                                   protected_tables: tuple[str,...]) -> WarehouseRoleInventory:
    """Inspect effective grants to each candidate role without impersonation.

    Role names are strictly checked before DB IO. A missing table is a
    negative finding, not implicit permission denial or acceptance.
    """
    if (type(role_names) is not tuple or not 1<=len(role_names)<=10
            or len(set(role_names))!=len(role_names)
            or any(type(r) is not str or not _ROLE.fullmatch(r) for r in role_names)
            or type(protected_tables) is not tuple
            or not 1<=len(protected_tables)<=128
            or len(set(protected_tables))!=len(protected_tables)
            or any(type(t) is not str or not re.fullmatch(r"[a-z][a-z0-9_]{0,62}",t)
                   for t in protected_tables)):
        raise ValueError("REVIEWED_RUNTIME_ROLE_AND_TABLE_MATRIX_REQUIRED")
    with conn.cursor() as cur:
        cur.execute("SELECT current_schema(),current_user")
        schema,current_user=cur.fetchone()
        cur.execute("""
            SELECT c.relname,c.oid FROM pg_class c
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=current_schema()
              AND c.relkind IN ('r','p') AND c.relname=ANY(%s)
        """,(list(protected_tables),))
        tables=dict(cur.fetchall())
        output=[]
        for role in role_names:
            cur.execute("""
                SELECT rolcanlogin,rolsuper,rolcreaterole,rolcreatedb,
                       rolreplication,rolbypassrls
                FROM pg_roles WHERE rolname=%s
            """,(role,))
            flags=cur.fetchone()
            findings=[]
            if flags is None:
                findings.append("ROLE_MISSING")
                output.append(RolePrivileges(
                    role,False,False,False,(),tuple(protected_tables),(),False,
                    tuple(findings),
                ))
                continue
            login=bool(flags[0])
            elevated=any(bool(x) for x in flags[1:])
            if not login: findings.append("ROLE_CANNOT_LOGIN")
            if elevated: findings.append("ELEVATED_ROLE_PRIVILEGES")
            cur.execute("""
                SELECT r.rolname FROM pg_auth_members m
                JOIN pg_roles member ON member.oid=m.member
                JOIN pg_roles r ON r.oid=m.roleid
                WHERE member.rolname=%s ORDER BY r.rolname
            """,(role,))
            inherited=tuple(x[0] for x in cur.fetchall())
            if inherited: findings.append("ROLE_MEMBERSHIP_REVIEW_REQUIRED")
            cur.execute("SELECT has_schema_privilege(%s,current_schema(),'CREATE')",(role,))
            can_schema_create=bool(cur.fetchone()[0])
            if can_schema_create: findings.append("SCHEMA_CREATION_ALLOWED")
            missing=tuple(t for t in protected_tables if t not in tables)
            if missing: findings.append("PROTECTED_TABLES_MISSING")
            granted=[]
            for name,oid in sorted(tables.items()):
                cur.execute("""
                    SELECT has_table_privilege(%s,%s::oid,'INSERT'),
                           has_table_privilege(%s,%s::oid,'UPDATE'),
                           has_table_privilege(%s,%s::oid,'DELETE'),
                           has_table_privilege(%s,%s::oid,'TRUNCATE'),
                           has_table_privilege(%s,%s::oid,'REFERENCES'),
                           has_table_privilege(%s,%s::oid,'TRIGGER')
                """,tuple(v for _ in _MUTATIONS for v in (role,oid)))
                granted.extend((name,verb) for verb,yes in zip(_MUTATIONS,cur.fetchone()) if yes)
            if granted: findings.append("PROTECTED_TABLE_MUTATION_GRANTS")
            if not findings: findings.append("DIRECT_LOGIN_AND_NEGATIVE_DML_STILL_REQUIRED")
            output.append(RolePrivileges(role,True,login,elevated,inherited,missing,
                                         tuple(granted),can_schema_create,tuple(findings)))
    return WarehouseRoleInventory(schema,current_user,tuple(output))
