-- PROPOSED, once-only after 0043. Local disposable-schema validation only.
-- No runtime grants. Existing SQS jobs retain implicit SQS routing.
BEGIN;
CREATE TABLE processing_batch_routes (
 job_id UUID PRIMARY KEY REFERENCES processing_jobs(job_id),
 configuration JSONB NOT NULL,
 fingerprint CHAR(64) NOT NULL CHECK(fingerprint ~ '^[a-f0-9]{64}$'),
 max_submissions INTEGER NOT NULL CHECK(max_submissions BETWEEN 1 AND 3),
 submissions INTEGER NOT NULL DEFAULT 0 CHECK(submissions>=0 AND submissions<=max_submissions),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE processing_batch_submissions (
 submission_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 job_id UUID NOT NULL REFERENCES processing_batch_routes(job_id),
 ordinal INTEGER NOT NULL CHECK(ordinal BETWEEN 1 AND 3),
 state TEXT NOT NULL CHECK(state IN ('CLAIMED','UNCERTAIN','OBSERVED','RETIRED')),
 claim_token UUID NOT NULL DEFAULT gen_random_uuid(),
 claim_until TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()+interval '60 seconds',
 reconcile_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()+interval '60 seconds',
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(job_id,ordinal)
);
CREATE UNIQUE INDEX processing_batch_one_open ON processing_batch_submissions(job_id) WHERE state<>'RETIRED';
CREATE TABLE processing_batch_provider_jobs (
 provider_id TEXT PRIMARY KEY CHECK(length(provider_id) BETWEEN 1 AND 128),
 submission_id UUID NOT NULL REFERENCES processing_batch_submissions(submission_id),
 status TEXT NOT NULL CHECK(status IN ('ACTIVE','SUCCEEDED','FAILED')),
 observed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

-- Lock the job as well as the outbox: routing and an SQS claim serialize before
-- an external call. Do not claim a Batch job and filter it in Python afterwards.
CREATE OR REPLACE FUNCTION task20_claim_dispatch() RETURNS TABLE(job_id UUID,claim_token UUID)
LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $$
DECLARE chosen UUID;
BEGIN
 SELECT o.job_id INTO chosen FROM processing_dispatch_outbox o
 JOIN processing_jobs j ON j.job_id=o.job_id WHERE o.available_at<=clock_timestamp()
 AND NOT EXISTS(SELECT 1 FROM processing_batch_routes b WHERE b.job_id=j.job_id)
 AND o.delivered_at IS NULL AND (o.claim_until IS NULL OR o.claim_until<=clock_timestamp())
 AND j.status IN ('QUEUED','RETRY_WAIT','RUNNING') ORDER BY o.available_at
 FOR UPDATE OF j,o SKIP LOCKED LIMIT 1;
 -- A route may have committed after the SELECT's MVCC snapshot but before its
 -- job lock was obtained. Recheck in a fresh statement while owning both locks.
 IF chosen IS NULL OR EXISTS(SELECT 1 FROM processing_batch_routes b WHERE b.job_id=chosen) THEN RETURN; END IF;
 RETURN QUERY UPDATE processing_dispatch_outbox o SET claim_token=gen_random_uuid(),claim_until=clock_timestamp()+interval '60 seconds',
 attempts=attempts+1 WHERE o.job_id=chosen RETURNING o.job_id,o.claim_token;
END $$;

CREATE FUNCTION task158c_route(j UUID,c JSONB,f TEXT,b INTEGER) RETURNS VOID
LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $$
DECLARE x processing_jobs; old processing_batch_routes; o processing_dispatch_outbox;
BEGIN
 SELECT * INTO x FROM processing_jobs WHERE job_id=j FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'Job unavailable'; END IF;
 SELECT * INTO old FROM processing_batch_routes WHERE job_id=j;
 IF FOUND THEN
  IF old.configuration IS DISTINCT FROM c OR old.fingerprint<>f OR old.max_submissions<>b THEN
   RAISE EXCEPTION 'Immutable runtime route conflict'; END IF;
  RETURN;
 END IF;
 SELECT * INTO o FROM processing_dispatch_outbox WHERE job_id=j FOR UPDATE;
 IF x.operation NOT IN ('BRONZE','SILVER','GOLD') OR x.status<>'QUEUED' OR x.attempt_count<>0
 OR o.job_id IS NULL OR o.attempts<>0 OR o.claim_token IS NOT NULL OR o.delivered_at IS NOT NULL
 OR NOT EXISTS(SELECT 1 FROM processing_admission_pins WHERE job_id=j) THEN
  RAISE EXCEPTION 'Only fresh durably admitted jobs can select Batch'; END IF;
 INSERT INTO processing_batch_routes(job_id,configuration,fingerprint,max_submissions) VALUES(j,c,f,b);
END $$;

CREATE FUNCTION task158c_claim() RETURNS TABLE(submission_id UUID,job_id UUID,claim_token UUID,configuration JSONB,fingerprint CHAR(64))
LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $$
DECLARE x processing_jobs; r processing_batch_routes; src RECORD; s processing_batch_submissions;
BEGIN
 SELECT j.* INTO x FROM processing_jobs j JOIN processing_batch_routes b ON b.job_id=j.job_id
 WHERE j.status IN ('QUEUED','RETRY_WAIT','RUNNING') AND j.available_at<=clock_timestamp()
 AND (j.status<>'RUNNING' OR j.lease_expires_at<=clock_timestamp())
 AND b.submissions<b.max_submissions
 AND NOT EXISTS(SELECT 1 FROM processing_batch_submissions a WHERE a.job_id=j.job_id AND a.state<>'RETIRED')
 ORDER BY j.created_at FOR UPDATE OF j SKIP LOCKED LIMIT 1;
 IF NOT FOUND THEN RETURN; END IF;
 SELECT * INTO r FROM processing_batch_routes b WHERE b.job_id=x.job_id FOR UPDATE;
 SELECT * INTO src FROM task20_source(x.requested_by,x.upload_id);
 IF src.workspace_id IS DISTINCT FROM x.workspace_id OR src.dataset_id IS DISTINCT FROM x.dataset_id
 OR src.file_id IS DISTINCT FROM x.file_id THEN
  UPDATE processing_jobs SET status='FAILED',failure_code='SOURCE_INELIGIBLE',lease_token=NULL,
   lease_expires_at=NULL,completed_at=clock_timestamp(),updated_at=clock_timestamp() WHERE processing_jobs.job_id=x.job_id;
  RETURN;
 END IF;
 IF x.attempt_count>=x.max_attempts THEN
  UPDATE processing_jobs SET status='FAILED',failure_code='LEASE_EXPIRED',lease_token=NULL,
   lease_expires_at=NULL,completed_at=clock_timestamp(),updated_at=clock_timestamp() WHERE processing_jobs.job_id=x.job_id;
  RETURN;
 END IF;
 UPDATE processing_batch_routes b SET submissions=b.submissions+1 WHERE b.job_id=x.job_id RETURNING * INTO r;
 INSERT INTO processing_batch_submissions(job_id,ordinal,state) VALUES(x.job_id,r.submissions,'CLAIMED') RETURNING * INTO s;
 RETURN QUERY SELECT s.submission_id,x.job_id,s.claim_token,r.configuration,r.fingerprint;
END $$;

-- Submission response CAS. A crash/expired claim is uncertainty, never permission
-- to resend. Late responses may only add correlated provider history via observe.
CREATE FUNCTION task158c_response(s UUID,t UUID,p TEXT,kind TEXT) RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $$
DECLARE a processing_batch_submissions;
BEGIN
 SELECT * INTO a FROM processing_batch_submissions WHERE submission_id=s FOR UPDATE;
 IF NOT FOUND OR a.state<>'CLAIMED' OR a.claim_token<>t OR a.claim_until<=clock_timestamp() THEN RETURN FALSE; END IF;
 IF kind NOT IN ('OBSERVED','UNCERTAIN','RETIRED') THEN RAISE EXCEPTION 'Invalid submission response'; END IF;
 IF kind='OBSERVED' THEN
  IF p IS NULL THEN RAISE EXCEPTION 'Provider identity required'; END IF;
  IF EXISTS(SELECT 1 FROM processing_batch_provider_jobs h WHERE h.provider_id=p AND h.submission_id<>s) THEN
   RAISE EXCEPTION 'Provider correlation conflict'; END IF;
  -- A reconciler may observe the provider task before submit returns. Preserve
  -- that evidence (including terminal status) instead of failing the response.
  INSERT INTO processing_batch_provider_jobs VALUES(p,s,'ACTIVE',clock_timestamp())
  ON CONFLICT(provider_id) DO UPDATE SET observed_at=clock_timestamp()
   WHERE processing_batch_provider_jobs.submission_id=s;
  IF NOT FOUND THEN RAISE EXCEPTION 'Provider correlation conflict'; END IF;
  IF (SELECT count(*) FROM processing_batch_provider_jobs WHERE submission_id=s)>32 THEN
   RAISE EXCEPTION 'Provider history bound exceeded'; END IF;
 ELSIF p IS NOT NULL THEN RAISE EXCEPTION 'Unexpected provider identity'; END IF;
 UPDATE processing_batch_submissions SET state=kind,reconcile_at=clock_timestamp()+interval '30 seconds' WHERE submission_id=s;
 RETURN TRUE;
END $$;

CREATE FUNCTION task158c_reconcile(s UUID,observations JSONB) RETURNS TEXT
LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $$
DECLARE x processing_jobs; a processing_batch_submissions; item JSONB; src RECORD; n INTEGER;
BEGIN
 SELECT j.* INTO x FROM processing_jobs j JOIN processing_batch_submissions b ON b.job_id=j.job_id
 WHERE b.submission_id=s FOR UPDATE OF j;
 IF NOT FOUND THEN RETURN 'NOT_FOUND'; END IF;
 SELECT * INTO a FROM processing_batch_submissions WHERE submission_id=s FOR UPDATE;
 IF jsonb_typeof(observations)<>'array' OR jsonb_array_length(observations)>32 THEN
  RAISE EXCEPTION 'Bounded provider observations required'; END IF;
 FOR item IN SELECT value FROM jsonb_array_elements(observations) LOOP
  IF item->>'status' NOT IN ('ACTIVE','SUCCEEDED','FAILED') OR item->>'id' IS NULL THEN
   RAISE EXCEPTION 'Invalid provider observation'; END IF;
  IF EXISTS(SELECT 1 FROM processing_batch_provider_jobs p WHERE p.provider_id=item->>'id' AND p.submission_id<>s) THEN
   RAISE EXCEPTION 'Provider correlation conflict'; END IF;
  INSERT INTO processing_batch_provider_jobs(provider_id,submission_id,status) VALUES(item->>'id',s,item->>'status')
  ON CONFLICT(provider_id) DO UPDATE SET status=CASE WHEN processing_batch_provider_jobs.status='ACTIVE'
   THEN EXCLUDED.status ELSE processing_batch_provider_jobs.status END,observed_at=clock_timestamp()
   WHERE processing_batch_provider_jobs.submission_id=s;
  IF NOT FOUND THEN RAISE EXCEPTION 'Provider correlation conflict'; END IF;
 END LOOP;
 IF (SELECT count(*) FROM processing_batch_provider_jobs WHERE submission_id=s)>32 THEN
  RAISE EXCEPTION 'Provider history bound exceeded'; END IF;
 -- SQL terminal authority always dominates notifications or provider status.
 IF x.status IN ('SUCCEEDED','FAILED','CANCELLED') THEN
  UPDATE processing_batch_submissions SET state='RETIRED' WHERE submission_id=s;
  RETURN CASE WHEN x.status='SUCCEEDED' THEN 'COMPLETED' ELSE 'TERMINAL' END;
 END IF;
 SELECT * INTO src FROM task20_source(x.requested_by,x.upload_id);
 IF src.workspace_id IS DISTINCT FROM x.workspace_id OR src.dataset_id IS DISTINCT FROM x.dataset_id
 OR src.file_id IS DISTINCT FROM x.file_id THEN
  UPDATE processing_jobs SET status='FAILED',failure_code='SOURCE_INELIGIBLE',lease_token=NULL,
   lease_expires_at=NULL,completed_at=clock_timestamp(),updated_at=clock_timestamp() WHERE job_id=x.job_id;
  UPDATE processing_batch_submissions SET state='RETIRED' WHERE submission_id=s;
  RETURN 'TERMINAL';
 END IF;
 UPDATE processing_batch_submissions SET reconcile_at=clock_timestamp()+interval '30 seconds',
 state=CASE WHEN state='CLAIMED' AND claim_until<=clock_timestamp() THEN 'UNCERTAIN' ELSE state END
 WHERE submission_id=s RETURNING * INTO a;
 IF x.status='RUNNING' AND x.lease_expires_at>clock_timestamp() THEN RETURN 'LEASE_ACTIVE'; END IF;
 IF a.state='CLAIMED' THEN RETURN 'SUBMITTING'; END IF;
 IF a.state='UNCERTAIN' THEN RETURN 'UNCERTAIN'; END IF;
 IF a.state='RETIRED' THEN RETURN 'RETIRED'; END IF;
 SELECT count(*) INTO n FROM processing_batch_provider_jobs WHERE submission_id=s;
 IF n=0 OR EXISTS(SELECT 1 FROM processing_batch_provider_jobs WHERE submission_id=s AND status='ACTIVE') THEN
  RETURN 'PROVIDER_ACTIVE'; END IF;
 -- A successful container is not job completion. Only after every known provider
 -- ended and no SQL lease remains may a new bounded submission be considered.
 UPDATE processing_batch_submissions SET state='RETIRED' WHERE submission_id=s;
 IF x.attempt_count>=x.max_attempts THEN
  UPDATE processing_jobs SET status='FAILED',failure_code='LEASE_EXPIRED',lease_token=NULL,
   lease_expires_at=NULL,completed_at=clock_timestamp(),updated_at=clock_timestamp() WHERE job_id=x.job_id;
  RETURN 'TERMINAL';
 END IF;
 IF (SELECT submissions>=max_submissions FROM processing_batch_routes WHERE job_id=x.job_id) THEN RETURN 'BUDGET_EXHAUSTED'; END IF;
 RETURN 'RETRY_READY';
END $$;

CREATE FUNCTION task158c_cancel(j UUID,actor BIGINT) RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $$
DECLARE x processing_jobs; src RECORD;
BEGIN
 SELECT * INTO x FROM processing_jobs WHERE job_id=j FOR UPDATE;
 IF NOT FOUND THEN RETURN FALSE; END IF;
 SELECT * INTO src FROM task20_source(actor,x.upload_id);
 IF src.workspace_id IS DISTINCT FROM x.workspace_id OR src.dataset_id IS DISTINCT FROM x.dataset_id
 OR src.file_id IS DISTINCT FROM x.file_id THEN RAISE EXCEPTION 'Cancellation ownership unavailable'; END IF;
 IF x.status IN ('SUCCEEDED','FAILED','CANCELLED') THEN RETURN FALSE; END IF;
 UPDATE processing_jobs SET status='CANCELLED',lease_token=NULL,lease_expires_at=NULL,
  completed_at=clock_timestamp(),updated_at=clock_timestamp() WHERE job_id=j;
 RETURN TRUE;
END $$;

REVOKE ALL ON processing_batch_routes,processing_batch_submissions,processing_batch_provider_jobs FROM PUBLIC;
REVOKE ALL ON FUNCTION task158c_route(UUID,JSONB,TEXT,INTEGER),task158c_claim(),
 task158c_response(UUID,UUID,TEXT,TEXT),task158c_reconcile(UUID,JSONB),task158c_cancel(UUID,BIGINT) FROM PUBLIC;
DO $$ DECLARE p RECORD; trusted TEXT:=current_schema(); BEGIN
 FOR p IN SELECT oid::regprocedure AS signature FROM pg_proc WHERE pronamespace=to_regnamespace(trusted)
 AND (proname LIKE 'task158c_%' OR proname='task20_claim_dispatch') LOOP
  EXECUTE format('ALTER FUNCTION %s SET search_path = pg_catalog, %I, pg_temp',p.signature,trusted);
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',p.signature);
 END LOOP;
END $$;
COMMIT;
