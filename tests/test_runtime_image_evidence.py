"""Tiny synthetic OCI archives; no actual images, registry or Docker execution."""
import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest
from tools import verify_runtime_image as images

# Synthetic review identities; none are claims about a deployed/base image.
REVISION='a'*40
REQUIREMENTS='b'*64
BASE='synthetic-base@sha256:'+'c'*64
MANIFEST='application/vnd.oci.image.manifest.v1+json'


def artifact(tmp_path,*,role='worker',fault=None):
    blobs={}
    def blob(payload,media):
        body=payload if isinstance(payload,bytes) else json.dumps(payload,separators=(',',':')).encode()
        digest='sha256:'+hashlib.sha256(body).hexdigest();name='blobs/sha256/'+digest[7:];blobs[name]=body
        return {'mediaType':media,'size':len(body),'digest':digest}
    meta={'User':'worker','Cmd':['python','-m','app.processing_worker.container_entrypoint'],
        'Env':['DATARISE_PROCESSING_DEPLOYMENT_APPROVED=false','DATARISE_PROCESSING_READERS=false','DATARISE_DISPATCH_APPROVED=false'],
        'Labels':{'org.opencontainers.image.revision':REVISION,'io.datarise.base-image':BASE,'io.datarise.requirements-sha256':REQUIREMENTS}}
    if role=='verifier':meta.update(User='',Cmd=['app.lambda_worker.verifier_handler.lambda_handler'],Entrypoint=['/lambda-entrypoint.sh'])
    arch='arm64' if fault=='architecture' else 'amd64'
    if fault=='root':meta['User']='0'
    if fault=='command':meta['Cmd']=['synthetic-unapproved-command']
    if fault=='entrypoint':meta['Entrypoint']=['synthetic-unapproved-entrypoint']
    if fault=='enabled':meta['Env'][0]='DATARISE_PROCESSING_DEPLOYMENT_APPROVED=true'
    if fault=='duplicate-env':meta['Env'].append(meta['Env'][0])
    if fault=='secret-env':meta['Env'].append('DATABASE_URL=SYNTHETIC_DO_NOT_PRINT')
    if fault=='revision':meta['Labels']['org.opencontainers.image.revision']='d'*40
    if fault=='requirements':meta['Labels']['io.datarise.requirements-sha256']='e'*64
    config=blob({'os':'linux','architecture':arch,'config':meta},'application/vnd.oci.image.config.v1+json')
    layer=blob(b'synthetic-layer-bytes','application/vnd.oci.image.layer.v1.tar')
    manifest=blob({'schemaVersion':2,'mediaType':MANIFEST,'config':config,'layers':[layer]},MANIFEST)
    index={'schemaVersion':2,'manifests':[manifest]}
    if fault=='multi':index['manifests'].append(manifest)
    if fault=='nested-index':index['manifests'][0]=dict(manifest,mediaType='application/vnd.oci.image.index.v1+json')
    if fault=='corrupt-layer':blobs['blobs/sha256/'+layer['digest'][7:]]=b'X'*layer['size']
    if fault=='missing-layer':del blobs['blobs/sha256/'+layer['digest'][7:]]
    if fault=='extra-blob':blobs['blobs/sha256/'+'0'*64]=b'synthetic-extraneous'
    path=tmp_path/'synthetic-oci.tar'
    with tarfile.open(path,'w') as tar:
        def add(name,body):
            info=tarfile.TarInfo(name);info.size=len(body);tar.addfile(info,io.BytesIO(body))
        add('oci-layout',b'{"imageLayoutVersion":"1.0.0"}')
        body=json.dumps(index).encode();add('index.json',body)
        for name,value in blobs.items():add(name,value)
        if fault=='duplicate-member':add('index.json',body)
        if fault=='traversal':add('../synthetic-outside',b'not-extracted')
        if fault=='symlink':
            info=tarfile.TarInfo('synthetic-link');info.type=tarfile.SYMTYPE;info.linkname='/synthetic-outside';tar.addfile(info)
    return path,manifest['digest']


def check(path,digest,role='worker'):
    return images.verify_archive(path,expected_digest=digest,role=role,revision=REVISION,requirements_hash=REQUIREMENTS,base_image=BASE)


@pytest.mark.parametrize('role',['worker','verifier'])
def test_identity_and_single_platform_contract(tmp_path,role):
    path,digest=artifact(tmp_path,role=role)
    result=check(path,digest,role)
    assert result['manifest_digest']==digest and result['layers_checked']==1
    assert result['architecture']=='linux/amd64' and result['deployment_approved'] is False
    assert result['provenance_authenticated'] is False


@pytest.mark.parametrize('fault',['architecture','root','command','entrypoint','enabled','duplicate-env','secret-env','revision',
    'requirements','multi','nested-index','corrupt-layer','missing-layer','extra-blob','duplicate-member','traversal','symlink'])
def test_bad_or_unsafe_image_evidence_is_rejected_without_extraction(tmp_path,fault):
    path,digest=artifact(tmp_path,fault=fault)
    with pytest.raises(ValueError):check(path,digest)
    assert not (tmp_path/'synthetic-outside').exists()


def test_unreviewed_digest_or_tag_not_accepted(tmp_path):
    path,digest=artifact(tmp_path)
    with pytest.raises(ValueError):check(path,'sha256:'+'0'*64)
    with pytest.raises(ValueError):check(path,'synthetic:latest')
    with pytest.raises(ValueError):images.verify_archive(path,expected_digest=digest,role='worker')


def test_archive_bounds_apply_before_reading_image(tmp_path,monkeypatch):
    path,digest=artifact(tmp_path)
    monkeypatch.setattr(images,'MAX_ARCHIVE',10)
    with pytest.raises(ValueError):check(path,digest)
