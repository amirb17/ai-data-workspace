from app.db.database import get_connection


def create_dataset(
    workspace_id: int,
    dataset_name: str,
    description: str | None = None,
    owner: str | None = None,
):
    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO datasets (
                    workspace_id,
                    dataset_name,
                    description,
                    owner
                )
                VALUES (%s, %s, %s, %s)
                RETURNING
                    dataset_id,
                    dataset_name,
                    description,
                    owner,
                    status,
                    created_at,
                    updated_at,
                    workspace_id;
                """,
                (
                    workspace_id,
                    dataset_name,
                    description,
                    owner,
                ),
            )

            dataset = cur.fetchone()
            conn.commit()

            return dataset

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()

def get_dataset_by_id(dataset_id: int):
    query = """
        SELECT
            dataset_id,
            dataset_name,
            description,
            owner,
            status,
            created_at,
            updated_at,
            workspace_id
        FROM datasets
        WHERE dataset_id = %s;
    """

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (dataset_id,),
            )

            return cursor.fetchone()

def get_dataset_by_workspace(
    workspace_id: int,
    dataset_id: int,
):
    query = """
        SELECT
            dataset_id,
            dataset_name,
            description,
            owner,
            status,
            created_at,
            updated_at,
            workspace_id
        FROM datasets
        WHERE dataset_id = %s
          AND workspace_id = %s;
    """

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (
                    dataset_id,
                    workspace_id,
                ),
            )

            return cursor.fetchone()

def get_datasets_by_workspace(
    workspace_id: int,
):
    query = """
        SELECT
            dataset_id,
            dataset_name,
            description,
            owner,
            status,
            created_at,
            updated_at,
            workspace_id
        FROM datasets
        WHERE workspace_id = %s
        ORDER BY created_at;
    """

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (workspace_id,),
            )

            return cursor.fetchall()

def get_dataset_by_workspace_and_name(
    workspace_id: int,
    dataset_name: str,
):
    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    dataset_id,
                    dataset_name,
                    description,
                    owner,
                    status,
                    created_at,
                    updated_at,
                    workspace_id
                FROM datasets
                WHERE workspace_id = %s
                  AND dataset_name = %s;
                """,
                (
                    workspace_id,
                    dataset_name,
                ),
            )

            return cur.fetchone()

    finally:
        conn.close()

def create_next_dataset_version(
    dataset_id: int,
    schema_hash: str,
):
    lock_query = """
        SELECT
            dataset_id
        FROM datasets
        WHERE dataset_id = %s
        FOR UPDATE;
    """

    next_version_query = """
        SELECT
            COALESCE(MAX(version_number), 0) + 1
        FROM dataset_versions
        WHERE dataset_id = %s;
    """

    insert_query = """
        INSERT INTO dataset_versions (
            dataset_id,
            version_number,
            schema_hash
        )
        VALUES (%s, %s, %s)
        RETURNING
            dataset_version_id,
            dataset_id,
            version_number,
            schema_hash,
            status,
            created_at;
    """

    with get_connection() as conn:
        with conn.cursor() as cursor:

            # Lock the dataset row so concurrent workers
            # cannot create the same next version.
            cursor.execute(
                lock_query,
                (dataset_id,),
            )

            dataset = cursor.fetchone()

            if dataset is None:
                raise ValueError(
                    f"Dataset {dataset_id} not found"
                )

            cursor.execute(
                next_version_query,
                (dataset_id,),
            )

            version_number = cursor.fetchone()[0]

            cursor.execute(
                insert_query,
                (
                    dataset_id,
                    version_number,
                    schema_hash,
                ),
            )

            result = cursor.fetchone()

            conn.commit()

            return result
def get_dataset_version_by_schema(
    dataset_id: int,
    schema_hash: str,
):
    query = """
        SELECT
            dataset_version_id,
            dataset_id,
            version_number,
            schema_hash,
            status,
            created_at
        FROM dataset_versions
        WHERE dataset_id = %s
          AND schema_hash = %s;
    """

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (
                    dataset_id,
                    schema_hash,
                ),
            )

            return cursor.fetchone()


def assign_physical_file_to_dataset_version(
    file_id: int,
    dataset_version_id: int,
):
    insert_query = """
        INSERT INTO dataset_version_files (
            dataset_version_id,
            file_id
        )
        VALUES (%s, %s)
        ON CONFLICT (dataset_version_id, file_id)
        DO NOTHING
        RETURNING
            dataset_version_file_id,
            dataset_version_id,
            file_id,
            status,
            created_at;
    """

    select_query = """
    SELECT
        dataset_version_file_id,
        dataset_version_id,
        file_id,
        status,
        created_at
    FROM dataset_version_files
    WHERE dataset_version_id = %s
      AND file_id = %s;
"""

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                insert_query,
                (
                    dataset_version_id,
                    file_id,
                ),
            )

            result = cursor.fetchone()

            if result is None:
                cursor.execute(
                    select_query,
                    (
                        dataset_version_id,
                        file_id,
                    ),
                )

                result = cursor.fetchone()

            conn.commit()

            return result

def get_dataset_version_file(
    dataset_version_id: int,
    file_id: int,
):
    query = """
        SELECT
            dataset_version_file_id,
            dataset_version_id,
            file_id,
            status,
            created_at
        FROM dataset_version_files
        WHERE dataset_version_id = %s
          AND file_id = %s;
    """

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (
                    dataset_version_id,
                    file_id,
                ),
            )

            return cursor.fetchone()


def update_dataset_version_file_status(
    dataset_version_file_id: int,
    status: str,
):
    query = """
        UPDATE dataset_version_files
        SET status = %s
        WHERE dataset_version_file_id = %s
        RETURNING
            dataset_version_file_id,
            dataset_version_id,
            file_id,
            status,
            created_at;
    """

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (
                    status,
                    dataset_version_file_id,
                ),
            )

            result = cursor.fetchone()

            if result is None:
                raise ValueError(
                    "Dataset-version-file "
                    f"{dataset_version_file_id} not found"
                )

            conn.commit()

            return result


def initialize_dataset_version_file_rules(dataset_version_file_id: int, approved_version: int | None = None):
    # Atomic conditional update: a concurrent/repeated Bronze request must not
    # overwrite finalized rules or downstream progress.
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                UPDATE dataset_version_files
                SET status = CASE WHEN status IN ('UPLOADED','AWAITING_RULES')
                                  THEN CASE WHEN %s::integer IS NULL THEN 'AWAITING_RULES' ELSE 'READY_FOR_SILVER' END ELSE status END,
                    applied_rule_version = CASE WHEN status IN ('UPLOADED','AWAITING_RULES') AND %s::integer IS NOT NULL
                                                THEN %s ELSE applied_rule_version END,
                    rules_reused = CASE WHEN status IN ('UPLOADED','AWAITING_RULES') AND %s::integer IS NOT NULL
                                        THEN TRUE ELSE rules_reused END
                WHERE dataset_version_file_id = %s
                RETURNING dataset_version_file_id, dataset_version_id, file_id, status, created_at
            """, (approved_version, approved_version, approved_version, approved_version, dataset_version_file_id))
            return cursor.fetchone()


def get_upload_processing_association(dataset_id: int, file_id: int):
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT dvf.dataset_version_file_id, dvf.dataset_version_id,
                       dvf.file_id, dvf.status, dv.version_number, dv.rule_version,
                       dvf.applied_rule_version, dvf.rules_reused, dv.approved_rule_version
                FROM dataset_version_files dvf
                JOIN dataset_versions dv ON dv.dataset_version_id = dvf.dataset_version_id
                JOIN physical_files pf ON pf.file_id = dvf.file_id
                WHERE dv.dataset_id = %s AND dvf.file_id = %s AND dv.schema_hash = pf.schema_hash
            """, (dataset_id, file_id))
            return cursor.fetchone()


def get_rule_approval_context(association_id: int, lock: bool = False):
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT f.dataset_version_id, dv.rule_version, dv.approved_rule_version,
                       f.applied_rule_version, f.rules_reused, dv.schema_hash = pf.schema_hash AS schema_matches
                FROM dataset_version_files f
                JOIN dataset_versions dv ON dv.dataset_version_id = f.dataset_version_id
                JOIN physical_files pf ON pf.file_id = f.file_id
                WHERE f.dataset_version_file_id = %s
            """ + (" FOR UPDATE OF dv, f" if lock else ""), (association_id,))
            return cursor.fetchone()


def approve_association_rules(association_id: int):
    # Called inside the service's approval transaction/row lock.
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                UPDATE dataset_versions dv SET approved_rule_version = rule_version
                FROM dataset_version_files f WHERE f.dataset_version_file_id = %s
                  AND dv.dataset_version_id = f.dataset_version_id
                RETURNING dv.rule_version
            """, (association_id,))
            version = cursor.fetchone()[0]
            cursor.execute("""
                INSERT INTO approved_rule_policies(dataset_version_id, rule_version, rules)
                SELECT dv.dataset_version_id, %s,
                    jsonb_agg(jsonb_build_object('column_name',br.column_name,'rule_type',br.rule_type,
                                                'rule_config',br.rule_config) ORDER BY br.rule_id)
                FROM dataset_versions dv JOIN business_rules br ON br.dataset_version_id=dv.dataset_version_id
                JOIN dataset_version_files f ON f.dataset_version_id=dv.dataset_version_id
                WHERE f.dataset_version_file_id=%s AND br.is_active
                GROUP BY dv.dataset_version_id
                ON CONFLICT (dataset_version_id, rule_version) DO NOTHING
            """, (version, association_id))
            cursor.execute("""
                UPDATE dataset_version_files SET status = 'READY_FOR_SILVER',
                    applied_rule_version = %s, rules_reused = FALSE
                WHERE dataset_version_file_id = %s
                RETURNING dataset_version_file_id, dataset_version_id, file_id, status, created_at
            """, (version, association_id))
            return cursor.fetchone()
def get_files_for_dataset_version(
    dataset_version_id: int,
):
    query = """
        SELECT
            dvf.dataset_version_file_id,
            dvf.dataset_version_id,
            dvf.file_id,
            pf.file_name,
            pf.file_size,
            pf.file_hash,
            pf.storage_path,
            pf.status,
            dvf.created_at
        FROM dataset_version_files dvf
        JOIN physical_files pf
            ON pf.file_id = dvf.file_id
        WHERE dvf.dataset_version_id = %s
        ORDER BY dvf.created_at;
    """

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (dataset_version_id,),
            )

            return cursor.fetchall()
def get_dataset_version_file_by_id(
    dataset_version_file_id: int,
):
    query = """
        SELECT
            dvf.dataset_version_file_id,
            dvf.dataset_version_id,
            dvf.file_id,
            dvf.status,
            dvf.created_at,
            dv.dataset_id,
            d.workspace_id
        FROM dataset_version_files dvf
        JOIN dataset_versions dv
            ON dv.dataset_version_id = dvf.dataset_version_id
        JOIN datasets d
            ON d.dataset_id = dv.dataset_id
        WHERE dvf.dataset_version_file_id = %s;
    """

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (dataset_version_file_id,),
            )

            return cursor.fetchone()

def get_dataset_version_rule_version(
    dataset_version_id: int,
) -> int:
    query = """
        SELECT rule_version
        FROM dataset_versions
        WHERE dataset_version_id = %s;
    """

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (dataset_version_id,),
            )

            row = cursor.fetchone()

            if row is None:
                raise ValueError(
                    f"Dataset version "
                    f"{dataset_version_id} not found"
                )

            return row[0]


def increment_dataset_version_rule_version(
    dataset_version_id: int,
) -> int:
    query = """
        UPDATE dataset_versions
        SET rule_version = rule_version + 1
        WHERE dataset_version_id = %s
        RETURNING rule_version;
    """

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (dataset_version_id,),
            )

            row = cursor.fetchone()

            if row is None:
                raise ValueError(
                    f"Dataset version "
                    f"{dataset_version_id} not found"
                )

            conn.commit()

            return row[0]
