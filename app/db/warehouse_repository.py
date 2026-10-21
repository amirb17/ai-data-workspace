"""Warehouse metadata SQL only. Callers own authorization and transactions."""
from uuid import uuid4
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


def cursor(conn):
    return conn.cursor(row_factory=dict_row)


def workspace(conn, workspace_id):
    return cursor(conn).execute('SELECT * FROM workspaces WHERE workspace_id=%s FOR SHARE', (workspace_id,)).fetchone()


def model(conn, workspace_id, model_id, lock=False):
    return cursor(conn).execute('SELECT * FROM warehouse_models WHERE workspace_id=%s AND warehouse_model_id=%s' +
                               (' FOR UPDATE' if lock else ' FOR SHARE'), (workspace_id, model_id)).fetchone()


def models(conn, workspace_id):
    return cursor(conn).execute('SELECT * FROM warehouse_models WHERE workspace_id=%s ORDER BY warehouse_model_id', (workspace_id,)).fetchall()


def create_model(conn, workspace_id, actor, request):
    return cursor(conn).execute('''INSERT INTO warehouse_models(workspace_id,display_name,normalized_name,key_namespace,created_by)
        VALUES (%s,%s,%s,%s,%s) RETURNING *''',
        (workspace_id, request.display_name, request.normalized_name, uuid4(), actor)).fetchone()


def version(conn, workspace_id, model_id, version_id):
    return cursor(conn).execute('''SELECT * FROM warehouse_model_versions WHERE workspace_id=%s
        AND warehouse_model_id=%s AND warehouse_model_version_id=%s FOR UPDATE''', (workspace_id, model_id, version_id)).fetchone()


def create_version(conn, workspace_id, model_id, actor, request):
    latest = cursor(conn).execute('SELECT COALESCE(MAX(version_number),0) AS n FROM warehouse_model_versions WHERE warehouse_model_id=%s', (model_id,)).fetchone()['n']
    if latest != request.expected_version:
        raise ValueError('Model version changed; reload before creating a version')
    return cursor(conn).execute('''INSERT INTO warehouse_model_versions(workspace_id,warehouse_model_id,version_number,
        configuration_version,key_algorithm_version,key_strategy,key_storage_type,created_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
        (workspace_id, model_id, latest+1, request.configuration_version, request.key_algorithm_version,
         request.key_strategy, request.key_storage_type, actor)).fetchone()


def sources(conn, version_id):
    return cursor(conn).execute('SELECT * FROM warehouse_model_sources WHERE warehouse_model_version_id=%s ORDER BY source_mapping_id', (version_id,)).fetchall()


def add_source(conn, workspace_id, model_id, version_id, request):
    return cursor(conn).execute('''INSERT INTO warehouse_model_sources(workspace_id,warehouse_model_id,warehouse_model_version_id,source_dataset_id,purpose)
        VALUES (%s,%s,%s,%s,%s) RETURNING *''', (workspace_id, model_id, version_id, request.source_dataset_id, request.purpose)).fetchone()


def dataset(conn, dataset_id):
    return cursor(conn).execute('SELECT * FROM datasets WHERE dataset_id=%s FOR SHARE', (dataset_id,)).fetchone()


def remove_source(conn, version_id, mapping_id):
    return conn.execute('DELETE FROM warehouse_model_sources WHERE warehouse_model_version_id=%s AND source_mapping_id=%s',
                        (version_id, mapping_id)).rowcount == 1


def approve(conn, model_id, version_id, actor):
    # The model row is locked by the service; replacement is atomic to readers.
    conn.execute("UPDATE warehouse_model_versions SET status='ARCHIVED' WHERE warehouse_model_id=%s AND status='APPROVED'", (model_id,))
    row = cursor(conn).execute("""UPDATE warehouse_model_versions SET status='APPROVED',reviewed_by=%s,reviewed_at=NOW()
        WHERE warehouse_model_version_id=%s RETURNING *""", (actor, version_id)).fetchone()
    conn.execute("UPDATE warehouse_models SET status='APPROVED' WHERE warehouse_model_id=%s AND status='DRAFT'", (model_id,))
    return row


def archive(conn, model_id):
    conn.execute("UPDATE warehouse_model_versions SET status='ARCHIVED' WHERE warehouse_model_id=%s AND status<>'ARCHIVED'", (model_id,))
    return cursor(conn).execute("UPDATE warehouse_models SET status='ARCHIVED',archived_at=NOW() WHERE warehouse_model_id=%s RETURNING *", (model_id,)).fetchone()


def resource(conn, workspace_id, model_id, version_id, resource_id):
    return cursor(conn).execute('''SELECT * FROM warehouse_resources WHERE workspace_id=%s AND warehouse_model_id=%s
        AND warehouse_model_version_id=%s AND resource_id=%s FOR SHARE''', (workspace_id, model_id, version_id, resource_id)).fetchone()


def resources(conn, workspace_id, model_id, version_id):
    return cursor(conn).execute('''SELECT * FROM warehouse_resources WHERE workspace_id=%s AND warehouse_model_id=%s
        AND warehouse_model_version_id=%s ORDER BY resource_id''', (workspace_id, model_id, version_id)).fetchall()


def register_resource(conn, workspace_id, model_id, version_id, actor, request):
    return cursor(conn).execute('''INSERT INTO warehouse_resources(workspace_id,warehouse_model_id,warehouse_model_version_id,
        provider,environment,location,generation,physical_reference,created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
        (workspace_id, model_id, version_id, request.provider, request.environment, request.location,
         request.generation, Jsonb(request.physical_reference.model_dump()), actor)).fetchone()


def create_operation(conn, workspace_id, model_id, version_id, environment, actor, request):
    return cursor(conn).execute('''INSERT INTO warehouse_operations(workspace_id,warehouse_model_id,warehouse_model_version_id,
        resource_id,environment,operation_type,reason,created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
        (workspace_id, model_id, version_id, request.resource_id, environment, request.operation_type, request.reason, actor)).fetchone()


def operation(conn, workspace_id, operation_id):
    return cursor(conn).execute('SELECT * FROM warehouse_operations WHERE workspace_id=%s AND operation_id=%s FOR UPDATE', (workspace_id, operation_id)).fetchone()


def new_attempt(conn, workspace_id, operation_id, actor):
    return cursor(conn).execute('''INSERT INTO warehouse_runs(workspace_id,operation_id,attempt_number,created_by)
        VALUES (%s,%s,(SELECT COALESCE(MAX(attempt_number),0)+1 FROM warehouse_runs WHERE operation_id=%s),%s) RETURNING *''',
        (workspace_id, operation_id, operation_id, actor)).fetchone()


def run(conn, workspace_id, operation_id, run_id):
    return cursor(conn).execute('''SELECT * FROM warehouse_runs WHERE workspace_id=%s AND operation_id=%s
        AND warehouse_run_id=%s FOR UPDATE''', (workspace_id, operation_id, run_id)).fetchone()


def transition(conn, run_id, request):
    return cursor(conn).execute('''UPDATE warehouse_runs SET status=%s,
        started_at=CASE WHEN %s='RUNNING' THEN NOW() ELSE started_at END,
        completed_at=CASE WHEN %s IN ('SUCCEEDED','FAILED') THEN NOW() ELSE NULL END,
        failure_code=%s WHERE warehouse_run_id=%s RETURNING *''',
        (request.status, request.status, request.status, request.failure_code, run_id)).fetchone()


def modeling_available(conn):
    return conn.execute("SELECT to_regclass('warehouse_model_configurations') IS NOT NULL").fetchone()[0]


def modeling(conn, workspace_id, model_id, version_id):
    return cursor(conn).execute('''SELECT * FROM warehouse_model_configurations WHERE workspace_id=%s
        AND warehouse_model_id=%s AND warehouse_model_version_id=%s''', (workspace_id, model_id, version_id)).fetchone()


def source_contract(conn, dataset_id, policy_id, rule_version):
    return cursor(conn).execute('''SELECT p.*,r.rules FROM dataset_load_policies p
        JOIN dataset_versions v ON v.dataset_version_id=p.dataset_version_id AND v.dataset_id=p.dataset_id
        JOIN approved_rule_policies r ON r.dataset_version_id=v.dataset_version_id AND r.rule_version=%s
        WHERE p.policy_id=%s AND p.dataset_id=%s AND v.schema_hash=p.schema_hash''', (rule_version, policy_id, dataset_id)).fetchone()


def save_modeling(conn, workspace_id, model_id, version_id, dataset_id, contract, actor, request, schema, rule_hash):
    return cursor(conn).execute('''INSERT INTO warehouse_model_configurations(warehouse_model_version_id,workspace_id,
        warehouse_model_id,source_dataset_id,source_dataset_version_id,source_policy_id,source_rule_version,model_type,
        configuration,source_schema,source_rule_sha256,created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
        (version_id,workspace_id,model_id,dataset_id,contract['dataset_version_id'],request.source_policy_id,request.source_rule_version,
         request.configuration.model_type,Jsonb(request.configuration.model_dump(mode='json')),Jsonb(schema.model_dump(mode='json')),
         rule_hash,actor)).fetchone()
