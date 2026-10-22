"""Normal tests never call Google. Exercise the real SDK adapter via a stateful fake."""
from dataclasses import replace
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal, localcontext
from copy import deepcopy

import pytest
from google.api_core import exceptions as ge

from app.warehouse.accepted_changes import SourcePin, digest, event, manifest
from app.warehouse.providers.base import Binding, Policy, ProviderError, ErrorCode, TokenState
from app.warehouse.providers.bigquery import Batch, BigQueryTarget, classify
from app.warehouse.providers import physical
from app.warehouse.providers.planning import compile_dimension
from app.warehouse.providers.types import encode, decode, decimal_type
from app.warehouse.scd_hashing import state_digest
from tests.bigquery_fake import Client
from tests.test_warehouse_scd import context, JAN, JUN, OCT


def evidence(c, position=1, at=JAN, city='Pune', *, kind=None, outcome='INSERTED', **overrides):
    pin = SourcePin(workspace_id=1, dataset_id=1, dataset_version_id=1, policy_id=1, rule_version=1,
        rule_sha256='a'*64, application_id=position, upload_id=position, source_dq_run_id=1,
        state_id=position, previous_state_id=position-1 if position > 1 else None,
        artifact_sha256='b'*64, strategy='UPSERT', accepted_at=OCT, delivery_kind=kind)
    row = dict(id='C001', city=city, label='One', effective=at) | overrides
    names = list(c.configuration.business_keys + c.configuration.tracked_attributes + c.configuration.nonhistorical_attributes)
    if c.configuration.effective_time:
        names.append(c.configuration.effective_time.column)
    e = event(pin, 'accepted-business-event', outcome, row, names)
    return manifest(pin, 1, 1, digest(c.configuration.model_dump(mode='json')), [e], position)


def setup_target(c=None, *, client=None, maximum_bytes=1 << 30):
    c = c or context()
    binding = Binding('synthetic-project', 1, 1, 1, 1, 1)
    target = BigQueryTarget(Policy(binding.project, enabled=True, maximum_bytes=maximum_bytes), binding, client or Client())
    target.provision(c)
    return target


def batch(plan, target, n=1):
    return Batch(target.binding, n, digest(['token', n]), digest(['intent', n]), plan)


@pytest.mark.parametrize('scd', [1, 2])
def test_real_adapter_scd_forward_late_correction_retry(scd):
    c = context(scd)
    target = setup_target(c)
    states = ()
    member = None
    for n, at, city, kind in [(1, JAN, 'Pune', None), (2, JUN, 'Mumbai', None),
                               (3, OCT, 'Mumbai', None), (4, JUN, 'Delhi', 'CORRECTION')]:
        plan = compile_dimension(c, [evidence(c, n, at, city, kind=kind)], states)
        b = batch(plan, target, n)
        assert target.lookup(b) == TokenState.NOT_APPLIED
        assert target.apply(b) == plan.result == target.apply(b)
        assert target.lookup(b) == TokenState.APPLIED
        actual = physical.states(c, target.client.rows[target._name(b, 'candidate')])
        assert actual == plan.after
        states = actual
        member = member or states[0].member_sk
        assert member == states[0].member_sk
    if scd == 2:
        original = {v.valid_from: v.version_sk for v in states[0].versions}
        march = datetime(2026, 3, 1, tzinfo=timezone.utc)
        plan = compile_dimension(c, [evidence(c, 5, march, 'Bangalore', kind='BACKFILL')], states)
        b = batch(plan, target, 5)
        assert target.apply(b) == plan.result
        versions = physical.states(c, target.client.rows[target._name(b, 'candidate')])[0].versions
        assert [dict(v.tracked)['city'] for v in versions] == ['Pune', 'Bangalore', 'Delhi']
        assert versions[0].valid_to == march and versions[1].valid_to == JUN
        assert versions[0].version_sk == original[JAN] and versions[-1].version_sk == original[JUN]
    assert all(v[2].maximum_bytes_billed for v in target.client.calls if v[0] in ('query', 'dry'))
    assert all(t['location'] == 'asia-south1' and t['retry_count'] == 0 for t in target.telemetry)


def test_committed_response_loss_lookup_and_no_duplicate_effect():
    target = setup_target(); c = context()
    b = batch(compile_dimension(c, [evidence(c)]), target)
    target.client.lose_response = True
    with pytest.raises(ProviderError) as error:
        target.apply(b)
    assert error.value.uncertain
    restarted = BigQueryTarget(target.policy, target.binding, target.client)
    assert restarted.lookup(b) == TokenState.APPLIED
    assert restarted.apply(b) == b.plan.result
    assert len(target.client.rows[target.binding.physical('journal')]) == 1
    assert len(target.client.rows[target._name(b, 'candidate')]) == 1


def test_transaction_failure_has_no_partial_candidate_or_journal():
    target = setup_target(); c = context(); b = batch(compile_dimension(c, [evidence(c)]), target)
    target.client.fail_transaction = True
    with pytest.raises(ProviderError):
        target.apply(b)
    assert not target.client.rows[target._name(b, 'candidate')]
    assert not target.client.rows[target.binding.physical('journal')]


@pytest.mark.parametrize('corruption', ['attribute', 'key', 'interval', 'duplicate', 'journal', 'missing'])
def test_physical_reconciliation_rejects_corruption(corruption):
    c = context(); target = setup_target(); b = batch(compile_dimension(c, [evidence(c)]), target)
    target.apply(b)
    rows = target.client.rows[target._name(b, 'candidate')]
    if corruption == 'attribute':
        rows[0]['c0'] = 'Changed'
    elif corruption == 'key':
        rows[0]['member_sk'] = 'c'*64
    elif corruption == 'interval':
        rows[0]['valid_to'] = JAN.isoformat()
    elif corruption == 'duplicate':
        rows.append(deepcopy(rows[0]))
    elif corruption == 'journal':
        target.client.rows[target.binding.physical('journal')][0]['intent_sha256'] = 'c'*64
    else:
        rows.clear()
    with pytest.raises(ValueError):
        target.reconcile(b)


@pytest.mark.parametrize('drift', ['location', 'schema', 'cluster', 'expiration', 'labels'])
def test_table_drift_fails_closed(drift):
    c = context(); target = setup_target(); b = batch(compile_dimension(c, [evidence(c)]), target)
    target.apply(b)
    table = target.client.tables[target._name(b, 'candidate')]
    if drift == 'location': table._properties['location'] = 'US'
    if drift == 'schema': table.schema = table.schema[:-1]
    if drift == 'cluster': table.clustering_fields = ['version_sk']
    if drift == 'expiration': table.expires = OCT
    if drift == 'labels': table.labels = {}
    with pytest.raises(ProviderError): target.reconcile(b)


@pytest.mark.parametrize('estimate', [None, -1, 2 << 30])
def test_cost_admission_before_execution(estimate):
    c = context(); target = setup_target(); b = batch(compile_dimension(c, [evidence(c)]), target)
    target.client.estimate = estimate
    with pytest.raises(ProviderError, match='COST_LIMIT_EXCEEDED'): target.apply(b)
    assert not any(call[0] == 'load' for call in target.client.calls)


@pytest.mark.parametrize('value,kind', [
    ('नमस्ते 東京', 'STRING'), (True, 'BOOL'), ((1 << 63)-1, 'INT64'), (-(1 << 63), 'INT64'),
    (Decimal('12345678901234567890123456789.123456789'), 'NUMERIC'),
    (Decimal('12345678901234567890123456789012345678.12345678901234567890123456789012345678'), 'BIGNUMERIC'),
    (date(2026, 1, 1), 'DATE'), (JAN.astimezone(timezone(timedelta(hours=5, minutes=30))), 'TIMESTAMP'),
    (None, 'STRING')])
def test_exact_roundtrip(value, kind):
    with localcontext() as ctx:
        ctx.prec = 6
        assert decode(encode(value, kind), kind) == value


@pytest.mark.parametrize('value,kind', [(1 << 63, 'INT64'), (True, 'INT64'),
    (Decimal('1e-10'), 'NUMERIC'), (Decimal('1e-39'), 'BIGNUMERIC'), (Decimal('1e38'), 'BIGNUMERIC'),
    (1.25, 'BIGNUMERIC'), (datetime(2026, 1, 1), 'TIMESTAMP'), (JAN, 'DATE'), (Decimal('NaN'), 'NUMERIC')])
def test_lossy_values_fail_closed(value, kind):
    with pytest.raises(ValueError): encode(value, kind)


@pytest.mark.parametrize('value,expected', [('1.000000000', 'NUMERIC'), ('1e-10', 'BIGNUMERIC'),
    ('99999999999999999999999999999.999999999', 'NUMERIC'), ('1e29', 'BIGNUMERIC')])
def test_numeric_bounds(value, expected):
    assert decimal_type(Decimal(value)) == expected


@pytest.mark.parametrize('region', ['US', 'EU', 'asia-south2', 'us-central1'])
def test_wrong_region_rejected_before_client(region):
    with pytest.raises(ProviderError, match='LOCATION_MISMATCH'):
        Policy('synthetic-project', location=region)


@pytest.mark.parametrize('error,code', [
    (ge.Unauthorized('secret'), ErrorCode.AUTHENTICATION_FAILED),
    (ge.Forbidden('secret'), ErrorCode.PERMISSION_DENIED),
    (ge.NotFound('secret'), ErrorCode.RESOURCE_NOT_FOUND),
    (ge.Conflict('secret'), ErrorCode.JOB_CONFLICT),
    (ge.TooManyRequests('secret'), ErrorCode.RATE_LIMITED),
    (ge.ServiceUnavailable('secret'), ErrorCode.RETRYABLE_PROVIDER_ERROR),
    (ge.BadRequest('secret'), ErrorCode.PERMANENT_PROVIDER_ERROR),
    (TimeoutError('secret'), ErrorCode.RETRYABLE_PROVIDER_ERROR)])
def test_safe_error_taxonomy(error, code):
    result = classify(error, uncertain=True)
    assert result.code == code and 'secret' not in str(result) and result.uncertain


def test_flags_no_credentials_or_client_until_explicit_call(monkeypatch):
    monkeypatch.delenv('BIGQUERY_ENABLED', raising=False)
    with pytest.raises(ProviderError, match='DISABLED'): Policy.from_environment()
    with pytest.raises(ProviderError, match='DISABLED'):
        BigQueryTarget(Policy('synthetic-project'), Binding('synthetic-project', 1, 1, 1, 1, 1))


def test_generation_workspace_token_reuse_and_provisioning_idempotency():
    c = context(); target = setup_target(); b = batch(compile_dimension(c, [evidence(c)]), target)
    target.provision(c)
    assert len(target.client.datasets) == 2
    target.apply(b)
    changed = batch(compile_dimension(c, [evidence(c, city='Delhi')]), target)
    assert target.lookup(changed) == TokenState.INCONSISTENT
    for binding in (replace(b.binding, generation=2), replace(b.binding, workspace_id=2)):
        other = BigQueryTarget(target.policy, binding, target.client)
        with pytest.raises(ProviderError): other.apply(b)
        assert binding.table != b.binding.table or binding.serving != b.binding.serving


def test_cleanup_retains_recovery_and_candidate_evidence():
    c = context(); target = setup_target(); b = batch(compile_dimension(c, [evidence(c)]), target)
    with pytest.raises(ProviderError): target.cleanup_input(b, finalized=False)
    target.apply(b)
    target.cleanup_input(b, finalized=True)
    assert target._name(b, 'input') not in target.client.tables
    assert target.lookup(b) == TokenState.APPLIED and target.reconcile(b) == b.plan.result


def test_replay_has_stable_keys_and_unchanged_state_new_provenance():
    c = context(); target = setup_target(); m = evidence(c)
    first = compile_dimension(c, [m]); replay = compile_dimension(c, [m], first.after)
    assert first.after == replay.after
    assert target.apply(batch(first, target)) == first.result
    assert target.apply(batch(replay, target, 2)) == replay.result
    assert len(target.client.rows[target.binding.physical('journal')]) == 2


def test_deactivation_and_ambiguous_same_member_events_fail_closed():
    c = context()
    with pytest.raises(ValueError, match='deactivation'):
        compile_dimension(c, [evidence(c, outcome='DEACTIVATED')])


def test_date_scd_roundtrip_and_zero_change_manifest():
    c = context(date_domain=True); m = evidence(c, at=date(2026, 1, 1))
    target = setup_target(c); plan = compile_dimension(c, [m]); b = batch(plan, target)
    assert target.apply(b) == plan.result
    empty = manifest(SourcePin.model_validate_json(__import__('json').dumps(m['pin'])), 1, 1,
        m['configuration_sha256'], [], 2)
    plan = compile_dimension(c, [empty])
    assert target.apply(batch(plan, target, 2)) == plan.result


def test_in_progress_job_never_grants_blind_retry():
    from tests.bigquery_fake import Job
    c = context(); target = setup_target(); b = batch(compile_dimension(c, [evidence(c)]), target)
    job = Job(target._job_id(b, 'apply'), labels=target._job_labels(b)); job.state = 'RUNNING'
    target.client.jobs[job.job_id] = job
    assert target.lookup(b) == TokenState.IN_PROGRESS
    with pytest.raises(ProviderError): target.apply(b)
    assert not any(call[0] == 'load' for call in target.client.calls)


def test_mutation_statement_estimates_have_combined_budget():
    c = context(); target = setup_target(maximum_bytes=300); b = batch(compile_dimension(c, [evidence(c)]), target)
    # Every statement is below the cap; the transactional aggregate is not.
    with pytest.raises(ProviderError, match='COST_LIMIT_EXCEEDED'): target.apply(b)
    assert not any(call[0] == 'query' and call[1].startswith('BEGIN TRANSACTION') for call in target.client.calls)


def test_semantic_effective_column_corruption_is_detected():
    c = context(); target = setup_target(); b = batch(compile_dimension(c, [evidence(c)]), target)
    target.apply(b)
    column = next(column for name, column, _ in physical.columns(c) if name == 'effective')
    target.client.rows[target._name(b, 'candidate')][0][column] = JUN.isoformat()
    with pytest.raises(ProviderError): target.reconcile(b)


def test_readback_byte_budget_rejects_unexpected_wide_rows():
    c = context(); target = setup_target(); b = batch(compile_dimension(c, [evidence(c)]), target)
    target.apply(b)
    target.policy = replace(target.policy, maximum_payload_bytes=2000)
    target.client.rows[target._name(b, 'candidate')][0]['c0'] = 'x'*5000
    with pytest.raises(ProviderError, match='COST_LIMIT_EXCEEDED'): target.reconcile(b)


def test_unknown_job_without_journal_requires_manual_resolution():
    from tests.bigquery_fake import Job
    c = context(); target = setup_target(); b = batch(compile_dimension(c, [evidence(c)]), target)
    job = Job(target._job_id(b, 'apply'), labels=target._job_labels(b))
    target.client.jobs[job.job_id] = job
    assert target.lookup(b) == TokenState.INCONSISTENT
    with pytest.raises(ProviderError): target.apply(b)


def test_unordered_same_member_within_manifest_is_rejected():
    from app.warehouse.accepted_changes import SourcePin
    import json
    c = context(); m = evidence(c)
    pin = SourcePin.model_validate_json(json.dumps(m['pin']))
    events = [event(pin, key, 'UPDATED', dict(id='C001', city=city, label='One', effective=JAN),
        ('id','city','label','effective')) for key, city in [('a','Pune'), ('b','Delhi')]]
    m = manifest(pin, 1, 1, m['configuration_sha256'], events, 1)
    with pytest.raises(ValueError, match='intra-manifest'): compile_dimension(c, [m])


def test_published_candidate_read_requires_matching_independent_checksum():
    c = context(); target = setup_target(); b = batch(compile_dimension(c, [evidence(c)]), target)
    target.apply(b)
    assert target.read_published_candidate(b, b.plan.result) == b.plan.after
    wrong = b.plan.result.model_copy(update=dict(checksum='c'*64))
    with pytest.raises(ProviderError): target.read_published_candidate(b, wrong)


def test_fact_and_provisional_inference_cannot_fabricate_dimension_rows():
    from types import SimpleNamespace
    from app.schemas.warehouse_modeling import FactConfiguration
    fact = FactConfiguration(model_type='FACT', grain=dict(description='One event', identity_columns=('id',)))
    with pytest.raises(ValueError, match='FACT'):
        compile_dimension(SimpleNamespace(configuration=fact), [])
    from app.warehouse.scd_types import InferredMember
    c = context(); placeholder = InferredMember(c, (('id','C001'),), 'a'*64)
    with pytest.raises(ValueError): compile_dimension(c, [evidence(c)], [placeholder])


def test_normal_startup_with_provider_installed_never_resolves_adc():
    import subprocess
    import sys
    code = '''
import os
from unittest.mock import patch
os.environ['WAREHOUSE_ENABLED']='false'
os.environ['BIGQUERY_ENABLED']='false'
with patch('google.auth.default', side_effect=AssertionError('Unexpected ADC lookup')), patch('botocore.client.BaseClient._make_api_call', side_effect=AssertionError('Unexpected cloud API')):
    from app.main import app
    from fastapi.testclient import TestClient
    with TestClient(app) as client:
        assert client.get('/health').status_code == 200
    assert not any('warehouse' in path for path in app.openapi()['paths'])
print('disabled startup passed without ADC or cloud requests')
'''
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert 'disabled startup passed' in result.stdout
