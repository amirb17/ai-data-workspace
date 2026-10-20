"""Safe control-plane snapshots; source values and credentials never leave SQL."""
from psycopg import sql
from psycopg.rows import dict_row


def business_tables(conn):
    return [r[0] for r in conn.execute('''SELECT table_name FROM information_schema.tables
        WHERE table_schema=current_schema() AND table_type='BASE TABLE' ORDER BY table_name''')
        if r[0] != 'users' and not r[0].startswith('auth_') and r[0] != 'ownership_adoption_events']


def snapshot(conn, source, target_id):
    c = conn.cursor(row_factory=dict_row)
    target = c.execute('SELECT user_id,owner_key,email_verified,account_status FROM users WHERE user_id=%s', (target_id,)).fetchone()
    if not target:
        raise ValueError('Target account not found')
    workspaces = c.execute('SELECT workspace_id,workspace_name,owner,status FROM workspaces ORDER BY workspace_id').fetchall()
    datasets = c.execute('''SELECT dataset_id,dataset_name,workspace_id,owner,status,current_state_id,current_profile_id
        FROM datasets ORDER BY dataset_id''').fetchall()
    invariants = {}
    for table in business_tables(conn):
        row = sql.SQL("to_jsonb(r)-'owner'") if table in ('workspaces', 'datasets') else sql.SQL('to_jsonb(r)')
        invariants[table] = c.execute(sql.SQL('''SELECT count(*) AS count,
            encode(sha256(convert_to(COALESCE(string_agg(h,'' ORDER BY h),''),'UTF8')),'hex') AS sha256
            FROM (SELECT encode(sha256(convert_to(({})::text,'UTF8')),'hex') h FROM {} r) x''').format(
                row, sql.Identifier(table))).fetchone()
    return {'target': target, 'workspaces': workspaces, 'datasets': datasets, 'invariants': invariants}


def events(conn, adoption_id):
    return conn.cursor(row_factory=dict_row).execute(
        'SELECT * FROM ownership_adoption_events WHERE adoption_id=%s ORDER BY executed_at', (adoption_id,)).fetchall()


def active_work(conn):
    # Adoption is a maintenance operation, never races background publishers.
    checks = [('semantic_profiles', "status='PROFILING'"), ('semantic_suggestions', "status='GENERATING'"),
              ('workspace_semantic_suggestions', "status='GENERATING'"),
              ('relationship_discovery_runs', "status='DISCOVERING'"),
              ('metric_discovery_runs', "status='GENERATING'"),
              ('processing_attempts', "status='PROCESSING'"),
              ('delivery_applications', "status='RUNNING'"),
              ('workspace_metric_results', "status='COMPUTING'")]
    return any(conn.execute(sql.SQL('SELECT EXISTS(SELECT 1 FROM {} WHERE {})').format(
        sql.Identifier(table), sql.SQL(predicate))).fetchone()[0] for table, predicate in checks)
