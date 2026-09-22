import io
from datetime import datetime, timezone

import pandas as pd
from app.processing.dataset_profiler import profile_dataframe
from app.storage.s3_service import get_s3_client, BUCKET_NAME


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
    profiles = profile_dataframe(df)
    row_count = len(df)
    column_names = list(df.columns)

    schema = {
        column: str(dtype)
        for column, dtype in df.dtypes.items()
    }

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
        "profiles": profiles,
    }