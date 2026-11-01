"""Offline-verifiable candidate evidence; not a live publisher or Parquet validator.

Trusted expected values must come from a server-side stage intent. This
helper grants no authorization, renews no lease and publishes no metadata.
"""
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID


class ArtifactVerificationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class VerifiedCandidate:
    intent_id: UUID
    candidate_id: UUID
    stage: str
    input_fingerprint: str
    manifest_sha256: str
    artifact_count: int


def _digest(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ArtifactVerificationError('Duplicate manifest field')
        result[key] = value
    return result


def verify_candidate(manifest_bytes: bytes, *, manifest_sha256: str, intent_id: UUID,
                     candidate_id: UUID, stage: str, input_fingerprint: str,
                     expected_roles: frozenset[str], read_artifact: Callable[[str], bytes],
                     max_artifact_bytes: int) -> VerifiedCandidate:
    """Check the exact manifest and all required bytes against trusted pins.

    Size limits apply to each returned byte payload; the injected reader must
    also enforce streaming/download limits before allocating those bytes.
    Artifact schema/row-count/lineage validation is a separate required step.
    """
    if stage not in {'BRONZE', 'SILVER', 'GOLD'} or not isinstance(intent_id, UUID) or not isinstance(candidate_id, UUID):
        raise ArtifactVerificationError('Invalid stage identity')
    if not _digest(manifest_sha256) or not _digest(input_fingerprint):
        raise ArtifactVerificationError('Invalid checksum pin')
    if not isinstance(expected_roles, frozenset) or not expected_roles or not all(isinstance(r, str) and r for r in expected_roles):
        raise ArtifactVerificationError('Explicit required artifact roles are necessary')
    if type(max_artifact_bytes) is not int or max_artifact_bytes <= 0:
        raise ArtifactVerificationError('A positive artifact size limit is necessary')
    if not isinstance(manifest_bytes, bytes) or len(manifest_bytes) > 1048576:
        raise ArtifactVerificationError('Invalid manifest payload')
    if hashlib.sha256(manifest_bytes).hexdigest() != manifest_sha256:
        raise ArtifactVerificationError('Manifest checksum mismatch')
    try:
        manifest = json.loads(manifest_bytes, object_pairs_hook=_object)
    except (ValueError, UnicodeError) as exc:
        raise ArtifactVerificationError('Invalid manifest JSON') from exc
    fields = {'version', 'intent_id', 'candidate_id', 'stage', 'input_fingerprint', 'artifacts'}
    if not isinstance(manifest, dict) or set(manifest) != fields:
        raise ArtifactVerificationError('Incomplete or unsupported manifest')
    if (type(manifest['version']) is not int or manifest['version'] != 1
        or manifest['intent_id'] != str(intent_id) or manifest['candidate_id'] != str(candidate_id)
        or manifest['stage'] != stage or manifest['input_fingerprint'] != input_fingerprint):
        raise ArtifactVerificationError('Manifest intent differs from trusted pins')
    artifacts = manifest['artifacts']
    if not isinstance(artifacts, list) or len(artifacts) != len(expected_roles):
        raise ArtifactVerificationError('Required artifact set is incomplete')
    roles, keys = set(), set()
    prefix = f'artifact-candidates/{candidate_id}/'
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) != {'role', 'key', 'sha256', 'size_bytes'}:
            raise ArtifactVerificationError('Invalid artifact descriptor')
        role, key = artifact['role'], artifact['key']
        size = artifact['size_bytes']
        if not isinstance(role, str) or role not in expected_roles or role in roles:
            raise ArtifactVerificationError('Invalid or duplicate artifact role')
        if (not isinstance(key, str) or not key.startswith(prefix) or key in keys
            or '\\' in key or any(p in {'', '.', '..'} for p in key.split('/'))):
            raise ArtifactVerificationError('Artifact outside immutable candidate scope')
        if not _digest(artifact['sha256']) or type(size) is not int or not 0 < size <= max_artifact_bytes:
            raise ArtifactVerificationError('Invalid artifact size or checksum')
        roles.add(role)
        keys.add(key)
    # Validate every descriptor before invoking any external reader.
    for artifact in artifacts:
        payload = read_artifact(artifact['key'])
        if not isinstance(payload, bytes) or len(payload) != artifact['size_bytes']:
            raise ArtifactVerificationError('Stored artifact size mismatch')
        if hashlib.sha256(payload).hexdigest() != artifact['sha256']:
            raise ArtifactVerificationError('Stored artifact checksum mismatch')
    return VerifiedCandidate(intent_id, candidate_id, stage, input_fingerprint,
                             manifest_sha256, len(artifacts))
