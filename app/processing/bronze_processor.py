import io
from datetime import datetime, timezone

import pandas as pd
from app.processing.dataset_profiler import profile_dataframe
from app.storage.s3_service import get_s3_client, BUCKET_NAME
from app.processing.schema_fingerprint import calculate_schema_hash

def run_bronze_stage(
    file_id: int,
    raw_object_key: str,
):
    s3 = get_s3_client()

    response = s3.get_object(
        Bucket=BUCKET_NAME,
        Key=raw_object_key,
    )

    raw_bytes = response["Body"].read()

    df = pd.read_csv(io.BytesIO(raw_bytes))
        # Preserve lexical business values separately from pandas'
    # inferred representation.
    #
    # Future exact-numeric processing must never reconstruct an
    # exact Decimal from an already-inferred binary float.
    raw_text_df = pd.read_csv(
        io.BytesIO(raw_bytes),
        dtype=str,
        keep_default_na=False,
        na_filter=False,
    )

    if (
        list(raw_text_df.columns)
        != list(df.columns)
        or len(raw_text_df) != len(df)
    ):
        raise ValueError(
            "Raw lexical Bronze representation differs "
            "from inferred Bronze structure"
        )
    schema_hash = calculate_schema_hash(df)
    profiles = profile_dataframe(df)
    row_count = len(df)
    column_names = list(df.columns)

    schema = {
        column: str(dtype)
        for column, dtype in df.dtypes.items()
    }
    raw_text_buffer = io.BytesIO()

    raw_text_df.to_parquet(
        raw_text_buffer,
        index=False,
        engine="pyarrow",
    )

    raw_text_key = (
        f"bronze/file_id={file_id}/raw_text.parquet"
    )

    raw_text_buffer.seek(0)

    s3.put_object(
        Bucket=BUCKET_NAME,
        Key=raw_text_key,
        Body=raw_text_buffer.getvalue(),
    )
    df["_source_file_id"] = file_id
    df["_bronze_ingested_at"] = datetime.now(timezone.utc)

    parquet_buffer = io.BytesIO()

    df.to_parquet(
        parquet_buffer,
        index=False,
        engine="pyarrow",
    )

    bronze_object_key = (
        f"bronze/file_id={file_id}/data.parquet"
    )

    parquet_buffer.seek(0)

    s3.put_object(
        Bucket=BUCKET_NAME,
        Key=bronze_object_key,
        Body=parquet_buffer.getvalue(),
    )

    return {
        "bronze_object_key": bronze_object_key,
        "row_count": row_count,
        "column_names": column_names,
        "schema": schema,
        "schema_hash": schema_hash,
        "profiles": profiles,
    }