"""Verify original CSV evidence for exact-numeric processing."""

import hashlib
import io

import pandas as pd

from app.processing.schema_fingerprint import calculate_schema_hash
from app.storage.s3_service import parse_s3_uri


MAX_EXACT_SOURCE_BYTES = 32 * 1024 * 1024
MAX_EXACT_ROWS = 100_000
MAX_EXACT_COLUMNS = 128


def _read_bounded(client, bucket, key):
    response = client.get_object(
        Bucket=bucket,
        Key=key,
    )
    body = response["Body"]

    try:
        payload = body.read(MAX_EXACT_SOURCE_BYTES + 1)
    finally:
        body.close()

    if len(payload) > MAX_EXACT_SOURCE_BYTES:
        raise ValueError("Exact source byte bound exceeded")

    return payload


def verified_exact_lexical_frame(
    *,
    file_id,
    physical_file,
    bronze_frame,
    client,
    bucket,
):
    """
    Establish a trusted lexical frame from registered source bytes.

    The lexical sidecar is not trusted independently:
    it must match a fresh parse of the hash-verified original CSV.

    The inferred Bronze rows must also match that original CSV.
    """

    if (
        physical_file is None
        or physical_file[0] != file_id
        or physical_file[5] not in ("UPLOADED", "SUCCESS")
    ):
        raise ValueError(
            "Registered physical source unavailable"
        )

    file_size = physical_file[2]
    expected_sha256 = physical_file[3]
    source_uri = physical_file[4]
    schema_sha256 = physical_file[6]

    if (
        not isinstance(file_size, int)
        or file_size <= 0
        or file_size > MAX_EXACT_SOURCE_BYTES
        or not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
        or not schema_sha256
    ):
        raise ValueError(
            "Incomplete exact source registration"
        )

    source_key = parse_s3_uri(source_uri)

    raw_bytes = _read_bounded(
        client,
        bucket,
        source_key,
    )

    if len(raw_bytes) != file_size:
        raise ValueError(
            "Source size differs from registration"
        )

    if hashlib.sha256(raw_bytes).hexdigest() != expected_sha256:
        raise ValueError(
            "Source SHA-256 differs from registration"
        )

    # Parse the same verified bytes in two representations.
    inferred = pd.read_csv(
        io.BytesIO(raw_bytes),
    )

    lexical = pd.read_csv(
        io.BytesIO(raw_bytes),
        dtype=str,
        keep_default_na=False,
        na_filter=False,
    )

    columns = list(inferred.columns)

    if (
        not columns
        or len(columns) > MAX_EXACT_COLUMNS
        or len(inferred) > MAX_EXACT_ROWS
        or len(inferred) == 0
        or not inferred.columns.is_unique
        or columns != list(lexical.columns)
        or len(inferred) != len(lexical)
        or {"_source_file_id", "_bronze_ingested_at"}
        .intersection(columns)
    ):
        raise ValueError(
            "Invalid exact source structure"
        )

    if calculate_schema_hash(inferred) != schema_sha256:
        raise ValueError(
            "Source schema fingerprint differs"
        )

    # Verify the existing inferred Bronze Parquet against
    # a fresh parse of the original registered source.
    if (
        not set(columns).issubset(bronze_frame.columns)
        or "_source_file_id" not in bronze_frame.columns
        or len(bronze_frame) != len(inferred)
        or not bronze_frame["_source_file_id"].eq(file_id).all()
    ):
        raise ValueError(
            "Bronze source identity or structure differs"
        )

    try:
        pd.testing.assert_frame_equal(
            inferred.reset_index(drop=True),
            bronze_frame[columns].reset_index(drop=True),
            check_dtype=False,
            check_exact=True,
        )
    except AssertionError as error:
        raise ValueError(
            "Bronze rows differ from registered source"
        ) from error

    # Verify the lexical sidecar as well. Sidecar existence
    # alone is not proof of exactness.
    sidecar_key = (
        f"bronze/file_id={file_id}/raw_text.parquet"
    )

    sidecar_bytes = _read_bounded(
        client,
        bucket,
        sidecar_key,
    )

    sidecar = pd.read_parquet(
        io.BytesIO(sidecar_bytes)
    )

    try:
        pd.testing.assert_frame_equal(
            lexical.reset_index(drop=True),
            sidecar.reset_index(drop=True),
            check_dtype=False,
            check_exact=True,
        )
    except AssertionError as error:
        raise ValueError(
            "Lexical sidecar differs from original CSV"
        ) from error

    return lexical