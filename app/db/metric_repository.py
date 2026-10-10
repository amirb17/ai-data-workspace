"""Workspace coordination; source reads join the caller's transaction."""
from contextlib import contextmanager
from psycopg.rows import dict_row
from app.db.database import get_connection

def key(conn,workspace):return f'{conn.execute("SELECT current_schema()").fetchone()[0]}:datarise:metrics:{workspace}'

@contextmanager
def discovery_lock(workspace):
    with get_connection() as conn:
        conn.autocommit=True;k=key(conn,workspace)
        if not conn.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0))',(k,)).fetchone()[0]:raise RuntimeError('Metric discovery already active')
        try:yield
        finally:conn.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))',(k,))

def active(workspace):
    with get_connection() as conn:return not conn.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))',(key(conn,workspace),)).fetchone()[0]

def matching(conn,workspace,signature,config):
    return conn.cursor(row_factory=dict_row).execute('SELECT * FROM metric_discovery_runs WHERE workspace_id=%s AND source_signature=%s AND configuration_key=%s',(workspace,signature,config)).fetchone()

def latest_reviews(conn,workspace):
    return conn.cursor(row_factory=dict_row).execute('SELECT DISTINCT ON(candidate_id) * FROM metric_reviews WHERE workspace_id=%s ORDER BY candidate_id,review_version DESC',(workspace,)).fetchall()
