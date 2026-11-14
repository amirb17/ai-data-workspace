-- Authenticated SQL metadata authority; no direct API table writes.
BEGIN;
CREATE FUNCTION task23_sql_owner(actor BIGINT,ws BIGINT)
RETURNS TEXT LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE owner_name TEXT;
BEGIN
 SELECT w.owner INTO owner_name FROM users u
 JOIN workspaces w ON w.owner=u.owner_key
 WHERE u.user_id=actor AND u.account_status='ACTIVE'
 AND w.workspace_id=ws AND w.status='ACTIVE';
 IF owner_name IS NULL THEN RAISE EXCEPTION 'SQL workspace unavailable' USING ERRCODE='42501'; END IF;
 RETURN owner_name;
END $$;

CREATE FUNCTION task23_sql_record(actor BIGINT,ws BIGINT,sqid BIGINT,
 query_text TEXT,digest TEXT,metadata JSONB,history_limit INTEGER)
RETURNS TABLE(execution_id BIGINT,created_at TIMESTAMPTZ)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE owner_name TEXT;
BEGIN
 owner_name:=task23_sql_owner(actor,ws);
 IF length(query_text) NOT BETWEEN 1 AND 20000 OR digest !~ '^[0-9a-f]{64}$'
    OR metadata->>'status' NOT IN ('SUCCEEDED','BLOCKED','FAILED','TIMED_OUT')
    OR history_limit NOT BETWEEN 1 AND 500
 THEN RAISE EXCEPTION 'Invalid SQL execution envelope'; END IF;
 PERFORM 1 FROM workspaces WHERE workspace_id=ws AND owner=owner_name FOR UPDATE;
 IF sqid IS NOT NULL AND NOT EXISTS(
   SELECT 1 FROM saved_sql_queries
   WHERE query_id=sqid AND workspace_id=ws AND owner_key=owner_name)
 THEN RAISE EXCEPTION 'Saved SQL query unavailable'; END IF;
 RETURN QUERY
 INSERT INTO sql_query_executions(workspace_id,owner_key,saved_query_id,sql_text,query_hash,payload)
 VALUES(ws,owner_name,sqid,query_text,digest,metadata)
 RETURNING sql_query_executions.execution_id,sql_query_executions.created_at;
 DELETE FROM sql_query_executions WHERE workspace_id=ws AND owner_key=owner_name
 AND sql_query_executions.execution_id IN (
   SELECT q.execution_id FROM sql_query_executions q
   WHERE q.workspace_id=ws AND q.owner_key=owner_name
   ORDER BY q.execution_id DESC OFFSET history_limit);
END $$;

CREATE FUNCTION task23_sql_save(actor BIGINT,ws BIGINT,qid BIGINT,
 query_name TEXT,query_description TEXT,query_text TEXT,expected_version INTEGER,
 max_saved INTEGER)
RETURNS SETOF saved_sql_queries LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE owner_name TEXT; current_version INTEGER;
BEGIN
 owner_name:=task23_sql_owner(actor,ws);
 IF length(query_name) NOT BETWEEN 1 AND 120
 OR length(query_description)>1000 OR length(query_text) NOT BETWEEN 1 AND 20000
 OR max_saved NOT BETWEEN 1 AND 500
 THEN RAISE EXCEPTION 'Invalid saved SQL query'; END IF;
 PERFORM 1 FROM workspaces WHERE workspace_id=ws AND owner=owner_name FOR UPDATE;
 IF qid IS NOT NULL THEN
   SELECT version INTO current_version FROM saved_sql_queries
   WHERE query_id=qid AND workspace_id=ws AND owner_key=owner_name FOR UPDATE;
   IF current_version IS NULL OR current_version IS DISTINCT FROM expected_version
   THEN RAISE EXCEPTION 'Saved SQL query changed'; END IF;
   RETURN QUERY UPDATE saved_sql_queries SET name=query_name,
     description=query_description,sql_text=query_text,version=version+1,updated_at=NOW()
     WHERE query_id=qid AND workspace_id=ws AND owner_key=owner_name RETURNING *;
 ELSE
   IF (SELECT count(*) FROM saved_sql_queries WHERE workspace_id=ws AND owner_key=owner_name)>=max_saved
   THEN RAISE EXCEPTION 'Saved SQL query limit reached'; END IF;
   RETURN QUERY INSERT INTO saved_sql_queries(workspace_id,owner_key,name,description,sql_text)
    VALUES(ws,owner_name,query_name,query_description,query_text) RETURNING *;
 END IF;
END $$;

CREATE FUNCTION task23_sql_delete(actor BIGINT,ws BIGINT,qid BIGINT,expected_version INTEGER)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE owner_name TEXT; current_version INTEGER;
BEGIN
 owner_name:=task23_sql_owner(actor,ws);
 PERFORM 1 FROM workspaces WHERE workspace_id=ws AND owner=owner_name FOR UPDATE;
 SELECT version INTO current_version FROM saved_sql_queries
 WHERE query_id=qid AND workspace_id=ws AND owner_key=owner_name FOR UPDATE;
 IF current_version IS NULL OR current_version IS DISTINCT FROM expected_version
 THEN RAISE EXCEPTION 'Saved SQL query changed'; END IF;
 DELETE FROM saved_sql_queries WHERE query_id=qid AND workspace_id=ws AND owner_key=owner_name;
 RETURN TRUE;
END $$;

REVOKE ALL ON FUNCTION task23_sql_owner(BIGINT,BIGINT),
 task23_sql_record(BIGINT,BIGINT,BIGINT,TEXT,TEXT,JSONB,INTEGER),
 task23_sql_save(BIGINT,BIGINT,BIGINT,TEXT,TEXT,TEXT,INTEGER,INTEGER),
 task23_sql_delete(BIGINT,BIGINT,BIGINT,INTEGER) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION
 task23_sql_record(BIGINT,BIGINT,BIGINT,TEXT,TEXT,JSONB,INTEGER),
 task23_sql_save(BIGINT,BIGINT,BIGINT,TEXT,TEXT,TEXT,INTEGER,INTEGER),
 task23_sql_delete(BIGINT,BIGINT,BIGINT,INTEGER) TO datarise_api;
GRANT SELECT ON TABLE public.saved_sql_queries,public.sql_query_executions TO datarise_api;
COMMIT;
