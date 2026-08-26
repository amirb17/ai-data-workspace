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

def create_uploaded_physical_file(
    file_name: str,
    file_size: int,
    file_hash: str,
    storage_path: str,
):
    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO physical_files (
                    file_name,
                    file_size,
                    file_hash,
                    storage_path,
                    status
                )
                VALUES (%s, %s, %s, %s, 'UPLOADED')
                RETURNING
                    file_id,
                    file_name,
                    file_size,
                    file_hash,
                    storage_path,
                    status;
                """,
                (
                    file_name,
                    file_size,
                    file_hash,
                    storage_path,
                ),
            )

            physical_file = cur.fetchone()
            conn.commit()

            return physical_file

    finally:
        conn.close()

def get_physical_file_by_id(file_id: int):
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
                WHERE file_id = %s;
                """,
                (file_id,),
            )

            return cur.fetchone()

    finally:
        conn.close()


def get_next_attempt_number(file_id: int):
    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COALESCE(MAX(attempt_number), 0) + 1
                FROM processing_attempts
                WHERE file_id = %s;
                """,
                (file_id,),
            )

            return cur.fetchone()[0]

    finally:
        conn.close()


def create_processing_attempt(
    file_id: int,
    attempt_number: int,
    stage: str = "BRONZE",
    status: str = "PROCESSING",
):
    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO processing_attempts (
                    file_id,
                    attempt_number,
                    stage,
                    status,
                    started_at
                )
                VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
                RETURNING
                    attempt_id,
                    file_id,
                    attempt_number,
                    stage,
                    status,
                    error_message,
                    started_at,
                    completed_at;
                """,
                (
                    file_id,
                    attempt_number,
                    stage,
                    status,
                ),
            )

            attempt = cur.fetchone()
            conn.commit()

            return attempt

    finally:
        conn.close()
def get_active_processing_attempt(file_id: int):
    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    attempt_id,
                    file_id,
                    attempt_number,
                    stage,
                    status,
                    error_message,
                    started_at,
                    completed_at
                FROM processing_attempts
                WHERE file_id = %s
                  AND status = 'PROCESSING'
                ORDER BY attempt_number DESC
                LIMIT 1;
                """,
                (file_id,),
            )

            return cur.fetchone()

    finally:
        conn.close()
def update_physical_file_status(
    file_id: int,
    status: str,
):
    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE physical_files
                SET
                    status = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE file_id = %s
                RETURNING file_id, status;
                """,
                (status, file_id),
            )

            result = cur.fetchone()
            conn.commit()

            return result

    finally:
        conn.close()
def complete_processing_attempt(
    attempt_id: int,
    status: str,
    error_message: str | None = None,
):
    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE processing_attempts
                SET
                    status = %s,
                    error_message = %s,
                    completed_at = CURRENT_TIMESTAMP
                WHERE attempt_id = %s
                RETURNING
                    attempt_id,
                    file_id,
                    attempt_number,
                    stage,
                    status,
                    error_message,
                    started_at,
                    completed_at;
                """,
                (
                    status,
                    error_message,
                    attempt_id,
                ),
            )

            result = cur.fetchone()
            conn.commit()

            return result

    finally:
        conn.close()