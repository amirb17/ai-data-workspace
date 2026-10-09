"""Suggestion history and scoped coordinator. No trusted semantic mutations."""
from contextlib import contextmanager
from psycopg.rows import dict_row
from app.db.database import get_connection


def key(conn, workspace, dataset):
    schema = conn.execute('SELECT current_schema()').fetchone()[0]
    return f'{schema}:datarise:understanding:{workspace}:{dataset}'


@contextmanager
def generation_lock(workspace, dataset):
    with get_connection() as conn:
        conn.autocommit = True
        lock = key(conn,workspace,dataset)
        if not conn.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0))',(lock,)).fetchone()[0]:
            raise RuntimeError('Generation already active')
        try:
            yield
        finally:
            conn.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))',(lock,))


def matching(dataset, profile, version, configuration):
    with get_connection() as conn:
        return conn.cursor(row_factory=dict_row).execute('''SELECT * FROM semantic_suggestions WHERE dataset_id=%s
            AND source_profile_id=%s AND semantic_model_version=%s AND configuration_key=%s''',
            (dataset,profile,version,configuration)).fetchone()


def has_history(dataset):
    with get_connection() as conn:
        return bool(conn.execute('SELECT 1 FROM semantic_suggestions WHERE dataset_id=%s LIMIT 1',(dataset,)).fetchone())


def active(workspace,dataset):
    with get_connection() as conn:
        return not conn.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))',
            (key(conn,workspace,dataset),)).fetchone()[0]
