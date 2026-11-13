-- Read-only permissions inventory for the authenticated workspace Overview.
-- Run as datarise_api on private aiworkspace RDS over SSM.
\set ON_ERROR_STOP on
SELECT current_database() AS db, current_user AS role, current_schema() AS schema;
WITH required(table_name, reason) AS (
 VALUES
 ('workspaces','owner scope'),('datasets','dataset scope'),
 ('users','current user scope'),('upload_requests','delivery listing'),
 ('physical_files','delivery listing'),
 ('dataset_state_versions','dataset analytics readiness'),
 ('delivery_applications','dataset analytics readiness'),
 ('dataset_load_policies','dataset analytics readiness'),
 ('dataset_gold_runs','dataset analytics readiness')
)
SELECT r.table_name, r.reason,
       to_regclass('public.' || r.table_name) IS NOT NULL AS exists,
       CASE WHEN to_regclass('public.' || r.table_name) IS NOT NULL
            THEN has_table_privilege(current_user, 'public.' || r.table_name, 'SELECT')
            ELSE NULL END AS can_select
FROM required r ORDER BY r.table_name;
