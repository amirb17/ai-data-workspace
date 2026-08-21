from app.db.database import get_connection
from app.db.database import get_connection


def find_physical_file_by_hash(file_hash: str):
    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    file_id,
                    file_name,
                    file_size,
                    file_hash,
                    storage_path,
                    status
                FROM physical_files
                WHERE file_hash = %s
                """,
                (file_hash,)
            )

            return cur.fetchone()

    finally:
        conn.close()
        
def create_physical_file(
    file_name: str,
    file_size: int,
    file_hash: str,
):
    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO physical_files
                    (file_name, file_size, file_hash)
                VALUES
                    (%s, %s, %s)
                RETURNING file_id, file_name, file_size, file_hash, status;
                """,
                (file_name, file_size, file_hash),
            )

            file = cur.fetchone()
            conn.commit()

            return file

    finally:
        conn.close()

def create_upload_request(
    user_id: int,
    file_id: int,
):
    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO upload_requests
                    (user_id, file_id, status)
                VALUES
                    (%s, %s, 'UPLOADED')
                RETURNING upload_id, user_id, file_id, status, created_at;
                """,
                (user_id, file_id),
            )

            upload_request = cur.fetchone()
            conn.commit()

            return upload_request

    finally:
        conn.close()

def update_physical_file_storage(
    file_id: int,
    storage_path: str,
    status: str,
):
    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE physical_files
                SET
                    storage_path = %s,
                    status = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE file_id = %s
                RETURNING
                    file_id,
                    file_name,
                    file_size,
                    file_hash,
                    storage_path,
                    status;
                """,
                (storage_path, status, file_id),
            )

            updated_file = cur.fetchone()
            conn.commit()

            return updated_file

    finally:
        conn.close()