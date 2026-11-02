"""Explicitly injected, version-pinned and bounded S3 I/O. No SDK initialization."""
import hashlib
import io
from dataclasses import asdict, dataclass
from uuid import UUID, uuid4


class StorageIntegrityError(ValueError):
    pass


def validate_csv_source(payload):
    """Bound raw structure before pandas allocates inferred and lexical frames."""
    import csv
    try:
        rows = csv.reader(io.StringIO(payload.decode('utf-8-sig')))
        header = next(rows)
        if (not 0 < len(header) <= 128 or len(set(header)) != len(header)
            or any(c.startswith('_source_') or c.startswith('_bronze_') for c in header)):
            raise StorageIntegrityError('Unsupported source columns')
        for count, values in enumerate(rows, 1):
            if count > 100000 or len(values) != len(header):
                raise StorageIntegrityError('Source exceeds bounded tabular contract')
    except (StopIteration, csv.Error) as exc:
        raise StorageIntegrityError('Invalid bounded CSV source') from exc


@dataclass(frozen=True, slots=True)
class ObjectIdentity:
    key: str
    version: str
    sha256: str
    size: int

    @classmethod
    def decode(cls, value):
        obj = cls(**value)
        if (not isinstance(obj.key, str) or not obj.key or obj.key.startswith('/')
            or '\\' in obj.key or any(p in {'', '.', '..'} for p in obj.key.split('/'))
            or not isinstance(obj.version, str) or obj.version in {'', 'null'}
            or type(obj.size) is not int or obj.size <= 0
            or not isinstance(obj.sha256, str) or len(obj.sha256) != 64
            or any(c not in '0123456789abcdef' for c in obj.sha256)):
            raise StorageIntegrityError('Invalid immutable object identity')
        return obj


class VersionedArtifacts:
    def __init__(self, client, bucket, *, max_bytes=32*1024*1024, max_total_bytes=128*1024*1024,
                 checkpoint=lambda: None):
        if not bucket or type(max_bytes) is not int or max_bytes <= 0 or max_total_bytes < max_bytes:
            raise ValueError('Invalid storage limits')
        self.client, self.bucket = client, bucket
        self.max_bytes, self.max_total_bytes = max_bytes, max_total_bytes
        self.checkpoint, self.consumed = checkpoint, 0

    def pin_source(self, key, sha256, size):
        if not key or key.startswith('/') or '\\' in key or any(p in {'', '.', '..'} for p in key.split('/')):
            raise StorageIntegrityError('Invalid registered source key')
        self.checkpoint()
        response = self.client.head_object(Bucket=self.bucket, Key=key)
        identity = ObjectIdentity.decode(dict(key=key, version=response.get('VersionId'), sha256=sha256, size=size))
        self.read(identity)  # Registered SHA/size, never a hash asserted by HEAD.
        return identity

    def read(self, identity):
        identity = ObjectIdentity.decode(asdict(identity))
        if identity.size > self.max_bytes or self.consumed+identity.size > self.max_total_bytes:
            raise StorageIntegrityError('Artifact resource limit exceeded')
        self.checkpoint()
        response = self.client.get_object(Bucket=self.bucket, Key=identity.key, VersionId=identity.version)
        body = response['Body']
        try:
            if response.get('VersionId') != identity.version or response.get('ContentLength') != identity.size:
                raise StorageIntegrityError('Stored object identity changed')
            chunks, count = [], 0
            while True:
                chunk = body.read(min(65536, identity.size-count+1))
                if not chunk:
                    break
                count += len(chunk)
                self.consumed += len(chunk)
                if count > identity.size or self.consumed > self.max_total_bytes:
                    raise StorageIntegrityError('Artifact read exceeds registered size')
                chunks.append(chunk)
                self.checkpoint()
            payload = b''.join(chunks)
        finally:
            body.close()
        if len(payload) != identity.size or hashlib.sha256(payload).hexdigest() != identity.sha256:
            raise StorageIntegrityError('Stored artifact integrity mismatch')
        self.checkpoint()
        return payload

    def put(self, key, payload):
        # Only the server-owned candidate namespace can be written here.
        if not key.startswith('artifact-candidates/') or not isinstance(payload, bytes):
            raise StorageIntegrityError('Unauthorized artifact write')
        if not 0 < len(payload) <= self.max_bytes:
            raise StorageIntegrityError('Artifact write exceeds limit')
        try:
            UUID(key.split('/')[1])
            ObjectIdentity.decode(dict(key=key, version='validation-only', sha256='0'*64, size=len(payload)))
        except (ValueError, IndexError, TypeError) as exc:
            raise StorageIntegrityError('Invalid candidate write namespace') from exc
        self.checkpoint()
        response = self.client.put_object(Bucket=self.bucket, Key=key, Body=payload, IfNoneMatch='*')
        identity = ObjectIdentity.decode(dict(key=key, version=response.get('VersionId'),
            sha256=hashlib.sha256(payload).hexdigest(), size=len(payload)))
        if self.read(identity) != payload:
            raise StorageIntegrityError('Artifact write verification failed')
        return identity


class MemoryVersionedClient:
    """Offline emulator: writes/versions/failures never touch a filesystem or SDK."""
    def __init__(self):
        self.objects, self.latest = {}, {}
        self.calls = []

    def seed(self, key, body):
        version = uuid4().hex
        self.objects[key, version] = body
        self.latest[key] = version
        return ObjectIdentity(key, version, hashlib.sha256(body).hexdigest(), len(body))

    def put_object(self, *, Bucket, Key, Body, IfNoneMatch):
        self.calls.append(('put', Key))
        if IfNoneMatch != '*' or Key in self.latest:
            raise StorageIntegrityError('Object overwrite rejected')
        return {'VersionId': self.seed(Key, Body).version}

    def get_object(self, *, Bucket, Key, VersionId):
        self.calls.append(('get', Key, VersionId))
        payload = self.objects[Key, VersionId]
        return {'Body': io.BytesIO(payload), 'ContentLength': len(payload), 'VersionId': VersionId}

    def head_object(self, *, Bucket, Key):
        version = self.latest[Key]
        return {'VersionId': version, 'ContentLength': len(self.objects[Key, version])}
