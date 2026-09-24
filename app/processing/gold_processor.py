import io
from datetime import datetime, timezone

import boto3
import pandas as pd


session = boto3.Session(
    profile_name="ai-data-workspace"
)

s3_client = session.client(
    "s3",
    region_name="ap-south-1",
)


SILVER_ONLY_COLUMNS = [
    "_dq_violations",
    "_dq_violation_count",
    "_dq_is_valid",
]


def process_gold(
    file_id: int,
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
        f"gold/base/file_id={file_id}/"
        f"attempt_id={attempt_id}/data.parquet"
    )

    buffer = io.BytesIO()

    gold_df.to_parquet(
        buffer,
        index=False,
        engine="pyarrow",
    )

    buffer.seek(0)

    s3_client.put_object(
        Bucket=bucket_name,
        Key=gold_key,
        Body=buffer.getvalue(),
        ContentType="application/octet-stream",
    )

    return {
        "gold_df": gold_df,
        "row_count": len(gold_df),
        "gold_key": gold_key,
    }
