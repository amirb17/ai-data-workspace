"""Workspace coordinator and versioned evidence/review persistence."""
from contextlib import contextmanager
from psycopg.rows import dict_row
from app.db.database import get_connection

def lock_key(conn, workspace):
    return f'{conn.execute("SELECT current_schema()").fetchone()[0]}:datarise:relationships:{workspace}'

@contextmanager
def discovery_lock(workspace):
    with get_connection() as conn:
        conn.autocommit = True
        key = lock_key(conn,workspace)
        if not conn.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0))',(key,)).fetchone()[0]:
            raise RuntimeError('Relationship discovery already active')
        try: yield
        finally: conn.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))',(key,))

def active(workspace):
    with get_connection() as conn:
        return not conn.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))',(lock_key(conn,workspace),)).fetchone()[0]

def matching(conn, workspace, signature, algorithm):
    return conn.cursor(row_factory=dict_row).execute('''SELECT * FROM relationship_discovery_runs
        WHERE workspace_id=%s AND source_signature=%s AND algorithm_version=%s''',(workspace,signature,algorithm)).fetchone()

def candidates(conn, run):
    return conn.cursor(row_factory=dict_row).execute('SELECT * FROM relationship_candidates WHERE run_id=%s ORDER BY candidate_id',(run,)).fetchall()

def reviews(conn, workspace):
    return conn.cursor(row_factory=dict_row).execute('''SELECT DISTINCT ON (r.candidate_key) r.*, c.evidence
        FROM relationship_reviews r JOIN relationship_candidates c ON c.candidate_id=r.candidate_id AND c.workspace_id=r.workspace_id
        WHERE r.workspace_id=%s ORDER BY r.candidate_key,r.relationship_version DESC''',(workspace,)).fetchall()
