from app.db.database import get_connection


def get_gold_artifacts_for_dataset_version(
    dataset_version_id: int,
):
    """
    Return published Gold artifacts belonging to a dataset version.

    Includes semantic metadata when available.
    """

    query = """
        SELECT
            ga.gold_artifact_id,
            ga.gold_run_id,
            ga.artifact_type,
            ga.artifact_name,
            ga.storage_path,
            ga.row_count,
            gam.grain,
            gam.time_grain,
            ga.created_at
        FROM gold_artifacts ga

        JOIN gold_runs gr
            ON gr.gold_run_id = ga.gold_run_id

        JOIN dataset_version_files dvf
            ON dvf.dataset_version_file_id =
               gr.dataset_version_file_id

        LEFT JOIN gold_artifact_models gam
            ON gam.gold_artifact_id =
               ga.gold_artifact_id

        WHERE dvf.dataset_version_id = %s

        ORDER BY
            ga.gold_run_id DESC,
            ga.gold_artifact_id;
    """

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (dataset_version_id,),
            )

            return cursor.fetchall()

def get_gold_artifact_semantic_columns(
    gold_artifact_id: int,
):
    """
    Return semantic column metadata for one Gold artifact.
    """

    query = """
        SELECT
            gold_artifact_column_id,
            gold_artifact_id,
            column_name,
            column_role,
            source_column,
            aggregation_type,
            ordinal_position,
            data_type
        FROM gold_artifact_columns
        WHERE gold_artifact_id = %s
        ORDER BY ordinal_position;
    """

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (gold_artifact_id,),
            )

            return cursor.fetchall()

def get_gold_artifact_semantic_columns_for_artifacts(
    gold_artifact_ids: list[int],
):
    """
    Return semantic column metadata for multiple Gold artifacts
    in one database query.
    """

    if not gold_artifact_ids:
        return []

    placeholders = ", ".join(
        ["%s"] * len(gold_artifact_ids)
    )

    query = f"""
        SELECT
            gold_artifact_column_id,
            gold_artifact_id,
            column_name,
            column_role,
            source_column,
            aggregation_type,
            ordinal_position,
            data_type
        FROM gold_artifact_columns
        WHERE gold_artifact_id IN ({placeholders})
        ORDER BY
            gold_artifact_id,
            ordinal_position;
    """

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                tuple(gold_artifact_ids),
            )

            return cursor.fetchall()