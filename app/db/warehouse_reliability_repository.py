"""SQL primitives; caller owns authorization and the PostgreSQL transaction."""
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


def one(conn, query, args=()):
    return conn.cursor(row_factory=dict_row).execute(query, args).fetchone()


def all_rows(conn, query, args=()):
    return conn.cursor(row_factory=dict_row).execute(query, args).fetchall()


def journal(conn, run, fence, stage, evidence):
    conn.execute('''INSERT INTO warehouse_run_journal(warehouse_run_id,fence,stage,evidence)
                    VALUES (%s,%s,%s,%s)''', (run, fence, stage, Jsonb(evidence)))


def manifests(conn, mapping):
    return all_rows(conn, 'SELECT * FROM warehouse_accepted_manifests WHERE source_mapping_id=%s ORDER BY position', (mapping,))


def register(conn, scope, mapping, source, body, previous):
    w, m, v = scope
    conn.execute('''INSERT INTO warehouse_accepted_manifests(manifest_id,workspace_id,warehouse_model_id,
        warehouse_model_version_id,source_mapping_id,source_dataset_id,source_state_id,position,previous_manifest_id,checksum,body)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
        (body['manifest_id'], w, m, v, mapping, source, body['pin']['state_id'], body['position'], previous, body['checksum'], Jsonb(body)))
    for e in body['events']:
        conn.execute('''INSERT INTO warehouse_accepted_events(manifest_id,event_id,checksum,outcome,payload)
            VALUES (%s,%s,%s,%s,%s)''', (body['manifest_id'], e['event_id'], e['checksum'], e['outcome'], Jsonb(e['payload'])))
    conn.execute('INSERT INTO warehouse_outbox(manifest_id) VALUES (%s)', (body['manifest_id'],))


def checkpoint(conn, resource, mapping):
    return one(conn, 'SELECT * FROM warehouse_checkpoints WHERE resource_id=%s AND source_mapping_id=%s FOR UPDATE', (resource, mapping))


def head(conn, resource):
    return one(conn, 'SELECT * FROM warehouse_publication_heads WHERE resource_id=%s FOR UPDATE', (resource,))


def intent(conn, operation):
    return one(conn, 'SELECT * FROM warehouse_execution_intents WHERE operation_id=%s', (operation,))


def lease(conn, resource):
    return one(conn, '''SELECT *,expires_at>clock_timestamp() AS active FROM warehouse_writer_leases
                       WHERE resource_id=%s FOR UPDATE''', (resource,))


def latest_stage(conn, operation):
    return one(conn, '''SELECT j.* FROM warehouse_run_journal j JOIN warehouse_runs r USING(warehouse_run_id)
        WHERE r.operation_id=%s AND j.stage IN ('PLANNED','TARGET_RECEIPT') ORDER BY j.journal_id DESC LIMIT 1''', (operation,))
