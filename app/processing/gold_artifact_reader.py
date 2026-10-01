import io

import pandas as pd

from app.storage.s3_service import (
    BUCKET_NAME,
    get_s3_client,
)


def read_gold_artifact(
    storage_path: str,
) -> pd.DataFrame:
    """
    Read one Gold Parquet artifact from S3
    and return it as a pandas DataFrame.
    """

    if not storage_path:
        raise ValueError(
            "Gold artifact storage path is required"
        )

    s3 = get_s3_client()

    response = s3.get_object(
        Bucket=BUCKET_NAME,
        Key=storage_path,
    )

    parquet_bytes = response["Body"].read()

    df = pd.read_parquet(
        io.BytesIO(parquet_bytes),
        engine="pyarrow",
    )

    return df