"""Synthetic local evidence only; no infrastructure or secret access."""
import copy
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path

import pytest
from tools import operational_checks as checks
from deployment.docker.verify_lock import verify_lock,check_identity,sha


@pytest.fixture
def baseline():return json.loads(Path('deployment/operations/baseline.json').read_text())


@pytest.fixture
def plan(baseline):
    return {'format_version':'1.2','complete':True,'errored':False,
        'variables':{'aws_region':{'value':baseline['region']},'worker_image_digest':{'value':baseline['worker_digest']}},
        'planned_values':{'root_module':{'resources':[
            {'type':'aws_caller_identity','values':{'account_id':baseline['account']}},
            {'address':'aws_instance.worker[0]','values':{'id':baseline['instance_id']}},
            {'address':'aws_lambda_function.trusted_verifier[0]','values':{'environment':[{'variables':{
                'DATARISE_PROCESSING_DEPLOYMENT_APPROVED':'false','DATARISE_PROCESSING_READERS':'false'}}]}}]}},
        'resource_changes':[]}


def update(plan,address='aws_iam_role_policy.worker_runtime[0]'):
    row={'address':address,'type':address.split('.')[0],'mode':'managed','change':{
        'actions':['update'],'before':{'id':'synthetic-id','role':'synthetic-worker','policy':'before'},
        'after':{'id':'synthetic-id','role':'synthetic-worker','policy':'reviewed'},'after_unknown':{}}}
    plan['resource_changes'].append(row)
    return row


def approval(row):return {'approved_updates':{row['address']:{'after_sha256':checks.canonical_hash(row['change']['after'])}}}


def test_noop_is_not_permission_to_deploy(plan,baseline):
    result=checks.review_plan(plan,{'approved_updates':{}},baseline)
    assert result['offline_checks_passed'] and result['deployment_approved'] is False


def test_update_requires_exact_reviewed_hash(plan,baseline):
    row=update(plan)
    assert not checks.review_plan(plan,{'approved_updates':{}},baseline)['offline_checks_passed']
    policy=approval(row)
    assert checks.review_plan(plan,policy,baseline)['offline_checks_passed']
    row['change']['after']['policy']='unreviewed'
    assert not checks.review_plan(plan,policy,baseline)['offline_checks_passed']


@pytest.mark.parametrize('actions',[['create'],['delete'],['delete','create'],['create','delete'],['forget']])
def test_destructive_or_new_resources_never_bypassed_by_allowlist(plan,baseline,actions):
    row=update(plan);row['change']['actions']=actions
    assert not checks.review_plan(plan,approval(row),baseline)['offline_checks_passed']


@pytest.mark.parametrize('fault',['unknown','sensitive-before','sensitive-after','replace-path','role-change','wrong-type','import','move'])
def test_unsafe_update_metadata_is_blocked(plan,baseline,fault):
    row=update(plan);c=row['change']
    if fault=='unknown':c['after_unknown']={'policy':True}
    if fault=='sensitive-before':c['before_sensitive']={'policy':True}
    if fault=='sensitive-after':c['after_sensitive']={'policy':True}
    if fault=='replace-path':c['replace_paths']=[['policy']]
    if fault=='role-change':c['after']['role']='different'
    if fault=='wrong-type':row['type']='aws_db_instance'
    if fault=='import':c['importing']={'id':'synthetic'}
    if fault=='move':row['previous_address']='aws_iam_role_policy.old[0]'
    assert not checks.review_plan(plan,approval(row),baseline)['offline_checks_passed']


@pytest.mark.parametrize('fault',['incomplete','errored','drift','deferred','region','account','instance','worker-image','outputs','enabled-flags'])
def test_baseline_or_incomplete_plan_is_blocked(plan,baseline,fault):
    if fault=='incomplete':plan['complete']=False
    if fault=='errored':plan['errored']=True
    if fault=='drift':plan['resource_drift']=[{}]
    if fault=='deferred':plan['deferred_changes']=[{}]
    if fault=='region':plan['variables']['aws_region']['value']='different'
    if fault=='account':plan['planned_values']['root_module']['resources'][0]['values']['account_id']='different'
    if fault=='instance':plan['planned_values']['root_module']['resources'][1]['values']['id']='different'
    if fault=='worker-image':plan['variables']['worker_image_digest']['value']='different'
    if fault=='outputs':plan['output_changes']={'unexpected':{'actions':['create']}}
    if fault=='enabled-flags':plan['planned_values']['root_module']['resources'][2]['values']['environment'][0]['variables']['DATARISE_PROCESSING_READERS']='true'
    assert not checks.review_plan(plan,{'approved_updates':{}},baseline)['offline_checks_passed']


def test_unexpected_resource_values_not_in_report(plan,baseline):
    row=update(plan,'aws_secretsmanager_secret_version.unexpected')
    row['change']['after']={'secret_string':'SYNTHETIC_DO_NOT_PRINT'}
    result=checks.review_plan(plan,{'approved_updates':{}},baseline)
    assert not result['offline_checks_passed']
    assert 'SYNTHETIC_DO_NOT_PRINT' not in json.dumps(result)


def host(baseline):
    return {k:baseline[k] for k in ('account','region','instance_id')}|{
        'observed_at':'2026-11-04T12:00:00Z','status_checks_healthy':True,
        'management_path':'organization-approved-serial-console','management_approved':True,
        'graceful_stop_verified':True,'worker_service_active':False,'worker_service_enabled':False,
        'worker_image_digest':baseline['worker_digest']}


def test_host_review_is_not_authenticated_live_evidence(baseline):
    now=datetime(2026,11,4,12,1,tzinfo=timezone.utc)
    result=checks.review_host(host(baseline),baseline,now)
    assert result['offline_checks_passed'] and not result['evidence_authenticated'] and not result['deployment_approved']


@pytest.mark.parametrize('field,value',[
    ('management_path','none'),('management_approved',False),('graceful_stop_verified',False),
    ('worker_service_active',True),('worker_service_enabled',True),('status_checks_healthy',False),
    ('observed_at','2026-11-04T11:00:00Z'),('observed_at','2026-11-04T13:00:00Z'),
    ('instance_id','different'),('worker_image_digest','unknown')])
def test_host_health_alone_is_insufficient(baseline,field,value):
    evidence=host(baseline);evidence[field]=value
    assert not checks.review_host(evidence,baseline,datetime(2026,11,4,12,1,tzinfo=timezone.utc))['offline_checks_passed']


def test_remote_command_fields_are_not_an_operations_interface(baseline):
    evidence=host(baseline);evidence['command']='synthetic-do-not-execute'
    with pytest.raises(ValueError):checks.review_host(evidence,baseline)


def test_duplicate_json_and_cli_errors_do_not_echo_evidence(tmp_path,monkeypatch,capsys):
    path=tmp_path/'evidence.json';path.write_text('{"password":"SYNTHETIC_DO_NOT_PRINT","password":"duplicate"}')
    with pytest.raises(ValueError):checks.read_json(path)
    monkeypatch.setattr('sys.argv',['checks','host',str(path)])
    assert checks.main()==2
    assert 'SYNTHETIC_DO_NOT_PRINT' not in capsys.readouterr().out


def test_real_build_inputs_are_locked_and_context_is_allowlisted():
    result=checks.review_context(Path.cwd())
    assert result['locked_requirements']>30 and not result['deployment_approved']


def test_changed_lock_or_project_fails_before_install(tmp_path):
    project=tmp_path/'pyproject.toml';lock=tmp_path/'uv.lock';req=tmp_path/'requirements.lock'
    for source,target in [(Path('pyproject.toml'),project),(Path('uv.lock'),lock),(Path('deployment/docker/requirements.lock'),req)]:target.write_text(source.read_text())
    assert verify_lock(project,lock,req)>30
    project.write_text(project.read_text()+'\n# changed input\n')
    with pytest.raises(ValueError):verify_lock(project,lock,req)


def test_no_guessed_build_identity_or_unpinned_packages():
    path=Path('deployment/docker/requirements.lock')
    with pytest.raises(ValueError):check_identity('python:3.13-slim','a'*40,sha(path.read_text()),path)
    with pytest.raises(ValueError):check_identity('synthetic@sha256:'+'0'*64,'uncommitted',sha(path.read_text()),path)
    for name in ('worker','verifier'):
        text=Path('deployment/docker/Dockerfile.'+name).read_text()
        assert '--require-hashes' in text and '--no-deps' in text and '--only-binary=:all:' in text
        assert 'DATARISE_DISPATCH_APPROVED=false' in text
        assert 'FROM ${' in text



def test_sensitive_data_source_reads_are_not_preapproved(plan,baseline):
    plan['resource_changes'].append({'address':'data.aws_secretsmanager_secret_version.unexpected',
        'type':'aws_secretsmanager_secret_version','mode':'data','change':{'actions':['read'],'after':{'secret_string':'SYNTHETIC_DO_NOT_PRINT'}}})
    result=checks.review_plan(plan,{'approved_updates':{}},baseline)
    assert not result['offline_checks_passed'] and 'SYNTHETIC_DO_NOT_PRINT' not in json.dumps(result)


def test_invalid_actions_do_not_become_reported_payloads(plan,baseline):
    row=update(plan);row['change']['actions']={'SYNTHETIC_DO_NOT_PRINT':True}
    with pytest.raises(ValueError):checks.review_plan(plan,approval(row),baseline)



def test_unverified_host_example_is_explicit_no_go(baseline):
    evidence=json.loads(Path('deployment/operations/host-evidence.example.json').read_text())
    result=checks.review_host(evidence,baseline)
    assert not result['offline_checks_passed'] and not result['deployment_approved']
    assert 'No organization-approved management path evidenced' in result['reasons']
