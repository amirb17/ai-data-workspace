-- 414F0-B1: READ-ONLY PostgreSQL warehouse runtime privilege inventory.
-- Run only against a reviewed development database; no grants, role changes or cloud calls.
-- This is a diagnostic snapshot, NOT an access permit or deployment approval.
-- Inspect the intended schema first: SELECT current_schema(), current_user, session_user;
-- Run using an audit login with catalog visibility (not a runtime role).
-- Unexpected NULL/missing roles or relations mean NOT VERIFIED.
SELECT current_schema() AS audited_schema, current_user AS audit_login, session_user AS audit_session;

WITH expected(name, expected_login) AS (
 VALUES ('datarise_authority_owner',false),('datarise_api',true),
        ('datarise_worker',true),('datarise_verifier',true),
        ('datarise_dispatch',true)
)
SELECT e.name AS role_name, r.oid IS NOT NULL AS exists,
       r.rolcanlogin, r.rolsuper, r.rolcreatedb, r.rolcreaterole,
       r.rolreplication, r.rolbypassrls, r.rolinherit,
       COALESCE(r.rolcanlogin=e.expected_login AND NOT r.rolsuper
         AND NOT r.rolcreatedb AND NOT r.rolcreaterole
         AND NOT r.rolreplication AND NOT r.rolbypassrls
         AND NOT r.rolinherit,false) AS safe_attributes
FROM expected e LEFT JOIN pg_catalog.pg_roles r ON r.rolname=e.name
ORDER BY e.name;

-- Role memberships, including indirect paths. Any runtime-to-authority or
-- cross-runtime membership is a review blocker, regardless of role attributes.
WITH names(name) AS (
 VALUES ('datarise_authority_owner'),('datarise_api'),
        ('datarise_worker'),('datarise_verifier'),('datarise_dispatch')
)
SELECT child.name AS member, parent.name AS target,
       CASE WHEN member_role.oid IS NULL OR target_role.oid IS NULL THEN NULL
            ELSE pg_catalog.pg_has_role(member_role.oid,target_role.oid,'MEMBER') END AS has_membership
FROM names child CROSS JOIN names parent
LEFT JOIN pg_catalog.pg_roles member_role ON member_role.rolname=child.name
LEFT JOIN pg_catalog.pg_roles target_role ON target_role.rolname=parent.name
WHERE child.name<>parent.name ORDER BY child.name,parent.name;

-- Inspect effective rights against the existing 414D/E control metadata.
-- 'can_*' includes inherited, PUBLIC and column-scope permissions according to
-- PostgreSQL's effective privilege functions; do not infer lack of column grants
-- solely from a false table-level UPDATE/INSERT result.
WITH names(name) AS (
 VALUES ('datarise_api'),('datarise_worker'),
        ('datarise_verifier'),('datarise_dispatch')
), objects(name) AS (
 VALUES ('warehouse_query_dispatch_checkpoints'),
        ('warehouse_query_dispatch_revocations'),
        ('warehouse_query_send_intents'),
        ('warehouse_query_send_journal'),
        ('warehouse_query_recovery_attestations'),
        ('warehouse_query_provider_policies'),
        ('warehouse_query_attempt_publication_pins'),
        ('warehouse_query_attempt_reservation_links'),
        ('warehouse_query_publication_retirement_fences')
), resolved AS (
 SELECT n.name AS role_name,o.name AS relation_name,
        r.oid AS role_oid, c.oid AS relation_oid,
        c.relowner AS owner_oid
 FROM names n CROSS JOIN objects o
 LEFT JOIN pg_catalog.pg_roles r ON r.rolname=n.name
 LEFT JOIN pg_catalog.pg_namespace ns ON ns.nspname=current_schema()
 LEFT JOIN pg_catalog.pg_class c ON c.relnamespace=ns.oid AND c.relname=o.name
)
SELECT role_name,relation_name,relation_oid IS NOT NULL AS relation_exists,
       CASE WHEN role_oid IS NULL OR relation_oid IS NULL THEN NULL
            ELSE pg_catalog.has_table_privilege(role_oid,relation_oid,'SELECT') END AS can_select,
       CASE WHEN role_oid IS NULL OR relation_oid IS NULL THEN NULL
            ELSE pg_catalog.has_table_privilege(role_oid,relation_oid,'INSERT') END AS can_insert,
       CASE WHEN role_oid IS NULL OR relation_oid IS NULL THEN NULL
            ELSE pg_catalog.has_table_privilege(role_oid,relation_oid,'UPDATE') END AS can_update,
       CASE WHEN role_oid IS NULL OR relation_oid IS NULL THEN NULL
            ELSE pg_catalog.has_table_privilege(role_oid,relation_oid,'DELETE') END AS can_delete,
       CASE WHEN role_oid IS NULL OR relation_oid IS NULL THEN NULL
            ELSE pg_catalog.has_table_privilege(role_oid,relation_oid,'TRUNCATE') END AS can_truncate,
       role_oid=owner_oid AS owns_relation
FROM resolved ORDER BY relation_name,role_name;

-- Explicit column privileges may exist independently of table-level grants.
-- Review every returned row; trigger functions and SECURITY DEFINER ownership
-- require a separate pg_proc/pg_trigger and effective-execution audit.
WITH names(name) AS (
 VALUES ('datarise_api'),('datarise_worker'),
        ('datarise_verifier'),('datarise_dispatch')
)
SELECT r.rolname AS role_name,c.relname AS relation_name,a.attname AS column_name,
       pg_catalog.has_column_privilege(r.oid,c.oid,a.attname,'INSERT') AS can_insert,
       pg_catalog.has_column_privilege(r.oid,c.oid,a.attname,'UPDATE') AS can_update
FROM names n JOIN pg_catalog.pg_roles r ON r.rolname=n.name
JOIN pg_catalog.pg_namespace ns ON ns.nspname=current_schema()
JOIN pg_catalog.pg_class c ON c.relnamespace=ns.oid
 AND c.relname IN ('warehouse_query_dispatch_checkpoints',
  'warehouse_query_dispatch_revocations','warehouse_query_send_intents',
  'warehouse_query_send_journal','warehouse_query_recovery_attestations',
  'warehouse_query_provider_policies')
JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
WHERE pg_catalog.has_column_privilege(r.oid,c.oid,a.attname,'INSERT')
   OR pg_catalog.has_column_privilege(r.oid,c.oid,a.attname,'UPDATE')
ORDER BY c.relname,r.rolname,a.attname;
