"""Versioned evidence metadata and atomic current-profile publication."""
from contextlib import contextmanager
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from app.db.database import get_connection


def lock_key(conn,workspace,dataset):
    return f"{conn.execute('SELECT current_schema()').fetchone()[0]}:datarise:profile:{workspace}:{dataset}"


@contextmanager
def profile_lock(workspace,dataset):
    with get_connection() as conn:
        conn.autocommit=True
        key=lock_key(conn,workspace,dataset)
        if not conn.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0))',(key,)).fetchone()[0]:
            raise RuntimeError('Dataset profiling already active')
        try: yield
        finally: conn.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))',(key,))


def metadata(dataset,algorithm):
    with get_connection() as conn:
        cursor=conn.cursor(row_factory=dict_row)
        data=cursor.execute('''SELECT d.dataset_id,d.workspace_id,d.dataset_name,d.current_state_id,d.current_profile_id,d.profile_status,
            s.state_version,s.dataset_version_id,s.published_at,v.version_number AS schema_version
            FROM datasets d LEFT JOIN dataset_state_versions s ON s.state_id=d.current_state_id
            LEFT JOIN dataset_versions v ON v.dataset_version_id=s.dataset_version_id WHERE d.dataset_id=%s''',(dataset,)).fetchone()
        run=cursor.execute('SELECT * FROM semantic_profiles WHERE profile_id=%s',(data['current_profile_id'],)).fetchone() if data['current_profile_id'] else None
        latest=cursor.execute('SELECT status,failure_code FROM semantic_profiles WHERE dataset_id=%s AND source_state_id=%s AND algorithm_version=%s',
            (dataset,data['current_state_id'],algorithm)).fetchone()
        if data['profile_status']=='PROFILING':
            available=conn.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))',(lock_key(conn,data['workspace_id'],dataset),)).fetchone()[0]
            if available:
                data['profile_status']='FAILED'; latest={'failure_code':'INTERRUPTED'}
        ready=bool(run and run['status']=='READY' and run['algorithm_version']==algorithm and run['source_state_id']==data['current_state_id'] and data['profile_status']=='READY')
        if not ready and data['profile_status']=='READY': data['profile_status']='STALE'
        if data['current_state_id'] and data['profile_status']=='NOT_PROFILED': data['profile_status']='STALE'
        return data,run,latest,ready


def publish(conn,dataset,run,summary,columns):
    cursor=conn.cursor(row_factory=dict_row)
    head=cursor.execute('SELECT current_state_id FROM datasets WHERE dataset_id=%s FOR UPDATE',(dataset,)).fetchone()
    cursor.execute('DELETE FROM semantic_column_evidence WHERE profile_id=%s',(run['profile_id'],))
    for position,evidence in enumerate(columns,1):
        cursor.execute('INSERT INTO semantic_column_evidence(profile_id,ordinal_position,column_name,evidence) VALUES (%s,%s,%s,%s)',
            (run['profile_id'],position,evidence['original_name'],Jsonb(evidence)))
    cursor.execute("UPDATE semantic_profiles SET status='READY',summary=%s,completed_at=NOW(),failure_code=NULL WHERE profile_id=%s",(Jsonb(summary),run['profile_id']))
    if head['current_state_id']==run['source_state_id']:
        cursor.execute("UPDATE datasets SET current_profile_id=%s,profile_status='READY' WHERE dataset_id=%s",(run['profile_id'],dataset))


def columns(profile):
    with get_connection() as conn:
        return [r[0] for r in conn.execute('SELECT evidence FROM semantic_column_evidence WHERE profile_id=%s ORDER BY ordinal_position',(profile,)).fetchall()]
