import json
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
def save_dataset_profiles(
    file_id: int,
    profiles: list[dict],
):
    conn = get_connection()

    try:
        with conn.cursor() as cur:
            for profile in profiles:
                cur.execute(
                    """
                    INSERT INTO dataset_profiles (
                        file_id,
                        column_name,
                        inferred_type,
                        null_count,
                        distinct_count,
                        duplicate_count,
                        min_value,
                        max_value,
                        negative_count
                    )
                    VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s
                    )
                    ON CONFLICT (file_id, column_name)
                    DO UPDATE SET
                        inferred_type = EXCLUDED.inferred_type,
                        null_count = EXCLUDED.null_count,
                        distinct_count = EXCLUDED.distinct_count,
                        duplicate_count = EXCLUDED.duplicate_count,
                        min_value = EXCLUDED.min_value,
                        max_value = EXCLUDED.max_value,
                        negative_count = EXCLUDED.negative_count,
                        created_at = CURRENT_TIMESTAMP;
                    """,
                    (
                        file_id,
                        profile["column_name"],
                        profile["inferred_type"],
                        profile["null_count"],
                        profile["distinct_count"],
                        profile["duplicate_count"],
                        profile["min_value"],
                        profile["max_value"],
                        profile["negative_count"],
                    ),
                )

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()

def save_dataset_profile_summary(
    file_id: int,
    total_rows: int,
    total_columns: int,
):
    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO dataset_profile_summaries (
                    file_id,
                    total_rows,
                    total_columns
                )
                VALUES (%s, %s, %s)

                ON CONFLICT (file_id)
                DO UPDATE SET
                    total_rows = EXCLUDED.total_rows,
                    total_columns = EXCLUDED.total_columns,
                    updated_at = CURRENT_TIMESTAMP

                RETURNING
                    profile_summary_id,
                    file_id,
                    total_rows,
                    total_columns,
                    created_at,
                    updated_at;
                """,
                (
                    file_id,
                    total_rows,
                    total_columns,
                ),
            )

            result = cur.fetchone()

        conn.commit()
        return result

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()

def get_dataset_profile_summary(file_id: int):
    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    profile_summary_id,
                    file_id,
                    total_rows,
                    total_columns,
                    created_at,
                    updated_at
                FROM dataset_profile_summaries
                WHERE file_id = %s;
                """,
                (file_id,),
            )

            return cur.fetchone()

    finally:
        conn.close()

def get_dataset_profiles(file_id: int):
    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    profile_id,
                    file_id,
                    column_name,
                    inferred_type,
                    null_count,
                    distinct_count,
                    duplicate_count,
                    min_value,
                    max_value,
                    negative_count
                FROM dataset_profiles
                WHERE file_id = %s
                ORDER BY profile_id;
                """,
                (file_id,),
            )

            return cur.fetchall()

    finally:
        conn.close()

def save_business_rule(
    file_id: int,
    column_name: str,
    rule_type: str,
    rule_config: dict,
):
    query = """
    INSERT INTO business_rules (
        file_id,
        column_name,
        rule_type,
        rule_config,
        is_active
    )
    VALUES (%s, %s, %s, %s::jsonb, TRUE)

    ON CONFLICT (file_id, column_name, rule_type)
    DO UPDATE SET
        rule_config = EXCLUDED.rule_config,
        is_active = TRUE,
        updated_at = CURRENT_TIMESTAMP

    RETURNING
        rule_id,
        file_id,
        column_name,
        rule_type,
        rule_config,
        is_active,
        created_at,
        updated_at;
    """

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (
                    file_id,
                    column_name,
                    rule_type,
                    json.dumps(rule_config),
                ),
            )

            result = cursor.fetchone()
            conn.commit()

            return result
def save_business_rule_answer(
    file_id: int,
    column_name: str,
    rule_type: str,
    answer: str,
):
    query = """
        INSERT INTO business_rule_answers (
            file_id,
            column_name,
            rule_type,
            answer
        )
        VALUES (%s, %s, %s, %s)

        ON CONFLICT (file_id, column_name, rule_type)
        DO UPDATE SET
            answer = EXCLUDED.answer,
            updated_at = CURRENT_TIMESTAMP

        RETURNING
            answer_id,
            file_id,
            column_name,
            rule_type,
            answer,
            created_at,
            updated_at;
    """

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (
                    file_id,
                    column_name,
                    rule_type,
                    answer.upper(),
                ),
            )

            result = cursor.fetchone()
            conn.commit()

            return result

def get_business_rule_answers(file_id: int):
    query = """
        SELECT
            answer_id,
            file_id,
            column_name,
            rule_type,
            answer,
            created_at,
            updated_at
        FROM business_rule_answers
        WHERE file_id = %s
        ORDER BY answer_id;
    """

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, (file_id,))
            return cursor.fetchall()

def get_active_business_rules(file_id: int):
    query = """
        SELECT
            rule_id,
            file_id,
            column_name,
            rule_type,
            rule_config,
            is_active
        FROM business_rules
        WHERE file_id = %s
          AND is_active = TRUE
        ORDER BY rule_id;
    """

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, (file_id,))
            return cursor.fetchall()

def deactivate_business_rule(
    file_id: int,
    column_name: str,
    rule_type: str,
):
    query = """
        UPDATE business_rules
        SET
            is_active = FALSE,
            updated_at = CURRENT_TIMESTAMP
        WHERE file_id = %s
          AND column_name = %s
          AND rule_type = %s
          AND is_active = TRUE
        RETURNING rule_id;
    """

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (
                    file_id,
                    column_name,
                    rule_type,
                ),
            )

            result = cursor.fetchone()
            conn.commit()

            return result
def save_data_quality_run(
    file_id: int,
    attempt_id: int,
    total_rows: int,
    valid_rows: int,
    rejected_rows: int,
    silver_path: str,
    quarantine_path: str | None,
):
    query = """
        INSERT INTO data_quality_runs (
            file_id,
            attempt_id,
            total_rows,
            valid_rows,
            rejected_rows,
            silver_path,
            quarantine_path
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING
            dq_run_id,
            file_id,
            attempt_id,
            total_rows,
            valid_rows,
            rejected_rows,
            silver_path,
            quarantine_path,
            created_at;
    """

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (
                    file_id,
                    attempt_id,
                    total_rows,
                    valid_rows,
                    rejected_rows,
                    silver_path,
                    quarantine_path,
                ),
            )

            result = cursor.fetchone()
            conn.commit()

            return result
def save_data_quality_issue(
    dq_run_id: int,
    column_name: str,
    rule_type: str,
    violation_count: int,
):
    query = """
        INSERT INTO data_quality_issues (
            dq_run_id,
            column_name,
            rule_type,
            violation_count
        )
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (
            dq_run_id,
            column_name,
            rule_type
        )
        DO UPDATE SET
            violation_count = EXCLUDED.violation_count
        RETURNING *;
    """

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (
                    dq_run_id,
                    column_name,
                    rule_type,
                    violation_count,
                ),
            )

            result = cursor.fetchone()

        conn.commit()

    return result