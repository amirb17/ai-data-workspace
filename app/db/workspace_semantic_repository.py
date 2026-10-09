"""Database workspace coordinator and immutable suggestion history."""
from contextlib import contextmanager
from psycopg.rows import dict_row
from app.db.database import get_connection

def key(conn,workspace):
    schema=conn.execute('SELECT current_schema()').fetchone()[0]
    return f'{schema}:datarise:workspace-understanding:{workspace}'

@contextmanager
def generation_lock(workspace):
    with get_connection() as conn:
        conn.autocommit=True
        lock=key(conn,workspace)
        if not conn.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0))',(lock,)).fetchone()[0]:
            raise RuntimeError('Workspace analysis already active')
        try:yield
        finally:conn.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))',(lock,))

def active(workspace):
    with get_connection() as conn:
        return not conn.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))',(key(conn,workspace),)).fetchone()[0]

def matching(workspace,signature,version,configuration):
    with get_connection() as conn:
        return conn.cursor(row_factory=dict_row).execute('''SELECT * FROM workspace_semantic_suggestions
            WHERE workspace_id=%s AND source_signature=%s AND algorithm_version=%s AND configuration_key=%s''',
            (workspace,signature,version,configuration)).fetchone()

def has_history(workspace):
    with get_connection() as conn:
        return bool(conn.execute('SELECT 1 FROM workspace_semantic_suggestions WHERE workspace_id=%s LIMIT 1',(workspace,)).fetchone())
