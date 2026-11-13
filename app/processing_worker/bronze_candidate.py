"""Bounded, immutable Bronze *candidate* builder; not a publication executor.

Trusted callers supply a pinned S3 ObjectIdentity and an injected S3 client.
No job is claimed/completed, no dataset head changes and no direct stage endpoint
is exposed here. A separately reviewed worker must fence and publish receipts.
"""
from dataclasses import dataclass
from uuid import UUID

from app.storage.versioned_artifacts import (
    ObjectIdentity, StorageIntegrityError, VersionedArtifacts, validate_csv_source,
)

MAX_SOURCE_BYTES = 5 * 1024 * 1024
MAX_BRONZE_ROWS = 100_000
MAX_BRONZE_COLUMNS = 128


@dataclass(frozen=True, slots=True)
class BronzeCandidate:
    source: ObjectIdentity
    inferred: ObjectIdentity
    lexical: ObjectIdentity
    schema_hash: str
    rows: int
    columns: tuple[str, ...]


def build_bronze_candidate(client, bucket, source: ObjectIdentity, candidate_id: UUID, *,
                           checkpoint=lambda: None) -> BronzeCandidate:
    """Verify immutable source and stage two independently verifiable Parquets.

    Candidate keys are unique immutable namespaces and may be orphaned if the
    owner lease expires. Publication must be a separate fenced transaction.
    """
    import io
    import pandas as pd
    from app.processing.schema_fingerprint import calculate_schema_hash

    if not isinstance(candidate_id, UUID) or not isinstance(source, ObjectIdentity):
        raise TypeError('Trusted candidate UUID and registered source identity required')
    if source.size > MAX_SOURCE_BYTES:
        raise StorageIntegrityError('Bronze source exceeds reviewed size bound')
    storage = VersionedArtifacts(client, bucket, max_bytes=32*1024*1024,
                                 max_total_bytes=128*1024*1024, checkpoint=checkpoint)
    payload = storage.read(source)
    validate_csv_source(payload)
    checkpoint()
    try:
        lexical = pd.read_csv(io.BytesIO(payload), dtype=str, keep_default_na=False,
                              na_filter=False)
        inferred = pd.read_csv(io.BytesIO(payload))
    except (ValueError, UnicodeError, pd.errors.ParserError) as exc:
        raise StorageIntegrityError('Unsupported Bronze CSV representation') from exc
    if (lexical.empty or len(lexical) > MAX_BRONZE_ROWS
        or len(lexical.columns) > MAX_BRONZE_COLUMNS
        or list(lexical.columns) != list(inferred.columns)
        or len(lexical) != len(inferred)):
        raise StorageIntegrityError('Bronze CSV structure exceeds reviewed bounds')
    columns = tuple(lexical.columns)
    schema_hash = calculate_schema_hash(inferred)
    inferred['_source_file_sha256'] = source.sha256
    # No nondeterministic ingestion timestamp: immutable candidate is replayable.
    def parquet(frame):
        stream = io.BytesIO()
        frame.to_parquet(stream, index=False, engine='pyarrow')
        value = stream.getvalue()
        if not value or len(value) > 32*1024*1024:
            raise StorageIntegrityError('Bronze Parquet exceeds reviewed size bound')
        return value
    inferred_bytes = parquet(inferred)
    checkpoint()
    lexical_bytes = parquet(lexical)
    checkpoint()
    prefix = f'artifact-candidates/{candidate_id}/bronze'
    inferred_pin = storage.put(f'{prefix}/data.parquet', inferred_bytes)
    checkpoint()
    lexical_pin = storage.put(f'{prefix}/raw_text.parquet', lexical_bytes)
    checkpoint()
    return BronzeCandidate(source=source, inferred=inferred_pin, lexical=lexical_pin,
                           schema_hash=schema_hash, rows=len(lexical), columns=columns)
