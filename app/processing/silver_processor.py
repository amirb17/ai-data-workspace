import io
import json

import boto3
import pandas as pd

from app.db.file_repository import get_active_business_rules
from app.processing.silver_validator import (
    apply_business_rules,
    calculate_dq_issue_counts,
)


session = boto3.Session(
    profile_name="ai-data-workspace"
)

s3_client = session.client(
    "s3",
    region_name="ap-south-1",
)


def _prepare_for_parquet(df: pd.DataFrame) -> pd.DataFrame:
    output = df.copy()

    if "_dq_violations" in output.columns:
        output["_dq_violations"] = output[
            "_dq_violations"
        ].apply(json.dumps)

    return output


def _write_parquet_to_s3(
    df: pd.DataFrame,
    bucket_name: str,
    key: str,
) -> None:

    buffer = io.BytesIO()

    df.to_parquet(
        buffer,
        index=False,
        engine="pyarrow",
    )

    buffer.seek(0)

    s3_client.put_object(
        Bucket=bucket_name,
        Key=key,
        Body=buffer.getvalue(),
        ContentType="application/octet-stream",
    )


def process_silver(
    file_id: int,
    bucket_name: str,
):
    # Locate Bronze dataset

    bronze_key = (
        f"bronze/file_id={file_id}/data.parquet"
    )

    # Read Bronze from S3

    response = s3_client.get_object(
        Bucket=bucket_name,
        Key=bronze_key,
    )

    parquet_bytes = response["Body"].read()

    bronze_df = pd.read_parquet(
        io.BytesIO(parquet_bytes)
    )

    if bronze_df.empty:
        raise ValueError(
            f"Bronze dataset for file_id={file_id} is empty"
        )

    # Load active business rules

    rules = get_active_business_rules(file_id)

    if not rules:
        raise ValueError(
            f"No active business rules found "
            f"for file_id={file_id}"
        )

    # Apply DQ rules

    validated_df = apply_business_rules(
        df=bronze_df,
        rules=rules,
    )

    # Split valid and rejected rows

    silver_df = validated_df[
        validated_df["_dq_is_valid"]
    ].copy()

    quarantine_df = validated_df[
        ~validated_df["_dq_is_valid"]
    ].copy()
    dq_issues = calculate_dq_issue_counts(
    quarantine_df
    )
    # Prepare for Parquet

    silver_output = _prepare_for_parquet(
        silver_df
    )

    quarantine_output = _prepare_for_parquet(
        quarantine_df
    )

    # Define output locations

    silver_key = (
        f"silver/file_id={file_id}/data.parquet"
    )

    quarantine_key = (
        f"quarantine/file_id={file_id}/data.parquet"
    )

    # Write valid rows to Silver

    _write_parquet_to_s3(
        df=silver_output,
        bucket_name=bucket_name,
        key=silver_key,
    )

    # Write rejected rows if any

    if not quarantine_output.empty:
        _write_parquet_to_s3(
            df=quarantine_output,
            bucket_name=bucket_name,
            key=quarantine_key,
        )

    # Return processing result

    return {
    "silver_df": silver_df,
    "quarantine_df": quarantine_df,
    "total_rows": len(validated_df),
    "valid_rows": len(silver_df),
    "rejected_rows": len(quarantine_df),
    "silver_key": silver_key,
    "quarantine_key": (
        quarantine_key
        if not quarantine_output.empty
        else None
    ),
    "dq_issues": dq_issues,
    }