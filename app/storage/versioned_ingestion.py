"""Bounded version capture for explicitly enabled durable upload completion."""
import hashlib
from uuid import uuid4

from app.storage.versioned_artifacts import ObjectIdentity, StorageIntegrityError


def read_staged(client, bucket, key, expected_size, content_type=None):
    if type(expected_size) is not int or not 0 < expected_size <= 32*1024*1024:
        raise ValueError('Upload exceeds durable processing byte bound')
    metadata = client.head_object(Bucket=bucket, Key=key)
    version = metadata.get('VersionId')
    if version in (None,'','null') or metadata.get('ContentLength') != expected_size:
        raise StorageIntegrityError('Versioned staging object unavailable')
    if content_type and metadata.get('ContentType') != content_type:
        raise StorageIntegrityError('Staging content type changed')
    response = client.get_object(Bucket=bucket, Key=key, VersionId=version)
    body = response['Body']
    try:
        payload = body.read(expected_size+1)
    finally:
        body.close()
    if response.get('VersionId') != version or len(payload) != expected_size:
        raise StorageIntegrityError('Staging version changed or is incomplete')
    return payload, hashlib.sha256(payload).hexdigest()


def write_raw(client, bucket, payload, sha256, filename):
    if '/' in filename or '\\' in filename or '..' in filename or not filename:
        raise ValueError('Invalid source filename')
    key = f'raw/{sha256}/{uuid4().hex}/{filename}'
    response = client.put_object(Bucket=bucket, Key=key, Body=payload, IfNoneMatch='*')
    identity = ObjectIdentity.decode(dict(key=key,version=response.get('VersionId'),sha256=sha256,size=len(payload)))
    from app.storage.versioned_artifacts import VersionedArtifacts
    VersionedArtifacts(client,bucket).read(identity)
    return identity
