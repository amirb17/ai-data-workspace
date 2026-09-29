import io
from datetime import datetime, timezone

import pandas as pd
from app.processing.gold_catalog_builder import (
    build_gold_artifact_catalog,
    enrich_catalog_data_types,
)
from app.config import get_boto3_session
from app.db.file_repository import get_dataset_profiles
from app.processing.gold_planner import build_gold_plan
from app.processing.gold_mart_builder import build_mart

s3_client = get_boto3_session().client("s3")


SILVER_ONLY_COLUMNS = [
    "_dq_violations",
    "_dq_violation_count",
    "_dq_is_valid",
]


def _write_parquet_to_s3(
    df: pd.DataFrame,
    bucket_name: str,
    object_key: str,
) -> None:
    """
    Serialize a dataframe to Parquet and publish it to S3.
    """

    buffer = io.BytesIO()

    df.to_parquet(
        buffer,
        index=False,
        engine="pyarrow",
    )

    buffer.seek(0)

    s3_client.put_object(
        Bucket=bucket_name,
        Key=object_key,
        Body=buffer.getvalue(),
        ContentType="application/octet-stream",
    )



def process_gold(
    file_id: int,
    dataset_version_id: int,
    rule_version: int,
    bucket_name: str,
    silver_key: str,
    attempt_id: int,
) -> dict:
    """
    Publish validated Silver data as a curated Gold base dataset.
    """

    # Read successful Silver output
    response = s3_client.get_object(
        Bucket=bucket_name,
        Key=silver_key,
    )

    parquet_bytes = response["Body"].read()

    silver_df = pd.read_parquet(
        io.BytesIO(parquet_bytes)
    )

    if silver_df.empty:
        raise ValueError(
            f"Silver dataset for file_id={file_id} is empty"
        )

    gold_df = silver_df.copy()

    # Remove validation-only technical columns
    columns_to_drop = [
        column
        for column in SILVER_ONLY_COLUMNS
        if column in gold_df.columns
    ]

    if columns_to_drop:
        gold_df = gold_df.drop(
            columns=columns_to_drop
        )

    # Add Gold lineage/publication metadata
    gold_df["_gold_published_at"] = datetime.now(
        timezone.utc
    )

    gold_df["_gold_attempt_id"] = attempt_id

    # Attempt-specific output
    gold_key = (
    f"gold/base/"
    f"dataset_version_id={dataset_version_id}/"
    f"file_id={file_id}/"
    f"rule_version={rule_version}/"
    f"data.parquet"
)

    _write_parquet_to_s3(
        df=gold_df,
        bucket_name=bucket_name,
        object_key=gold_key,
    )
    # ---------------------------------------------------------
    # Build analytical Gold plan
    # ---------------------------------------------------------
    profiles = get_dataset_profiles(file_id)

    if not profiles:
        raise ValueError(
            f"No dataset profiles found for file_id={file_id}"
        )

    gold_plan = build_gold_plan(profiles)

    # BASE is always the first Gold artifact.
    artifacts = [
        {
            "artifact_type": "BASE",
            "artifact_name": "base",
            "storage_path": gold_key,
            "row_count": len(gold_df),
        }
    ]

    # ---------------------------------------------------------
    # Build and publish analytical Gold marts
    # ---------------------------------------------------------
    for artifact_plan in gold_plan.artifacts:

        mart_df = build_mart(
            df=gold_df,
            plan=artifact_plan,
        )
        catalog = build_gold_artifact_catalog(
            artifact_plan
        )

        catalog = enrich_catalog_data_types(
            catalog=catalog,
            df=mart_df,
        )

        mart_key = (
            f"gold/marts/"
            f"{artifact_plan.artifact_name}/"
            f"dataset_version_id={dataset_version_id}/"
            f"file_id={file_id}/"
            f"rule_version={rule_version}/"
            f"data.parquet"
        )

        _write_parquet_to_s3(
            df=mart_df,
            bucket_name=bucket_name,
            object_key=mart_key,
        )

        artifacts.append(
    {
        "artifact_type":
            artifact_plan.artifact_type,
        "artifact_name":
            artifact_plan.artifact_name,
        "storage_path": mart_key,
        "row_count": len(mart_df),
        "catalog": catalog.to_dict(),
    }
)

    return {
        "gold_df": gold_df,
        "row_count": len(gold_df),
        "gold_key": gold_key,
        "gold_plan": gold_plan.to_dict(),
        "artifacts": artifacts,
    }
