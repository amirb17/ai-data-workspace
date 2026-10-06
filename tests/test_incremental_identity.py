from datetime import date, datetime, timezone, timedelta
from decimal import Decimal, localcontext
import subprocess
import sys

import pytest
from pydantic import ValidationError
from app.processing.row_identity import business_key_hash, row_content_hash, normalize_value
from app.schemas.incremental import LoadPolicyRequest


def test_deterministic_ordered_key_and_content_without_operational_metadata():
    row = {'id':12, 'line':2, 'name':'Amir', '_source_file_id':42}
    assert business_key_hash(row,['id','line']) != business_key_hash(row,['line','id'])
    expected = row_content_hash(row,['id','line','name'])
    assert expected == row_content_hash({**row,'_source_file_id':99},['name','line','id'])
    code = "from app.processing.row_identity import row_content_hash; print(row_content_hash({'name':'Amir','line':2,'id':12},['name','line','id']))"
    assert subprocess.check_output([sys.executable,'-c',code],text=True).strip() == expected


def test_normalized_types_preserve_business_meaning():
    assert normalize_value(1) == normalize_value(Decimal('1.000')) == normalize_value(1.0)
    assert normalize_value(-0.0) == normalize_value(0)
    assert normalize_value(True) != normalize_value(1)
    assert normalize_value(None) == normalize_value(float('nan'))
    assert normalize_value(None) != normalize_value('')
    assert normalize_value(' A ') != normalize_value('A') != normalize_value('a')
    assert normalize_value(date(2026,10,6)) != normalize_value('2026-10-06')
    utc = datetime(2026,10,6,12,tzinfo=timezone.utc)
    assert normalize_value(utc) == normalize_value(utc.astimezone(timezone(timedelta(hours=5,minutes=30))))
    with localcontext() as ctx:
        ctx.prec=2
        assert normalize_value(Decimal('123456789.12345678900')) == ['number','123456789.123456789']


@pytest.mark.parametrize('value',[datetime(2026,10,6),float('inf'),Decimal('NaN'),{'secret':'value'}])
def test_ambiguous_or_unsupported_identity_rejected(value):
    with pytest.raises(ValueError): normalize_value(value)


@pytest.mark.parametrize('columns',[[],['missing'],['id','id'],['_dq_is_valid']])
def test_invalid_identity_columns_rejected(columns):
    with pytest.raises(ValueError): business_key_hash({'id':1,'_dq_is_valid':True},columns)


def test_null_business_key_rejected():
    with pytest.raises(ValueError): business_key_hash({'id':None},['id'])


@pytest.mark.parametrize('strategy',['UPSERT','SNAPSHOT'])
def test_keyed_modes_require_explicit_keys(strategy):
    with pytest.raises(ValidationError): LoadPolicyRequest(dataset_version_id=1,expected_policy_version=0,load_strategy=strategy,schema_evolution_policy='STRICT')


def test_explicit_composite_policy_and_append_without_key():
    request = LoadPolicyRequest(dataset_version_id=1,expected_policy_version=0,load_strategy='UPSERT',business_keys=['order_id','line_number'],schema_evolution_policy='STRICT')
    assert request.business_keys == ['order_id','line_number']
    assert LoadPolicyRequest(dataset_version_id=1,expected_policy_version=0,load_strategy='APPEND',schema_evolution_policy='STRICT').business_keys == []
    for keys in [['id','id'],[' ']]:
        with pytest.raises(ValidationError): LoadPolicyRequest(dataset_version_id=1,expected_policy_version=0,load_strategy='UPSERT',business_keys=keys,schema_evolution_policy='STRICT')
