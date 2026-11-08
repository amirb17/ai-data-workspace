
"""Dependency-injected versioned storage for durable processing."""

from app.storage.versioned_artifacts import VersionedArtifacts


def make_storage_factory(s3_client, bucket: str):
    """Bind an injected S3 client and bucket to a worker lease checkpoint."""
    if not isinstance(bucket, str) or not bucket.strip():
        raise ValueError("A nonempty S3 bucket name is required")

    if s3_client is None:
        raise ValueError("An S3 client is required")

    def create_storage(checkpoint):
        if not callable(checkpoint):
            raise TypeError("A callable lease checkpoint is required")

        return VersionedArtifacts(
            s3_client,
            bucket,
            checkpoint=checkpoint,
        )

    return create_storage
