-- Additive Task 20 integration. Apply only after reviewed deployment approval.
BEGIN;
ALTER TABLE processing_stage_intents ADD COLUMN input_contract JSONB;
ALTER TABLE processing_stage_intents ADD COLUMN expected_receipt_id UUID;
ALTER TABLE processing_artifact_candidates ADD COLUMN manifest_identity JSONB;
ALTER TABLE processing_artifact_candidates ADD COLUMN artifact_identities JSONB;
ALTER TABLE processing_artifact_candidates ADD COLUMN verification_report JSONB;
ALTER TABLE processing_stage_receipts ADD COLUMN generation BIGINT NOT NULL DEFAULT 1;
ALTER TABLE processing_stage_heads ADD COLUMN generation BIGINT NOT NULL DEFAULT 1;
CREATE TABLE processing_source_objects (
 file_id BIGINT PRIMARY KEY REFERENCES physical_files(file_id), identity JSONB NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE processing_dependency_epochs(dataset_id BIGINT PRIMARY KEY REFERENCES datasets(dataset_id), revision BIGINT NOT NULL DEFAULT 0);
CREATE TABLE processing_job_stage_attempts (
 job_id UUID NOT NULL REFERENCES processing_jobs(job_id), lease_token UUID NOT NULL,
 stage TEXT NOT NULL CHECK(stage IN ('BRONZE','SILVER','GOLD')),
 attempt_id BIGINT NOT NULL UNIQUE REFERENCES processing_attempts(attempt_id),
 PRIMARY KEY(job_id,lease_token,stage)
);
CREATE TABLE processing_job_inputs (
 job_id UUID PRIMARY KEY REFERENCES processing_jobs(job_id), input_contract JSONB NOT NULL,
 input_fingerprint CHAR(64) NOT NULL CHECK(input_fingerprint ~ '^[a-f0-9]{64}$')
);
CREATE TABLE processing_admission_pins (
 job_id UUID PRIMARY KEY REFERENCES processing_jobs(job_id), pins JSONB NOT NULL
);
CREATE TABLE processing_dispatch_outbox (
 job_id UUID PRIMARY KEY REFERENCES processing_jobs(job_id), available_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 claim_token UUID, claim_until TIMESTAMPTZ, delivered_at TIMESTAMPTZ,
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0)
);
CREATE FUNCTION task20_policy(d BIGINT,u BIGINT) RETURNS JSONB
LANGUAGE SQL SECURITY DEFINER SET search_path FROM CURRENT AS $$
 SELECT to_jsonb(p) FROM dataset_load_policies p WHERE p.dataset_id=d
 AND p.policy_id=coalesce((SELECT policy_id FROM delivery_applications WHERE upload_request_id=u),
 (SELECT policy_id FROM dataset_load_policies WHERE dataset_id=d ORDER BY policy_version DESC LIMIT 1))
$$;
CREATE FUNCTION task20_applied_state(d BIGINT,u BIGINT,policy BIGINT) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $$
DECLARE result JSONB;
BEGIN
 IF policy IS NULL THEN RETURN NULL; END IF;
 SELECT jsonb_build_object('application',a.application_id,'result_state',s.state_id,'current_state',ds.current_state_id)
 INTO result FROM delivery_applications a JOIN dataset_state_versions s ON s.state_id=a.result_state_id
 JOIN datasets ds ON ds.dataset_id=a.dataset_id
 WHERE a.upload_request_id=u AND a.dataset_id=d AND a.policy_id=policy AND a.status='SUCCESS'
 AND s.status='PUBLISHED' FOR SHARE OF a,s,ds;
 IF result IS NULL THEN RAISE EXCEPTION 'Committed dataset application required before Gold'; END IF;
 RETURN result;
END $$;
CREATE FUNCTION task20_schedule_job() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $$
BEGIN
 INSERT INTO processing_dependency_epochs(dataset_id) VALUES(NEW.dataset_id) ON CONFLICT DO NOTHING;
 IF NEW.status IN ('QUEUED','RETRY_WAIT') OR
    (NEW.status='RUNNING' AND (TG_OP='INSERT' OR NEW.lease_expires_at IS DISTINCT FROM OLD.lease_expires_at)) THEN
  INSERT INTO processing_dispatch_outbox(job_id,available_at) VALUES(NEW.job_id,
   CASE WHEN NEW.status='RUNNING' THEN NEW.lease_expires_at ELSE NEW.available_at END)
  ON CONFLICT(job_id) DO UPDATE SET available_at=EXCLUDED.available_at,delivered_at=NULL,
    claim_token=NULL,claim_until=NULL;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER task20_job_dispatch AFTER INSERT OR UPDATE ON processing_jobs FOR EACH ROW EXECUTE FUNCTION task20_schedule_job();

CREATE FUNCTION task20_require_job(j UUID,t UUID) RETURNS processing_jobs
LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $$
DECLARE x processing_jobs; eligible BIGINT;
BEGIN
 SELECT * INTO x FROM processing_jobs WHERE job_id=j FOR UPDATE;
 IF NOT FOUND OR x.status<>'RUNNING' OR x.lease_token IS DISTINCT FROM t
    OR x.lease_expires_at<=clock_timestamp() THEN RAISE EXCEPTION 'Execution lease unavailable'; END IF;
 SELECT u.upload_id INTO eligible FROM upload_requests u
 JOIN datasets d ON d.dataset_id=u.dataset_id AND d.workspace_id=u.workspace_id
 JOIN workspaces w ON w.workspace_id=d.workspace_id
 JOIN users a ON a.owner_key=d.owner AND a.owner_key=w.owner
 JOIN physical_files f ON f.file_id=u.file_id
 WHERE u.upload_id=x.upload_id AND a.user_id=x.requested_by AND a.account_status='ACTIVE'
 AND u.status='UPLOADED' AND u.archived_at IS NULL AND u.file_id=x.file_id
 AND u.dataset_id=x.dataset_id AND u.workspace_id=x.workspace_id
 AND (u.staging_object_key IS NULL OR (u.completed_at IS NOT NULL AND u.completion_result IS NOT NULL))
 FOR SHARE OF u,d,w,a,f;
 IF eligible IS NULL OR x.lease_expires_at<=clock_timestamp() THEN RAISE EXCEPTION 'Current ownership unavailable'; END IF;
 RETURN x;
END $$;

CREATE FUNCTION task20_require_input(j UUID,t UUID,c JSONB) RETURNS VOID
LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $$
DECLARE x processing_jobs; source JSONB; rules JSONB; upstream UUID; up_objects JSONB; up_manifest JSONB; profile JSONB; models JSONB; v BIGINT; epoch BIGINT;
BEGIN
 x:=task20_require_job(j,t);
 IF NOT EXISTS(SELECT 1 FROM processing_admission_pins WHERE job_id=j) THEN RAISE EXCEPTION 'Durable admission required'; END IF;
 IF c->>'version' IS DISTINCT FROM '1' OR (c->>'file_id')::bigint IS DISTINCT FROM x.file_id
 OR (c->>'dataset_id')::bigint IS DISTINCT FROM x.dataset_id
 OR (c->>'upload_id')::bigint IS DISTINCT FROM x.upload_id THEN RAISE EXCEPTION 'Input scope mismatch'; END IF;
 IF EXISTS(SELECT 1 FROM processing_job_inputs p WHERE p.job_id=j AND p.input_contract IS DISTINCT FROM c) THEN
  RAISE EXCEPTION 'Immutable job input changed'; END IF;
 IF EXISTS(SELECT 1 FROM processing_admission_pins p WHERE p.job_id=j AND
    (CASE WHEN c->>'stage'='BRONZE' THEN jsonb_build_object('stage',c->>'stage','source',c->'source')
     ELSE jsonb_build_object('stage',c->>'stage','association',c->'association','rule_version',c->'rule_version',
       'rules',c->'rules','upstream_receipt',c->'upstream_receipt','load_policy',c->'load_policy',
       'snapshot_context',c->'snapshot_context') || CASE WHEN c->>'stage'='GOLD'
       THEN jsonb_build_object('profiles',c->'profiles','models',c->'models','dependency_epoch',c->'dependency_epoch','applied_state',c->'applied_state') ELSE '{}'::jsonb END END)
     IS DISTINCT FROM p.pins) THEN RAISE EXCEPTION 'Admission input version changed'; END IF;
 IF x.operation IS DISTINCT FROM c->>'stage' THEN RAISE EXCEPTION 'Stage operation mismatch'; END IF;
 IF c->>'stage'='SILVER' AND (SELECT identity FROM processing_source_objects WHERE file_id=x.file_id) IS DISTINCT FROM c->'raw_source' THEN RAISE EXCEPTION 'Raw identity differs'; END IF;
 IF c->>'stage'='BRONZE' THEN
  SELECT identity INTO source FROM processing_source_objects WHERE file_id=x.file_id FOR SHARE;
  IF source IS NULL OR source IS DISTINCT FROM c->'source' OR NOT EXISTS(
   SELECT 1 FROM physical_files f WHERE f.file_id=x.file_id AND f.file_hash=source->>'sha256'
    AND f.file_size=(source->>'size')::bigint
    AND substring(f.storage_path from '^s3://[^/]+/(.*)$')=source->>'key')
   THEN RAISE EXCEPTION 'Physical source identity changed'; END IF;
 ELSE
  IF c->'load_policy' IS NOT NULL AND c->'load_policy'<>'null'::jsonb AND NOT EXISTS(SELECT 1 FROM dataset_load_policies p WHERE p.policy_id=(c->'load_policy'->>'policy_id')::bigint AND to_jsonb(p)=c->'load_policy') THEN RAISE EXCEPTION 'Pinned load policy changed'; END IF;
  IF c->'load_policy'->>'load_strategy'='SNAPSHOT' AND NOT EXISTS(
   SELECT 1 FROM snapshot_delivery_contexts s WHERE s.upload_request_id=x.upload_id AND to_jsonb(s)=c->'snapshot_context')
   THEN RAISE EXCEPTION 'Pinned snapshot declaration changed'; END IF;
  SELECT dv.dataset_version_id,p.rules INTO v,rules FROM dataset_version_files f
   JOIN dataset_versions dv ON dv.dataset_version_id=f.dataset_version_id
   JOIN physical_files pf ON pf.file_id=f.file_id
   JOIN approved_rule_policies p ON p.dataset_version_id=dv.dataset_version_id AND p.rule_version=f.applied_rule_version
   WHERE f.dataset_version_file_id=(c->>'association')::bigint AND f.file_id=x.file_id
   AND dv.dataset_id=x.dataset_id AND dv.schema_hash=pf.schema_hash
   AND f.applied_rule_version=(c->>'rule_version')::int FOR SHARE OF f,dv,p;
  IF v IS NULL OR v<>(c->>'dataset_version')::bigint OR rules IS DISTINCT FROM c->'rules' OR jsonb_array_length(rules)=0
    THEN RAISE EXCEPTION 'Approved input policy changed'; END IF;
  SELECT h.receipt_id,candidate.artifact_identities,candidate.manifest_identity
   INTO upstream,up_objects,up_manifest FROM processing_stage_heads h
   JOIN processing_artifact_candidates candidate ON candidate.candidate_id=h.candidate_id WHERE h.file_id=x.file_id
   AND h.stage=CASE WHEN c->>'stage'='SILVER' THEN 'BRONZE' ELSE 'SILVER' END
   AND h.dataset_version_file_id IS NOT DISTINCT FROM CASE WHEN c->>'stage'='GOLD' THEN (c->>'association')::bigint ELSE NULL END
   FOR SHARE OF h,candidate;
  IF upstream IS NULL OR upstream::text IS DISTINCT FROM c->>'upstream_receipt'
   OR up_objects IS DISTINCT FROM c->'upstream_artifacts' OR up_manifest IS DISTINCT FROM c->'upstream_manifest'
   THEN RAISE EXCEPTION 'Upstream publication changed'; END IF;
  IF c->>'stage'='GOLD' THEN
   IF task20_applied_state(x.dataset_id,x.upload_id,(c->'load_policy'->>'policy_id')::bigint)
       IS DISTINCT FROM nullif(c->'applied_state','null'::jsonb) THEN RAISE EXCEPTION 'Dataset state dependency changed'; END IF;
   SELECT revision INTO epoch FROM processing_dependency_epochs WHERE dataset_id=x.dataset_id FOR SHARE;
   IF epoch IS DISTINCT FROM (c->>'dependency_epoch')::bigint THEN RAISE EXCEPTION 'Dependency generation changed'; END IF;
   IF NOT EXISTS(SELECT 1 FROM data_quality_runs dq JOIN processing_attempts a ON a.attempt_id=dq.attempt_id
      WHERE dq.dq_run_id=(c->>'dq_run')::bigint AND dq.dataset_version_file_id=(c->>'association')::bigint
      AND dq.rule_version=(c->>'rule_version')::int AND a.status='SUCCESS') THEN RAISE EXCEPTION 'Silver DQ unavailable'; END IF;
   SELECT coalesce(jsonb_agg(to_jsonb(p) ORDER BY p.column_name),'[]'::jsonb) INTO profile FROM dataset_profiles p WHERE p.file_id=x.file_id;
   SELECT coalesce(jsonb_agg(to_jsonb(m) ORDER BY m.warehouse_model_version_id),'[]'::jsonb) INTO models
    FROM warehouse_model_configurations m WHERE m.source_dataset_id=x.dataset_id;
   IF profile IS DISTINCT FROM c->'profiles' OR models IS DISTINCT FROM c->'models' THEN RAISE EXCEPTION 'Model dependencies changed'; END IF;
  END IF;
 END IF;
 PERFORM task20_require_job(j,t);
END $$;

CREATE FUNCTION task20_verify(j UUID,t UUID,cid UUID,report JSONB) RETURNS VOID
LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $$
DECLARE c processing_artifact_candidates; i processing_stage_intents;
BEGIN
 PERFORM task20_require_job(j,t);
 SELECT * INTO c FROM processing_artifact_candidates WHERE candidate_id=cid FOR UPDATE;
 SELECT * INTO i FROM processing_stage_intents WHERE intent_id=c.intent_id FOR UPDATE;
 IF i.job_id IS DISTINCT FROM j OR i.lease_token IS DISTINCT FROM t OR i.stage IS DISTINCT FROM i.input_contract->>'stage'
  OR i.file_id IS DISTINCT FROM (i.input_contract->>'file_id')::bigint
  OR i.dataset_version_file_id IS DISTINCT FROM (i.input_contract->>'association')::bigint OR c.status<>'STAGED'
  OR c.manifest_identity IS NULL OR c.artifact_identities IS NULL
  OR report->>'manifest_sha256' IS DISTINCT FROM c.manifest_sha256
  OR report->'manifest_identity' IS DISTINCT FROM c.manifest_identity
  OR report->'artifacts' IS DISTINCT FROM c.artifact_identities THEN RAISE EXCEPTION 'Verification evidence mismatch'; END IF;
 IF NOT EXISTS(SELECT 1 FROM processing_job_stage_attempts l JOIN processing_attempts a ON a.attempt_id=l.attempt_id
  WHERE l.job_id=j AND l.lease_token=t AND l.stage=i.stage AND a.status='PROCESSING') AND NOT (i.stage='GOLD' AND report->'parquet' ? 'skip') THEN RAISE EXCEPTION 'Live stage attempt required'; END IF;
 PERFORM task20_require_input(j,t,i.input_contract);
 UPDATE processing_artifact_candidates SET status='VERIFIED',verified_at=clock_timestamp(),verification_report=report WHERE candidate_id=cid;
 PERFORM task20_require_job(j,t);
END $$;

CREATE FUNCTION task20_publish(j UUID,t UUID,cid UUID) RETURNS UUID
LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $$
DECLARE c processing_artifact_candidates; i processing_stage_intents; h processing_stage_heads; rid UUID; n BIGINT;
BEGIN
 PERFORM task20_require_job(j,t);
 SELECT * INTO c FROM processing_artifact_candidates WHERE candidate_id=cid FOR UPDATE;
 SELECT * INTO i FROM processing_stage_intents WHERE intent_id=c.intent_id FOR UPDATE;
 IF i.job_id IS DISTINCT FROM j OR i.lease_token IS DISTINCT FROM t OR i.stage IS DISTINCT FROM i.input_contract->>'stage'
  OR i.file_id IS DISTINCT FROM (i.input_contract->>'file_id')::bigint
  OR i.dataset_version_file_id IS DISTINCT FROM (i.input_contract->>'association')::bigint
  OR c.status<>'VERIFIED' OR c.verification_report IS NULL
   THEN RAISE EXCEPTION 'Verified current candidate required'; END IF;
 IF NOT EXISTS(SELECT 1 FROM processing_job_stage_attempts l JOIN processing_attempts a ON a.attempt_id=l.attempt_id
  WHERE l.job_id=j AND l.lease_token=t AND l.stage=i.stage AND a.status IN ('PROCESSING','SUCCESS')) AND NOT (i.stage='GOLD' AND c.verification_report->'parquet' ? 'skip') THEN RAISE EXCEPTION 'Current stage attempt required'; END IF;
 PERFORM task20_require_input(j,t,i.input_contract);
 -- Serialize absent and present heads for this physical/logical scope.
 PERFORM pg_advisory_xact_lock(hashtextextended(('processing_stage_heads'::regclass)::oid::text||':task20-head:'||i.stage||':'||i.file_id||':'||coalesce(i.dataset_version_file_id,0),0));
 SELECT * INTO h FROM processing_stage_heads WHERE stage=i.stage AND file_id=i.file_id
  AND dataset_version_file_id IS NOT DISTINCT FROM i.dataset_version_file_id FOR UPDATE;
 SELECT receipt_id INTO rid FROM processing_stage_receipts WHERE candidate_id=cid;
 IF rid IS NOT NULL THEN PERFORM task20_require_job(j,t); RETURN rid; END IF;
 IF h.receipt_id IS DISTINCT FROM i.expected_receipt_id THEN RAISE EXCEPTION 'Publication generation conflict'; END IF;
 n:=coalesce(h.generation,0)+1;
 INSERT INTO processing_stage_receipts(intent_id,candidate_id,job_id,lease_token,stage,file_id,dataset_version_file_id,generation)
 VALUES(i.intent_id,cid,j,t,i.stage,i.file_id,i.dataset_version_file_id,n) RETURNING receipt_id INTO rid;
 INSERT INTO processing_stage_heads(stage,file_id,dataset_version_file_id,receipt_id,candidate_id,generation)
 VALUES(i.stage,i.file_id,i.dataset_version_file_id,rid,cid,n)
 ON CONFLICT ON CONSTRAINT uq_stage_head_scope DO UPDATE SET receipt_id=EXCLUDED.receipt_id,
 candidate_id=EXCLUDED.candidate_id,generation=EXCLUDED.generation,updated_at=clock_timestamp();
 PERFORM task20_require_job(j,t);
 RETURN rid;
END $$;
-- No PUBLIC authority for SECURITY DEFINER operations. Explicit deployment grants
-- are in deployment/task20_runtime_privileges.sql; no roles are created here.
REVOKE ALL ON FUNCTION task20_require_job(UUID,UUID),task20_require_input(UUID,UUID,JSONB),
 task20_verify(UUID,UUID,UUID,JSONB),task20_publish(UUID,UUID,UUID) FROM PUBLIC;
CREATE FUNCTION task20_protect_attempt() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $$
BEGIN
 IF OLD.status<>'PROCESSING' AND NEW IS DISTINCT FROM OLD AND EXISTS(SELECT 1 FROM processing_job_stage_attempts WHERE attempt_id=OLD.attempt_id) THEN
  RAISE EXCEPTION 'Completed durable stage attempt is immutable'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER task20_attempt_transition BEFORE UPDATE ON processing_attempts FOR EACH ROW EXECUTE FUNCTION task20_protect_attempt();
CREATE TRIGGER task20_immutable_job_inputs BEFORE UPDATE OR DELETE ON processing_job_inputs FOR EACH ROW EXECUTE FUNCTION protect_processing_stage_receipt();
CREATE TRIGGER task20_immutable_admission BEFORE UPDATE OR DELETE ON processing_admission_pins FOR EACH ROW EXECUTE FUNCTION protect_processing_stage_receipt();
CREATE TRIGGER task20_immutable_source BEFORE UPDATE OR DELETE ON processing_source_objects
 FOR EACH ROW EXECUTE FUNCTION protect_processing_stage_receipt();
CREATE FUNCTION task20_preserve_registry() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_TABLE_NAME='processing_stage_intents' THEN
  IF OLD.input_contract IS NOT NULL AND (to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD)) THEN
   RAISE EXCEPTION 'Registered stage input is immutable'; END IF;
 ELSE
  IF TG_OP='INSERT' THEN
   IF NEW.manifest_identity IS NOT NULL AND (NEW.status<>'STAGED' OR NEW.verification_report IS NOT NULL) THEN
    RAISE EXCEPTION 'Registered candidates must enter STAGED'; END IF;
  ELSE
   IF NEW.manifest_identity IS DISTINCT FROM OLD.manifest_identity
      OR NEW.artifact_identities IS DISTINCT FROM OLD.artifact_identities
      OR (OLD.verification_report IS NOT NULL AND NEW.verification_report IS DISTINCT FROM OLD.verification_report)
    THEN RAISE EXCEPTION 'Registered object evidence is immutable'; END IF;
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER task20_input_identity BEFORE UPDATE ON processing_stage_intents FOR EACH ROW EXECUTE FUNCTION task20_preserve_registry();
CREATE TRIGGER task20_object_identity BEFORE INSERT OR UPDATE ON processing_artifact_candidates FOR EACH ROW EXECUTE FUNCTION task20_preserve_registry();

CREATE FUNCTION task20_source(actor BIGINT,upload BIGINT) RETURNS TABLE(workspace_id BIGINT,dataset_id BIGINT,file_id BIGINT)
LANGUAGE SQL SECURITY DEFINER SET search_path FROM CURRENT AS $$
 SELECT u.workspace_id,u.dataset_id,u.file_id FROM upload_requests u
 JOIN datasets d ON d.dataset_id=u.dataset_id AND d.workspace_id=u.workspace_id
 JOIN workspaces w ON w.workspace_id=d.workspace_id
 JOIN users a ON a.owner_key=d.owner AND a.owner_key=w.owner
 JOIN physical_files f ON f.file_id=u.file_id
 WHERE u.upload_id=upload AND a.user_id=actor AND a.account_status='ACTIVE'
 AND u.status='UPLOADED' AND u.archived_at IS NULL
 AND (u.staging_object_key IS NULL OR (u.completed_at IS NOT NULL AND u.completion_result IS NOT NULL))
 FOR SHARE OF u,d,w,a,f
$$;
CREATE FUNCTION task20_admit(actor BIGINT,upload BIGINT,op TEXT,idem TEXT) RETURNS UUID
LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $$
DECLARE src RECORD; existing processing_jobs; id UUID; pins JSONB; a RECORD; upstream UUID; profile JSONB; models JSONB; epoch BIGINT;
BEGIN
 IF op NOT IN ('BRONZE','SILVER','GOLD') OR idem IS NULL OR length(btrim(idem)) NOT BETWEEN 1 AND 128 THEN
  RAISE EXCEPTION 'Invalid stage admission'; END IF;
 SELECT * INTO src FROM task20_source(actor,upload);
 IF NOT FOUND THEN RAISE EXCEPTION 'Upload admission denied' USING ERRCODE='42501'; END IF;
 INSERT INTO processing_jobs(requested_by,upload_id,workspace_id,dataset_id,file_id,operation,idempotency_key)
 VALUES(actor,upload,src.workspace_id,src.dataset_id,src.file_id,op,idem)
 ON CONFLICT(requested_by,operation,idempotency_key) DO NOTHING RETURNING job_id INTO id;
 IF id IS NULL THEN
  SELECT * INTO existing FROM processing_jobs WHERE requested_by=actor AND processing_jobs.operation=op
   AND idempotency_key=idem;
  IF existing.upload_id<>upload OR existing.file_id<>src.file_id OR existing.dataset_id<>src.dataset_id OR existing.workspace_id<>src.workspace_id
   THEN RAISE EXCEPTION 'Idempotency scope conflict' USING ERRCODE='23505'; END IF;
  id:=existing.job_id;
 END IF;
 IF NOT EXISTS(SELECT 1 FROM processing_admission_pins WHERE job_id=id) THEN
  IF op='BRONZE' THEN
   SELECT jsonb_build_object('stage',op,'source',identity) INTO pins FROM processing_source_objects WHERE file_id=src.file_id;
   IF pins IS NULL THEN RAISE EXCEPTION 'Version-pinned upload required'; END IF;
  ELSE
   SELECT f.dataset_version_file_id,f.applied_rule_version,p.rules INTO a FROM dataset_version_files f
    JOIN dataset_versions v ON v.dataset_version_id=f.dataset_version_id
    JOIN physical_files pf ON pf.file_id=f.file_id AND pf.schema_hash=v.schema_hash
    JOIN approved_rule_policies p ON p.dataset_version_id=v.dataset_version_id AND p.rule_version=f.applied_rule_version
    WHERE f.file_id=src.file_id AND v.dataset_id=src.dataset_id FOR SHARE OF f,v,p;
   IF NOT FOUND THEN RAISE EXCEPTION 'Approved stage admission required'; END IF;
   SELECT receipt_id INTO upstream FROM processing_stage_heads WHERE file_id=src.file_id
    AND stage=CASE WHEN op='SILVER' THEN 'BRONZE' ELSE 'SILVER' END
    AND dataset_version_file_id IS NOT DISTINCT FROM CASE WHEN op='GOLD' THEN a.dataset_version_file_id ELSE NULL END;
   IF upstream IS NULL THEN RAISE EXCEPTION 'Published upstream required'; END IF;
   pins:=jsonb_build_object('stage',op,'association',a.dataset_version_file_id,'rule_version',a.applied_rule_version,
    'rules',a.rules,'upstream_receipt',upstream,'load_policy',task20_policy(src.dataset_id,upload),'snapshot_context',NULL);
   IF pins->'load_policy'->>'load_strategy'='SNAPSHOT' THEN
    pins:=pins||jsonb_build_object('snapshot_context',(SELECT to_jsonb(s) FROM snapshot_delivery_contexts s WHERE s.upload_request_id=upload));
    IF pins->'snapshot_context'='null'::jsonb THEN RAISE EXCEPTION 'Snapshot declaration required before admission'; END IF;
   END IF;
   IF op='GOLD' THEN
    SELECT revision INTO epoch FROM processing_dependency_epochs WHERE dataset_id=src.dataset_id FOR SHARE;
    SELECT coalesce(jsonb_agg(to_jsonb(p) ORDER BY p.column_name),'[]'::jsonb) INTO profile FROM dataset_profiles p WHERE p.file_id=src.file_id;
    SELECT coalesce(jsonb_agg(to_jsonb(m) ORDER BY m.warehouse_model_version_id),'[]'::jsonb) INTO models
     FROM warehouse_model_configurations m WHERE m.source_dataset_id=src.dataset_id;
    pins:=pins||jsonb_build_object('profiles',profile,'models',models,'dependency_epoch',epoch,
      'applied_state',task20_applied_state(src.dataset_id,upload,(pins->'load_policy'->>'policy_id')::bigint));
   END IF;
  END IF;
  INSERT INTO processing_admission_pins VALUES(id,pins);
 END IF;
 RETURN id;
END $$;
CREATE FUNCTION task20_claim_dispatch() RETURNS TABLE(job_id UUID,claim_token UUID)
LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $$
BEGIN
 RETURN QUERY WITH ready AS (SELECT o.job_id FROM processing_dispatch_outbox o
 JOIN processing_jobs j ON j.job_id=o.job_id WHERE o.available_at<=clock_timestamp()
 AND o.delivered_at IS NULL AND (o.claim_until IS NULL OR o.claim_until<=clock_timestamp())
 AND j.status IN ('QUEUED','RETRY_WAIT','RUNNING') ORDER BY o.available_at FOR UPDATE OF o SKIP LOCKED LIMIT 1)
 UPDATE processing_dispatch_outbox o SET claim_token=gen_random_uuid(),claim_until=clock_timestamp()+interval '60 seconds',
 attempts=attempts+1 FROM ready WHERE o.job_id=ready.job_id RETURNING o.job_id,o.claim_token;
END $$;
CREATE FUNCTION task20_dispatched(j UUID,t UUID) RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $$
BEGIN
 UPDATE processing_dispatch_outbox SET delivered_at=clock_timestamp(),claim_until=NULL
 WHERE job_id=j AND claim_token=t AND claim_until>clock_timestamp();
 RETURN FOUND;
END $$;

CREATE FUNCTION task20_publish_incremental(j UUID,t UUID,contract JSONB,app_id BIGINT) RETURNS VOID
LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $$
DECLARE job processing_jobs; app delivery_applications; state dataset_state_versions; prior BIGINT;
BEGIN
 job:=task20_require_job(j,t);
 PERFORM task20_require_input(j,t,contract);
 SELECT * INTO app FROM delivery_applications WHERE application_id=app_id FOR UPDATE;
 IF app.upload_request_id IS DISTINCT FROM job.upload_id OR app.dataset_id<>job.dataset_id
  OR app.dataset_version_file_id IS DISTINCT FROM (contract->>'association')::bigint
  OR app.dataset_version_id IS DISTINCT FROM (contract->>'dataset_version')::bigint
  OR app.applied_rule_version IS DISTINCT FROM (contract->>'rule_version')::integer
  OR app.policy_id IS DISTINCT FROM (contract->'load_policy'->>'policy_id')::bigint THEN RAISE EXCEPTION 'Application scope mismatch'; END IF;
 IF app.status='SUCCESS' THEN RETURN; END IF;
 IF app.status<>'RUNNING' THEN RAISE EXCEPTION 'Running application required'; END IF;
 SELECT * INTO state FROM dataset_state_versions WHERE source_application_id=app_id AND status='VALIDATED' ORDER BY state_id DESC LIMIT 1 FOR UPDATE;
 IF NOT FOUND OR state.manifest_key NOT LIKE 'silver/dataset_id='||job.dataset_id||'/dataset_version_id='||app.dataset_version_id||'/state/application_id='||app_id||'/%'
  THEN RAISE EXCEPTION 'Validated scoped candidate required'; END IF;
 SELECT current_state_id INTO prior FROM datasets WHERE dataset_id=job.dataset_id FOR UPDATE;
 IF prior IS DISTINCT FROM state.previous_state_id OR (prior IS NOT NULL AND NOT EXISTS(
   SELECT 1 FROM dataset_state_versions p WHERE p.state_id=prior AND p.dataset_version_id=app.dataset_version_id AND p.policy_id=app.policy_id))
  THEN RAISE EXCEPTION 'Trusted state generation changed'; END IF;
 UPDATE dataset_state_versions SET status='PUBLISHED',published_at=clock_timestamp() WHERE dataset_state_versions.state_id=state.state_id;
 UPDATE datasets SET current_state_id=state.state_id,state_analytics_status='STALE' WHERE dataset_id=job.dataset_id;
 UPDATE delivery_applications SET status='SUCCESS',completed_at=clock_timestamp(),result_state_id=state.state_id,
  current_state_rows=state.row_count,failure_code=NULL WHERE application_id=app_id;
 PERFORM task20_require_job(j,t);
END $$;


CREATE FUNCTION task20_register_source(actor BIGINT,upload BIGINT,obj JSONB) RETURNS VOID
LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $$
DECLARE src RECORD; physical physical_files;
BEGIN
 SELECT * INTO src FROM task20_source(actor,upload);
 IF NOT FOUND THEN RAISE EXCEPTION 'Source authorization unavailable'; END IF;
 SELECT * INTO physical FROM physical_files WHERE file_id=src.file_id FOR SHARE;
 IF EXISTS(SELECT 1 FROM processing_source_objects WHERE file_id=src.file_id) THEN RETURN; END IF;
 IF jsonb_typeof(obj)<>'object' OR obj->>'sha256' IS DISTINCT FROM physical.file_hash OR (obj->>'size')::bigint IS DISTINCT FROM physical.file_size
 OR obj->>'key' IS DISTINCT FROM substring(physical.storage_path from '^s3://[^/]+/(.*)$')
 OR coalesce(obj->>'version','') IN ('','null') THEN RAISE EXCEPTION 'Raw version registration mismatch'; END IF;
 INSERT INTO processing_source_objects(file_id,identity) VALUES(src.file_id,obj);
END $$;
CREATE FUNCTION task20_managed(actor BIGINT,upload BIGINT) RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $$
DECLARE src RECORD;
BEGIN
 SELECT * INTO src FROM task20_source(actor,upload);
 IF NOT FOUND THEN RAISE EXCEPTION 'Source authorization unavailable'; END IF;
 RETURN EXISTS(SELECT 1 FROM processing_source_objects WHERE file_id=src.file_id)
     OR EXISTS(SELECT 1 FROM processing_stage_heads WHERE file_id=src.file_id);
END $$;
CREATE FUNCTION task20_file_managed(f BIGINT) RETURNS BOOLEAN
LANGUAGE SQL SECURITY DEFINER SET search_path FROM CURRENT AS $$
 SELECT EXISTS(SELECT 1 FROM processing_source_objects WHERE file_id=f)
     OR EXISTS(SELECT 1 FROM processing_stage_heads WHERE file_id=f)
$$;
CREATE FUNCTION task20_protect_admitted_job() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $$
BEGIN
 IF (NEW.requested_by,NEW.upload_id,NEW.workspace_id,NEW.dataset_id,NEW.file_id,NEW.operation,NEW.idempotency_key,NEW.max_attempts)
 IS DISTINCT FROM (OLD.requested_by,OLD.upload_id,OLD.workspace_id,OLD.dataset_id,OLD.file_id,OLD.operation,OLD.idempotency_key,OLD.max_attempts)
 AND EXISTS(SELECT 1 FROM processing_admission_pins WHERE job_id=OLD.job_id)
 THEN RAISE EXCEPTION 'Admitted job identity is immutable'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER task20_job_identity BEFORE UPDATE ON processing_jobs FOR EACH ROW EXECUTE FUNCTION task20_protect_admitted_job();

CREATE FUNCTION task20_dependency_changed() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $$
DECLARE d BIGINT; f BIGINT; old_f BIGINT; v BIGINT;
BEGIN
 IF TG_TABLE_NAME='warehouse_model_configurations' THEN
  FOR d IN SELECT DISTINCT unnest(ARRAY[
   CASE WHEN TG_OP='DELETE' THEN NULL ELSE NEW.source_dataset_id END,
   CASE WHEN TG_OP='INSERT' THEN NULL ELSE OLD.source_dataset_id END]) LOOP
   IF d IS NOT NULL THEN
    INSERT INTO processing_dependency_epochs VALUES(d,1) ON CONFLICT(dataset_id) DO UPDATE SET revision=processing_dependency_epochs.revision+1;
   END IF;
  END LOOP;
 ELSIF TG_TABLE_NAME='approved_rule_policies' THEN
  v:=CASE WHEN TG_OP='DELETE' THEN OLD.dataset_version_id ELSE NEW.dataset_version_id END;
  SELECT dataset_id INTO d FROM dataset_versions WHERE dataset_version_id=v;
  INSERT INTO processing_dependency_epochs VALUES(d,1) ON CONFLICT(dataset_id) DO UPDATE SET revision=processing_dependency_epochs.revision+1;
 ELSE
  f:=CASE WHEN TG_OP='DELETE' THEN OLD.file_id ELSE NEW.file_id END;
  old_f:=CASE WHEN TG_OP='INSERT' THEN NULL ELSE OLD.file_id END;
  FOR d IN SELECT DISTINCT v.dataset_id FROM dataset_versions v JOIN dataset_version_files a ON a.dataset_version_id=v.dataset_version_id WHERE a.file_id IN (f,old_f) LOOP
   INSERT INTO processing_dependency_epochs VALUES(d,1) ON CONFLICT(dataset_id) DO UPDATE SET revision=processing_dependency_epochs.revision+1;
  END LOOP;
 END IF;
 RETURN NULL;
END $$;
CREATE TRIGGER task20_profile_epoch AFTER INSERT OR UPDATE OR DELETE ON dataset_profiles FOR EACH ROW EXECUTE FUNCTION task20_dependency_changed();
CREATE TRIGGER task20_rule_epoch AFTER INSERT OR UPDATE OR DELETE ON approved_rule_policies FOR EACH ROW EXECUTE FUNCTION task20_dependency_changed();
CREATE TRIGGER task20_model_epoch AFTER INSERT OR UPDATE OR DELETE ON warehouse_model_configurations FOR EACH ROW EXECUTE FUNCTION task20_dependency_changed();
-- Freeze each function's trusted schema and exclude caller-controlled pg_temp.
DO $$ DECLARE p RECORD; trusted TEXT:=current_schema(); BEGIN
 FOR p IN SELECT oid::regprocedure AS signature FROM pg_proc WHERE pronamespace=to_regnamespace(trusted)
  AND proname LIKE 'task20_%' LOOP
   EXECUTE format('ALTER FUNCTION %s SET search_path = pg_catalog, %I, pg_temp',p.signature,trusted);
   EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',p.signature);
 END LOOP;
END $$;
COMMIT;
