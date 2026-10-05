from contextlib import contextmanager
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from app.db.database import get_connection


def create_upload_session(user_id, workspace_id, dataset_id, object_key, file_name, file_size, content_type):
    with get_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                INSERT INTO upload_requests (user_id, workspace_id, dataset_id, staging_object_key,
                    source_file_name, expected_file_size, content_type, status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,'INITIATED') RETURNING *;
            """, (user_id, workspace_id, dataset_id, object_key, file_name, file_size, content_type))
            return cur.fetchone()


@contextmanager
def locked_upload_session(upload_id):
    # Serialize completions across API workers; hold the lock through verification/result persistence.
    with get_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM upload_requests WHERE upload_id = %s FOR UPDATE", (upload_id,))
            yield conn, cur.fetchone()


def complete_session(conn, upload_id, file_id, result):
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE upload_requests SET file_id=%s, status='UPLOADED', completed_at=CURRENT_TIMESTAMP,
                completion_result=%s, last_error=NULL WHERE upload_id=%s;
        """, (file_id, Jsonb(result), upload_id))


def fail_session(conn, upload_id):
    with conn.cursor() as cur:
        cur.execute("""UPDATE upload_requests SET status='FAILED', last_error='Upload verification or finalization failed'
                       WHERE upload_id=%s AND completed_at IS NULL""", (upload_id,))
