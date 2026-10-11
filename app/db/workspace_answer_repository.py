"""Owned immutable answers and schema-scoped question coordination."""
from contextlib import contextmanager
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from app.db.database import get_connection


@contextmanager
def question_lock(workspace):
    with get_connection() as conn:
        conn.autocommit = True
        schema = conn.execute('SELECT current_schema()').fetchone()[0]
        key = f'{schema}:datarise:workspace-questions:{workspace}'
        if not conn.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0))', (key,)).fetchone()[0]:
            raise RuntimeError('A workspace question is already being answered')
        try:
            yield
        finally:
            conn.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))', (key,))


def get(conn, workspace, answer):
    return conn.cursor(row_factory=dict_row).execute('SELECT * FROM workspace_answers WHERE workspace_id=%s AND answer_id=%s', (workspace, answer)).fetchone()


def recent(conn, workspace):
    return conn.cursor(row_factory=dict_row).execute('SELECT * FROM workspace_answers WHERE workspace_id=%s ORDER BY answer_id DESC LIMIT 20', (workspace,)).fetchall()


def matching_question(conn, workspace, question_hash, planner_key):
    return conn.cursor(row_factory=dict_row).execute('''SELECT * FROM workspace_answers
        WHERE workspace_id=%s AND question_hash=%s AND planner_key=%s
        AND payload->>'status' IN ('ANSWERED','NO_DATA','REFRESH_REQUIRED')
        ORDER BY answer_id DESC LIMIT 1''', (workspace, question_hash, planner_key)).fetchone()


def cached(conn, workspace, key):
    return conn.cursor(row_factory=dict_row).execute('''SELECT * FROM workspace_answers
        WHERE workspace_id=%s AND cache_key=%s AND payload->>'status'='ANSWERED'
        ORDER BY answer_id DESC LIMIT 1''', (workspace, key)).fetchone()


def insert(conn, workspace, request, planner_key, cache_key, payload, internal):
    from app.processing.relationship_evidence import digest
    row = conn.cursor(row_factory=dict_row).execute('''INSERT INTO workspace_answers
        (workspace_id,previous_answer_id,question_hash,planner_key,cache_key,algorithm_version,payload,internal)
        VALUES (%s,%s,%s,%s,%s,1,%s,%s) RETURNING *''',
        (workspace, request.previous_answer_id, digest([request.question, request.timezone, request.previous_answer_id]),
         planner_key, cache_key, Jsonb(payload), Jsonb(internal))).fetchone()
    return row
