"""Workspace-scoped execution coordination and immutable result attempts."""
from contextlib import contextmanager
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from app.db.database import get_connection


def lock_key(conn, workspace):
    schema = conn.execute('SELECT current_schema()').fetchone()[0]
    return f'{schema}:datarise:workspace-analytics:{workspace}'


@contextmanager
def execution_lock(workspace):
    with get_connection() as conn:
        conn.autocommit = True
        key = lock_key(conn, workspace)
        if not conn.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0))', (key,)).fetchone()[0]:
            raise RuntimeError('Workspace analytics refresh already active')
        try: yield
        finally: conn.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))', (key,))


def active(conn, workspace):
    return not conn.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))',
                            (lock_key(conn, workspace),)).fetchone()[0]


def candidates(conn, workspace):
    return conn.cursor(row_factory=dict_row).execute('''SELECT c.* FROM metric_candidates c
        JOIN metric_discovery_runs r USING(workspace_id,run_id)
        WHERE c.workspace_id=%s AND r.status='READY' AND r.run_id=(
          SELECT run_id FROM metric_discovery_runs WHERE workspace_id=%s AND status='READY'
          ORDER BY run_version DESC LIMIT 1) ORDER BY c.candidate_id''', (workspace, workspace)).fetchall()


def results(conn, workspace, resolved):
    pins = [{'candidate_id': i['row']['candidate_id'], 'signature': i['plan'].signature}
            for i in resolved if i['plan']]
    # Read bounded latest/current attempts rather than materializing every historical output.
    wanted = [i['row']['candidate_id'] for i in resolved]
    return conn.cursor(row_factory=dict_row).execute('''WITH wanted AS (
        SELECT value::BIGINT AS candidate_id FROM jsonb_array_elements_text(%s)
      ), pins AS (
        SELECT * FROM jsonb_to_recordset(%s) AS p(candidate_id BIGINT,signature TEXT)
      ), latest AS (
        SELECT DISTINCT ON(r.candidate_id) r.* FROM workspace_metric_results r JOIN wanted w USING(candidate_id)
        WHERE r.workspace_id=%s ORDER BY r.candidate_id,r.result_id DESC
      ), matching AS (
        SELECT DISTINCT ON(r.candidate_id) r.* FROM workspace_metric_results r JOIN pins p
        ON p.candidate_id=r.candidate_id AND p.signature=r.dependency_signature WHERE r.workspace_id=%s
        ORDER BY r.candidate_id,r.result_id DESC
      ), successes AS (
        SELECT r.* FROM workspace_metric_results r JOIN pins p
        ON p.candidate_id=r.candidate_id AND p.signature=r.dependency_signature
        WHERE r.workspace_id=%s AND r.status='FRESH'
      ) SELECT * FROM latest UNION SELECT * FROM matching UNION SELECT * FROM successes ORDER BY result_id DESC''',
      (Jsonb(wanted), Jsonb(pins), workspace, workspace, workspace)).fetchall()
