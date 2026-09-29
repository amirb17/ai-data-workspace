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