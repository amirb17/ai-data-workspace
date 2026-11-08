"""Verify an uncompressed local OCI archive without extraction or execution.

No Docker daemon, registry, SDK or external command is used. Labels are compared
to reviewer-provided expectations; they are not a signed build attestation.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tarfile

MAX_ARCHIVE = 2 * 1024**3
MAX_BLOB = 512 * 1024**2
MAX_JSON = 1024**2
MANIFEST_TYPES = {'application/vnd.oci.image.manifest.v1+json',
                  'application/vnd.docker.distribution.manifest.v2+json'}


def verify_archive(path, *, expected_digest, role, revision=None, requirements_hash=None,
                   base_image=None):
    path = Path(path)
    if role not in {'worker', 'verifier', 'base'} or not re.fullmatch(r'sha256:[a-f0-9]{64}', expected_digest):
        raise ValueError('Invalid image review expectations')
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_ARCHIVE:
        raise ValueError('Unsuitable local OCI archive')
    with tarfile.open(path, mode='r:') as archive:
        members = {}
        seen = set()
        referenced = {'oci-layout', 'index.json'}
        total = 0
        for member in archive:
            name = member.name
            if ('\\' in name or PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts
                or str(PurePosixPath(name)) != name.rstrip('/') or name in seen or len(name) > 4096):
                raise ValueError('Unsafe archive path')
            seen.add(name)
            if len(seen) > 10000:
                raise ValueError('OCI member bound exceeded')
            if member.isdir():
                continue
            if not member.isfile() or not (name in {'index.json', 'oci-layout'} or re.fullmatch(r'blobs/sha256/[a-f0-9]{64}', name)):
                raise ValueError('Unsupported archive member')
            total += member.size
            if member.size < 0 or member.size > MAX_BLOB or total > MAX_ARCHIVE:
                raise ValueError('OCI archive bounds exceeded')
            members[name] = member

        def read(name, limit):
            member = members.get(name)
            if member is None or member.size > limit:
                raise ValueError('Missing or oversized OCI metadata')
            with archive.extractfile(member) as stream:
                value = stream.read(limit+1)
            if len(value) != member.size:
                raise ValueError('Incomplete OCI member')
            return value

        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('Duplicate OCI metadata field')
                result[key] = value
            return result

        def document(name):
            value = json.loads(read(name, MAX_JSON), object_pairs_hook=unique)
            if not isinstance(value, dict):
                raise ValueError('Invalid OCI document')
            return value

        if document('oci-layout') != {'imageLayoutVersion': '1.0.0'}:
            raise ValueError('Unsupported OCI layout')
        index = document('index.json')
        descriptors = index.get('manifests')
        if index.get('schemaVersion') != 2 or not isinstance(descriptors, list) or len(descriptors) != 1:
            raise ValueError('Exactly one image manifest is required')

        def checked_blob(descriptor):
            digest, size = descriptor.get('digest'), descriptor.get('size')
            if not isinstance(digest, str) or not re.fullmatch(r'sha256:[a-f0-9]{64}', digest) or type(size) is not int:
                raise ValueError('Invalid OCI descriptor')
            name = 'blobs/sha256/'+digest[7:]
            member = members.get(name)
            if member is None or member.size != size:
                raise ValueError('OCI descriptor size differs')
            checksum = hashlib.sha256()
            with archive.extractfile(member) as stream:
                while chunk := stream.read(1024**2):
                    checksum.update(chunk)
            if 'sha256:'+checksum.hexdigest() != digest:
                raise ValueError('OCI blob digest differs')
            referenced.add(name)
            return name

        descriptor = descriptors[0]
        if descriptor.get('mediaType') not in MANIFEST_TYPES or descriptor.get('digest') != expected_digest:
            raise ValueError('Unexpected or multi-platform image manifest')
        if descriptor.get('platform') not in (None, {'os': 'linux', 'architecture': 'amd64'}):
            raise ValueError('Unexpected OCI platform descriptor')
        manifest = document(checked_blob(descriptor))
        if manifest.get('schemaVersion') != 2 or manifest.get('mediaType') not in MANIFEST_TYPES:
            raise ValueError('Unsupported image manifest')
        config_descriptor = manifest.get('config', {})
        if config_descriptor.get('mediaType') not in {'application/vnd.oci.image.config.v1+json', 'application/vnd.docker.container.image.v1+json'}:
            raise ValueError('Unsupported image configuration')
        config = document(checked_blob(config_descriptor))
        if config.get('os') != 'linux' or config.get('architecture') != 'amd64':
            raise ValueError('Only reviewed linux/amd64 images are supported')
        layers = manifest.get('layers')
        if not isinstance(layers, list) or not 1 <= len(layers) <= 128:
            raise ValueError('Invalid OCI layer inventory')
        for layer in layers:
            if layer.get('mediaType') not in {'application/vnd.oci.image.layer.v1.tar',
                'application/vnd.oci.image.layer.v1.tar+gzip', 'application/vnd.docker.image.rootfs.diff.tar.gzip'}:
                raise ValueError('Unsupported image layer type')
            checked_blob(layer)
        if set(members) != referenced:
            raise ValueError('Unreferenced OCI archive content')
        if role != 'base':
            if not re.fullmatch(r'[a-f0-9]{40}', revision or '') or not re.fullmatch(r'[a-f0-9]{64}', requirements_hash or ''):
                raise ValueError('Approved source and dependency identities are required')
            if not re.fullmatch(r'[^\s@]+@sha256:[a-f0-9]{64}', base_image or ''):
                raise ValueError('Reviewed pinned base is required')
            metadata = config.get('config', {})
            labels = metadata.get('Labels', {})
            if (labels.get('org.opencontainers.image.revision') != revision
                or labels.get('io.datarise.requirements-sha256') != requirements_hash
                or labels.get('io.datarise.base-image') != base_image):
                raise ValueError('Build identities differ')
            env = {}
            for item in metadata.get('Env', []):
                key, separator, value = item.partition('=')
                if not separator or key in env:
                    raise ValueError('Ambiguous image environment')
                env[key] = value
            if any(env.get(flag) != 'false' for flag in ('DATARISE_PROCESSING_DEPLOYMENT_APPROVED', 'DATARISE_PROCESSING_READERS', 'DATARISE_DISPATCH_APPROVED')):
                raise ValueError('Image activation defaults are unsafe')
            if any(env.get(key) for key in ('DATABASE_URL', 'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN', 'GEMINI_API_KEY')):
                raise ValueError('Credential material in image environment')
            expected_cmd = ['python', '-m', 'app.processing_worker.container_entrypoint'] if role == 'worker' else ['app.lambda_worker.verifier_handler.lambda_handler']
            if metadata.get('Cmd') != expected_cmd:
                raise ValueError('Unexpected image command')
            expected_entry = [None, []] if role == 'worker' else [['/lambda-entrypoint.sh']]
            if metadata.get('Entrypoint') not in expected_entry:
                raise ValueError('Unexpected image entrypoint')
            if role == 'worker' and metadata.get('User') not in {'worker', '10001', '10001:10001'}:
                raise ValueError('Worker image must be non-root')
        return {'offline_checks_passed': True, 'deployment_approved': False,
                'manifest_digest': expected_digest, 'architecture': 'linux/amd64',
                'layers_checked': len(layers), 'provenance_authenticated': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('archive')
    parser.add_argument('--digest', required=True)
    parser.add_argument('--role', choices=['base', 'worker', 'verifier'], required=True)
    parser.add_argument('--revision')
    parser.add_argument('--requirements-hash')
    parser.add_argument('--base-image')
    args = parser.parse_args()
    try:
        result = verify_archive(args.archive, expected_digest=args.digest, role=args.role,
                                revision=args.revision, requirements_hash=args.requirements_hash,
                                base_image=args.base_image)
        print(json.dumps(result, sort_keys=True))
    except Exception:
        # Never echo image Env, labels, parser input or arbitrary tar members.
        print('{"offline_checks_passed":false,"deployment_approved":false,"error":"Invalid local image evidence"}')
        raise SystemExit(2)
