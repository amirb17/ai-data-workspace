import io
import json

import pandas as pd
from app.db.file_repository import get_physical_file_by_id
from app.db.processing_repository import get_approved_policy

from app.processing.exact_source import (
    verified_exact_lexical_frame,
)
from app.processing.exact_parquet import (
    write_exact_parquet,
    read_exact_parquet,
)
from app.processing.silver_transformer import (
    exact_decimal_spec,
)
from app.config import get_boto3_session
from app.db.file_repository import (
    get_active_business_rules_for_dataset_version,
)
from app.processing.silver_validator import (
    apply_business_rules,
    calculate_dq_issue_counts,
)
from app.processing.silver_transformer import (
    apply_common_cleaning,
    apply_transformations,
)


s3_client = get_boto3_session().client("s3")


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
def _write_exact_parquet_to_s3(
    df: pd.DataFrame,
    bucket_name: str,
    key: str,
    approved_rules: list,
    business_columns: list[str],
) -> dict:
    """
    Write explicitly typed exact Decimal Parquet.

    Preserve the approved physical type even when the
    entire Decimal column is NULL or the frame is empty.
    """

    payload, contract = write_exact_parquet(
        df,
        approved_rules,
        business_columns,
    )

    s3_client.put_object(
        Bucket=bucket_name,
        Key=key,
        Body=payload,
        ContentType="application/octet-stream",
    )

    # Verify the actual stored bytes, not just the
    # in-memory serialized representation.
    response = s3_client.get_object(
        Bucket=bucket_name,
        Key=key,
    )

    body = response["Body"]

    try:
        stored = body.read(len(payload) + 1)
    finally:
        body.close()

    if stored != payload:
        raise ValueError(
            "Exact Silver Parquet readback differs"
        )

    read_exact_parquet(
        stored,
        contract,
        approved_rules,
        business_columns,
        expected_columns=list(df.columns),
    )

    return contract

def process_silver(
    file_id: int,
    dataset_version_id: int,
    rule_version: int,
    bucket_name: str,
    approved_rules: list | None = None,
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

    rules = approved_rules if approved_rules is not None else get_active_business_rules_for_dataset_version(dataset_version_id)
    if not rules:
        raise ValueError(
            f"No active business rules found "
            f"for dataset_version_id={dataset_version_id}"
        )
        # Detect complete exact-decimal contracts.
    # Malformed contracts must fail closed.
    exact_rules = [
        rule
        for rule in rules
        if (
            rule[3] == "DATA_TYPE"
            and exact_decimal_spec(rule[4]) is not None
        )
    ]

    lexical_frame = None

    if exact_rules:
        # Exact conversion requires the immutable approved
        # rule snapshot, never mutable active rules alone.
        pinned_rules = get_approved_policy(
            dataset_version_id,
            rule_version,
        )

        if (
            approved_rules is None
            or rules != pinned_rules
        ):
            raise ValueError(
                "Exact Decimal requires matching "
                "pinned approved rules"
            )

        physical_file = get_physical_file_by_id(
            file_id
        )

        lexical_frame = verified_exact_lexical_frame(
            file_id=file_id,
            physical_file=physical_file,
            bronze_frame=bronze_df,
            client=s3_client,
            bucket=bucket_name,
        )
    # Apply DQ rules

    # Apply Silver transformations
    cleaned_df = apply_common_cleaning(
    bronze_df
    )
    (
        transformed_df,
        transformation_errors,
        original_null_masks,
    ) = apply_transformations(
        df=cleaned_df,
        rules=rules,
        lexical_frame=lexical_frame,
    )

    validated_df = apply_business_rules(
        df=transformed_df,
        rules=rules,
        transformation_errors=transformation_errors,
        original_null_masks=original_null_masks,
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
        f"silver/"
        f"dataset_version_id={dataset_version_id}/"
        f"file_id={file_id}/"
        f"rule_version={rule_version}/"
        f"data.parquet"
    )

    quarantine_key = (
        f"quarantine/"
        f"dataset_version_id={dataset_version_id}/"
        f"file_id={file_id}/"
        f"rule_version={rule_version}/"
        f"data.parquet"
    )

    # Write valid rows to Silver

    if exact_rules:
        # The lexical frame has already been verified
        # against the registered original CSV.
        business_columns = list(
            lexical_frame.columns
        )

        _write_exact_parquet_to_s3(
            df=silver_output,
            bucket_name=bucket_name,
            key=silver_key,
            approved_rules=rules,
            business_columns=business_columns,
        )

    else:
        _write_parquet_to_s3(
            df=silver_output,
            bucket_name=bucket_name,
            key=silver_key,
        )

    # Write rejected rows if any

    if not quarantine_output.empty:

        if exact_rules:
            _write_exact_parquet_to_s3(
                df=quarantine_output,
                bucket_name=bucket_name,
                key=quarantine_key,
                approved_rules=rules,
                business_columns=business_columns,
            )

        else:
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
