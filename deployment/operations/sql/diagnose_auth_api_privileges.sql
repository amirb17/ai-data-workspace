-- READ ONLY. Execute as the exact API database role (CURRENT_USER) on
-- ai-workspace-dev-postgres; no modifications or sample user data exposed.
-- psql: -X -v ON_ERROR_STOP=1 -f diagnose_auth_api_privileges.sql
SELECT current_database() AS database_name, current_user AS checked_role,
       current_schema() AS schema_name,
       has_schema_privilege(current_user, 'public', 'USAGE') AS public_schema_usage;

WITH requirements(rel, privilege) AS (
 VALUES
 ('public.auth_rate_limits','SELECT'), ('public.auth_rate_limits','INSERT'), ('public.auth_rate_limits','UPDATE'),
 ('public.users','SELECT'), ('public.users','INSERT'), ('public.users','UPDATE'),
 ('public.auth_challenges','SELECT'), ('public.auth_challenges','INSERT'), ('public.auth_challenges','UPDATE'),
 ('public.auth_security_events','INSERT'),
 ('public.auth_sessions','SELECT'), ('public.auth_sessions','INSERT'), ('public.auth_sessions','UPDATE'),
 ('public.auth_identities','SELECT'), ('public.auth_identities','INSERT'),
 ('public.auth_oauth_states','SELECT'), ('public.auth_oauth_states','INSERT'), ('public.auth_oauth_states','UPDATE')
)
SELECT rel, privilege,
       CASE WHEN to_regclass(rel) IS NULL THEN NULL
            ELSE has_table_privilege(current_user, rel, privilege) END AS granted,
       to_regclass(rel) IS NOT NULL AS relation_exists
FROM requirements ORDER BY rel, privilege;

-- Explicit sequence usage for BIGSERIAL INSERTs; only existing sequences.
WITH sequence_name(name) AS (
 VALUES ('public.users_user_id_seq'),
        ('public.auth_identities_identity_id_seq'),
        ('public.auth_security_events_event_id_seq')
)
SELECT name,
       CASE WHEN to_regclass(name) IS NULL THEN NULL
            ELSE has_sequence_privilege(current_user, name, 'USAGE') END AS usage_granted,
       to_regclass(name) IS NOT NULL AS sequence_exists
FROM sequence_name ORDER BY name;
