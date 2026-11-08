"""Synthetic reviewed binding proposals; existing dev/Mumbai contract unchanged."""
from dataclasses import replace
from decimal import Decimal
import pytest
from app.warehouse.sydney_binding import Approval,Registry
from app.warehouse.providers.base import Policy,Binding,ProviderError
from app.warehouse.providers.types import encode,decode


def approval():return Approval('synthetic-project',1,'synthetic-loader@synthetic-project.iam.gserviceaccount.com','synthetic-query@synthetic-project.iam.gserviceaccount.com',1)


def resolve(registry,a,**kw):
    pins=dict(schema_sha256='a'*64,source_fingerprint='b'*64,**kw)
    return registry.resolve(1,expected_fingerprint=a.fingerprint,model_id=2,version_id=3,generation=4,publication_id=5,revision=6,**pins)


def test_empty_registry_never_resolves_from_environment(monkeypatch):
    monkeypatch.setenv('BIGQUERY_LOCATION','australia-southeast1')
    with pytest.raises(PermissionError):resolve(Registry(),approval())
    with pytest.raises(ProviderError):Policy('synthetic-project',location='australia-southeast1')


def test_reviewed_namespace_is_distinct_and_all_generations_pinned():
    a=approval();value=resolve(Registry([a]),a)
    assert value['physical_table']=='synthetic-project.dr_prod_w1.m2_v3_g4_p5_r6'
    assert value['location']=='australia-southeast1'
    assert value['staging_dataset']=='dr_prod_stage_w1'
    assert value['schema_sha256']=='a'*64 and value['source_fingerprint']=='b'*64
    old=Binding('synthetic-project',1,2,3,4,5)
    assert old.location=='asia-south1' and old.serving=='dr_dev_w1'
    assert old.serving!=value['dataset']


@pytest.mark.parametrize('field,value',[('location','asia-south1'),('location','US'),('aws_region','us-east-1'),('environment','development'),('project','unsafe;project'),('workspace_id',True),('revision',0),('loader_identity','foreign-loader@foreign-project.iam.gserviceaccount.com'),('query_identity','synthetic-loader@synthetic-project.iam.gserviceaccount.com')])
def test_unreviewed_binding_inputs_rejected(field,value):
    with pytest.raises(ValueError):replace(approval(),**{field:value})


def test_review_revision_drift_and_duplicate_registry_rejected():
    a=approval()
    with pytest.raises(PermissionError):resolve(Registry([replace(a,revision=2)]),a)
    with pytest.raises(ValueError):Registry([a,a])


@pytest.mark.parametrize('value,kind',[(Decimal('9007199254740993.01'),'NUMERIC'),(Decimal('0.0000000001'),'BIGNUMERIC'),((1<<63)-1,'INT64')])
def test_existing_numeric_physical_contract_remains_exact(value,kind):
    assert decode(encode(value,kind),kind)==value


def test_float_money_is_never_promoted():
    with pytest.raises(ProviderError):encode(9007199254740993.01,'NUMERIC')


@pytest.mark.parametrize('field,value',[('schema_sha256','bad'),('source_fingerprint','B'*64),('schema_sha256',None)])
def test_schema_and_source_generations_must_be_explicit_hashes(field,value):
    a=approval();pins=dict(schema_sha256='a'*64,source_fingerprint='b'*64);pins[field]=value
    with pytest.raises(ValueError,match='schema/source'):
        Registry([a]).resolve(1,expected_fingerprint=a.fingerprint,model_id=2,version_id=3,generation=4,publication_id=5,revision=6,**pins)
