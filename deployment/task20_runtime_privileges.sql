-- MANUAL REVIEW/APPLICATION ONLY. Not discovered by migration fixtures.
-- Required psql identifier variables: schema, authority_owner, api_role,
-- worker_role, verifier_role, dispatch_role. Roles must ALREADY exist.
-- authority_owner: NOLOGIN, not granted/inherited by any runtime role.
-- Runtime roles: LOGIN/NOSUPERUSER/NOCREATEDB/NOCREATEROLE, not table owners.
-- Preserve existing reviewed auth/API CRUD grants; this file covers Task 20.
BEGIN;
SET LOCAL search_path TO :"schema", pg_catalog;
SELECT set_config('datarise_task20.authority', :'authority_owner', true),
 set_config('datarise_task20.api', :'api_role', true),
 set_config('datarise_task20.worker', :'worker_role', true),
 set_config('datarise_task20.verifier', :'verifier_role', true),
 set_config('datarise_task20.dispatch', :'dispatch_role', true);
DO $$
DECLARE names TEXT[]:=ARRAY[current_setting('datarise_task20.authority'),current_setting('datarise_task20.api'),
 current_setting('datarise_task20.worker'),current_setting('datarise_task20.verifier'),current_setting('datarise_task20.dispatch')];
 name TEXT; r RECORD;
BEGIN
 IF (SELECT count(DISTINCT n) FROM unnest(names) n)<>5 THEN RAISE EXCEPTION 'Task 20 identities must be distinct'; END IF;
 FOREACH name IN ARRAY names LOOP
  SELECT * INTO r FROM pg_roles WHERE rolname=name;
  IF NOT FOUND OR r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolbypassrls THEN
   RAISE EXCEPTION 'Task 20 requires existing unprivileged identities'; END IF;
  IF name=names[1] THEN
   IF r.rolcanlogin THEN RAISE EXCEPTION 'Authority owner must be NOLOGIN'; END IF;
  ELSE
   IF NOT r.rolcanlogin OR pg_has_role(name,names[1],'MEMBER') THEN
    RAISE EXCEPTION 'Runtime cannot inherit authority ownership'; END IF;
  END IF;
 END LOOP;
END $$;
REVOKE CREATE ON SCHEMA :"schema" FROM PUBLIC, :"api_role", :"worker_role", :"verifier_role", :"dispatch_role";
GRANT USAGE ON SCHEMA :"schema" TO :"api_role", :"worker_role", :"verifier_role", :"dispatch_role", :"authority_owner";
REVOKE ALL ON processing_stage_intents,processing_artifact_candidates,processing_stage_receipts,
 processing_stage_heads,processing_source_objects,processing_job_inputs,processing_admission_pins,
 processing_dependency_epochs,processing_dispatch_outbox,processing_job_stage_attempts FROM PUBLIC, :"api_role", :"worker_role", :"verifier_role", :"dispatch_role";
GRANT ALL ON processing_stage_intents,processing_artifact_candidates,processing_stage_receipts,
 processing_stage_heads,processing_source_objects,processing_job_inputs,processing_admission_pins,
 processing_dependency_epochs,processing_dispatch_outbox,processing_job_stage_attempts TO :"authority_owner";
GRANT SELECT,UPDATE ON processing_jobs,upload_requests,users,workspaces,datasets,physical_files,
 dataset_versions,dataset_version_files,approved_rule_policies,dataset_profiles,
 warehouse_model_configurations,delivery_applications,dataset_state_versions TO :"authority_owner";
GRANT SELECT ON data_quality_runs,processing_attempts,dataset_load_policies,snapshot_delivery_contexts TO :"authority_owner";
GRANT INSERT ON processing_jobs TO :"authority_owner";
GRANT UPDATE ON dataset_state_versions,delivery_applications TO :"authority_owner";
-- No direct candidate status/evidence/receipt/head writes by ANY runtime.
GRANT SELECT ON processing_stage_intents,processing_artifact_candidates,processing_stage_receipts,
 processing_stage_heads,processing_source_objects,processing_job_inputs,processing_admission_pins,
 processing_dependency_epochs TO :"worker_role", :"verifier_role";
GRANT SELECT ON processing_job_stage_attempts TO :"worker_role", :"verifier_role";
GRANT INSERT ON processing_stage_intents,processing_job_inputs,processing_job_stage_attempts TO :"worker_role";
GRANT INSERT(candidate_id,intent_id,manifest_sha256,manifest_identity,artifact_identities)
 ON processing_artifact_candidates TO :"worker_role";
-- Worker still uses existing repository functions for stage metadata, not algorithms in SQL.
GRANT SELECT,UPDATE ON processing_jobs TO :"worker_role";
GRANT SELECT ON users,workspaces,datasets,physical_files,upload_requests,approved_rule_policies,dataset_load_policies,
 warehouse_model_configurations TO :"worker_role", :"verifier_role";
GRANT SELECT ON business_rules,business_rule_answers TO :"worker_role";
GRANT SELECT ON snapshot_delivery_contexts TO :"worker_role";
GRANT UPDATE(schema_hash,status) ON physical_files TO :"worker_role";
GRANT UPDATE(state_analytics_status) ON datasets TO :"worker_role";
GRANT SELECT,INSERT,UPDATE ON processing_attempts,dataset_profiles,dataset_profile_summaries,
 dataset_versions,dataset_version_files,data_quality_runs,data_quality_issues,gold_runs,
 gold_artifacts,gold_artifact_models,gold_artifact_columns,delivery_applications,
 dataset_state_versions,delivery_manifests TO :"worker_role";
GRANT SELECT ON processing_attempts,data_quality_runs TO :"verifier_role";
-- Only sequences owned by columns of the stage metadata tables written above.
-- Do not grant access to unrelated user/auth/workspace sequences.
DO $$
DECLARE seq TEXT;
BEGIN
 FOR seq IN SELECT DISTINCT pg_get_serial_sequence(format('%I.%I',current_schema(),c.relname),a.attname)
  FROM pg_class c JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
  WHERE c.relnamespace=current_schema()::regnamespace AND c.relname=ANY(ARRAY[
   'processing_attempts','dataset_profiles','dataset_profile_summaries','dataset_versions','dataset_version_files',
   'data_quality_runs','data_quality_issues','gold_runs','gold_artifacts','gold_artifact_models',
   'gold_artifact_columns','delivery_applications','dataset_state_versions','delivery_manifests']) LOOP
  IF seq IS NOT NULL THEN
   EXECUTE format('GRANT USAGE,SELECT ON SEQUENCE %s TO %I',seq,current_setting('datarise_task20.worker'));
  END IF;
 END LOOP;
END $$;
-- Functions pin a trusted search_path in migration 0043; PUBLIC EXECUTE is revoked.
ALTER FUNCTION task20_require_job(UUID,UUID) OWNER TO :"authority_owner";
ALTER FUNCTION task20_require_input(UUID,UUID,JSONB) OWNER TO :"authority_owner";
ALTER FUNCTION task20_source(BIGINT,BIGINT) OWNER TO :"authority_owner";
ALTER FUNCTION task20_admit(BIGINT,BIGINT,TEXT,TEXT) OWNER TO :"authority_owner";
ALTER FUNCTION task20_register_source(BIGINT,BIGINT,JSONB) OWNER TO :"authority_owner";
ALTER FUNCTION task20_managed(BIGINT,BIGINT) OWNER TO :"authority_owner";
ALTER FUNCTION task20_verify(UUID,UUID,UUID,JSONB) OWNER TO :"authority_owner";
ALTER FUNCTION task20_publish(UUID,UUID,UUID) OWNER TO :"authority_owner";
ALTER FUNCTION task20_publish_incremental(UUID,UUID,JSONB,BIGINT) OWNER TO :"authority_owner";
ALTER FUNCTION task20_claim_dispatch() OWNER TO :"authority_owner";
ALTER FUNCTION task20_dispatched(UUID,UUID) OWNER TO :"authority_owner";
ALTER FUNCTION task20_schedule_job() OWNER TO :"authority_owner";
ALTER FUNCTION task20_dependency_changed() OWNER TO :"authority_owner";
ALTER FUNCTION task20_protect_attempt() OWNER TO :"authority_owner";
ALTER FUNCTION task20_protect_admitted_job() OWNER TO :"authority_owner";
ALTER FUNCTION task20_file_managed(BIGINT) OWNER TO :"authority_owner";
ALTER FUNCTION task20_policy(BIGINT,BIGINT) OWNER TO :"authority_owner";
ALTER FUNCTION task20_applied_state(BIGINT,BIGINT,BIGINT) OWNER TO :"authority_owner";
GRANT EXECUTE ON FUNCTION task20_admit(BIGINT,BIGINT,TEXT,TEXT),task20_register_source(BIGINT,BIGINT,JSONB),
 task20_managed(BIGINT,BIGINT),task20_file_managed(BIGINT) TO :"api_role";
GRANT EXECUTE ON FUNCTION task20_source(BIGINT,BIGINT),task20_require_job(UUID,UUID),
 task20_require_input(UUID,UUID,JSONB),task20_publish(UUID,UUID,UUID),
 task20_publish_incremental(UUID,UUID,JSONB,BIGINT),task20_applied_state(BIGINT,BIGINT,BIGINT) TO :"worker_role";
GRANT EXECUTE ON FUNCTION task20_verify(UUID,UUID,UUID,JSONB),task20_require_input(UUID,UUID,JSONB) TO :"verifier_role";
GRANT EXECUTE ON FUNCTION task20_claim_dispatch(),task20_dispatched(UUID,UUID) TO :"dispatch_role";
COMMIT;
-- Before enablement verify current_user/rolsuper/role memberships, pg_proc.proacl,
-- function ownership/search_path and has_table/column_privilege for EACH login.
-- Also audit/revoke existing column-level grants and inherited grants. Revoking
-- table privileges alone does not remove independent column privileges.
-- Do NOT deploy verifier credentials in the API or worker container.
