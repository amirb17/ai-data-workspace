
import pytest

from app.processing_worker.storage_factory import make_storage_factory
from app.storage.versioned_artifacts import (
    MemoryVersionedClient,
    VersionedArtifacts,
)


def test_storage_factory_creates_versioned_storage():
    client = MemoryVersionedClient()
    checkpoints = []

    factory = make_storage_factory(client, "test-bucket")
    storage = factory(lambda: checkpoints.append("checked"))

    assert isinstance(storage, VersionedArtifacts)
    assert storage.client is client
    assert storage.bucket == "test-bucket"

    identity = client.seed("raw/example.csv", b"id\n1\n")
    assert storage.read(identity) == b"id\n1\n"
    assert checkpoints


def test_storage_factory_rejects_missing_bucket():
    with pytest.raises(ValueError):
        make_storage_factory(MemoryVersionedClient(), "")


def test_storage_factory_rejects_missing_client():
    with pytest.raises(ValueError):
        make_storage_factory(None, "test-bucket")


def test_storage_factory_rejects_invalid_checkpoint():
    factory = make_storage_factory(
        MemoryVersionedClient(), "test-bucket"
    )

    with pytest.raises(TypeError):
        factory(None)
