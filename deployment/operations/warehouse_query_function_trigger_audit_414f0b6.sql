-- 414F0-B6: REVIEW-ONLY catalog inventory for warehouse control functions/triggers.
-- Run in an isolated migrated schema, NOT public, under BEGIN READ ONLY.
-- No GRANT/REVOKE/DDL or provider/network access. Results do not authorize execution.
SELECT current_schema() AS audited_schema, current_user AS audit_user,
       current_setting('search_path') AS search_path;

WITH expected(name) AS (
 VALUES ('warehouse_query_attempt_registry_guard'),
        ('warehouse_query_attempt_publication_guard'),
        ('warehouse_query_attempt_reservation_link_guard'),
        ('warehouse_query_linked_pin_insert_guard'),
        ('warehouse_query_retirement_fence_guard'),
        ('warehouse_query_retirement_link_guard'),
        ('warehouse_query_dispatch_record_guard'),
        ('warehouse_query_dispatch_revoke_notice'),
        ('warehouse_query_send_intent_guard'),
        ('warehouse_query_send_journal_guard'),
        ('warehouse_query_send_initial_event_guard'),
        ('warehouse_query_recovery_attestation_guard'),
        ('warehouse_query_provider_policy_guard'),
        ('warehouse_query_publication_dependency_present')
), actual AS (
 SELECT p.oid,p.proname,p.prosecdef,p.proconfig,p.proowner,p.proacl,
        n.nspname AS schema_name,pg_get_function_identity_arguments(p.oid) AS arguments
 FROM pg_catalog.pg_proc p
 JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname=current_schema()
)
SELECT e.name AS expected_function,a.oid IS NOT NULL AS exists,
       a.arguments, a.prosecdef AS security_definer,
       a.proconfig AS function_settings,
       pg_catalog.pg_get_userbyid(a.proowner) AS function_owner,
       CASE WHEN a.oid IS NULL THEN NULL
            ELSE EXISTS (SELECT 1 FROM pg_catalog.aclexplode(COALESCE(a.proacl,pg_catalog.acldefault('f',a.proowner))) x WHERE x.grantee=0 AND x.privilege_type='EXECUTE') END AS public_execute,
       a.proacl AS explicit_acl
FROM expected e LEFT JOIN actual a ON a.proname=e.name
ORDER BY e.name,a.arguments;

WITH expected(name) AS (
 VALUES ('warehouse_query_attempt_registry_validate'),
        ('warehouse_query_attempt_publication_validate'),
        ('warehouse_query_attempt_reservation_link_validate'),
        ('warehouse_query_linked_pin_insert_validate'),
        ('warehouse_query_retirement_fence_validate'),
        ('a_warehouse_query_retirement_link_validate'),
        ('warehouse_query_dispatch_checkpoint_guard'),
        ('warehouse_query_dispatch_revocation_guard'),
        ('warehouse_dispatch_session_revoke'),
        ('warehouse_dispatch_account_revoke'),
        ('warehouse_dispatch_workspace_revoke'),
        ('warehouse_dispatch_publication_revoke'),
        ('warehouse_dispatch_resource_revoke'),
        ('warehouse_dispatch_source_revoke'),
        ('warehouse_dispatch_model_revoke'),
        ('warehouse_dispatch_version_revoke'),
        ('warehouse_send_intent_guard'),
        ('warehouse_send_journal_guard'),
        ('warehouse_recovery_attestation_guard'),
        ('warehouse_provider_policy_guard')
), actual AS (
 SELECT t.tgname,t.tgenabled,t.tgfoid,t.tgisinternal,
        c.relname AS table_name
 FROM pg_catalog.pg_trigger t
 JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
 JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname=current_schema() AND NOT t.tgisinternal
)
SELECT e.name AS expected_trigger,a.tgname IS NOT NULL AS exists,
       a.table_name,a.tgenabled AS enabled_mode,
       CASE WHEN a.tgfoid IS NULL THEN NULL ELSE a.tgfoid::regprocedure::text END AS function_target
FROM expected e LEFT JOIN actual a ON a.tgname=e.name
ORDER BY e.name,a.table_name;
