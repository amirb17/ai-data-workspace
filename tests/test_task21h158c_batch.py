"""No SDK/network: trusted-runtime validation and default-disabled protocol."""
from dataclasses import replace
from uuid import uuid4
import pytest
from app.processing_worker.batch_scheduling import (
    Runtime, Submission, Observation, DispatchOutcome, dispatch_once, reconcile_once,
    DefinitelyNotSubmitted,
)


def runtime(stage='BRONZE'):
    return Runtime(stage, 'synthetic-stage:1', 'synthetic-queue', 'sha256:'+'a'*64, 'synthetic-task', 'synthetic-execution')


class FakeBatch:
    def __init__(self):
        self.calls = []
        self.jobs = []
        self.behavior = None

    def submit(self, intent):
        self.calls.append(intent)
        if self.behavior:
            return self.behavior(intent)
        observation = Observation(intent.submission_id, intent.fingerprint, str(uuid4()), 'ACTIVE')
        self.jobs.append(observation)
        return observation

    def lookup(self, intent):
        return [j for j in self.jobs if j.submission_id == intent.submission_id]


@pytest.mark.parametrize('field,value', [
    ('stage','CONTINUE'), ('definition','latest'), ('definition','stage:0'),
    ('queue','unsafe queue'), ('image_digest','latest'), ('task_role','$(unsafe)'),
    ('execution_role',''),
])
def test_untrusted_runtime_overrides_rejected(field,value):
    with pytest.raises(ValueError):replace(runtime(),**{field:value})


def test_runtime_fingerprint_pins_every_field():
    original=runtime()
    assert original.fingerprint==runtime().fingerprint
    for field,value in [('stage','SILVER'),('definition','synthetic-stage:2'),('queue','other'),
                        ('image_digest','sha256:'+'b'*64),('task_role','other'),('execution_role','other')]:
        assert replace(original,**{field:value}).fingerprint!=original.fingerprint


def test_disabled_before_any_repository_or_provider_access():
    assert dispatch_once(None,None)==DispatchOutcome.DISABLED
    assert reconcile_once(None,None,'invalid')=='DISABLED'
    assert dispatch_once(None,None,approval=lambda:False)==DispatchOutcome.DISABLED


@pytest.mark.parametrize('failure', ['timeout','definite','wrong_correlation','crash'])
def test_submission_failure_semantics(failure):
    intent=Submission(uuid4(),uuid4(),uuid4(),runtime(),runtime().fingerprint)
    class Repo:
        responses=[]
        def claim(self):return intent
        def response(self,*args):self.responses.append(args);return True
    repo=Repo();fake=FakeBatch()
    def submit(i):
        if failure=='timeout':raise TimeoutError('PRIVATE')
        if failure=='definite':raise DefinitelyNotSubmitted('PRIVATE')
        if failure=='crash':raise KeyboardInterrupt()
        return Observation(uuid4(),i.fingerprint,str(uuid4()),'ACTIVE')
    fake.behavior=submit
    if failure=='crash':
        with pytest.raises(KeyboardInterrupt):dispatch_once(repo,fake,approval=lambda:True)
        assert not repo.responses
    else:
        assert dispatch_once(repo,fake,approval=lambda:True)==(DispatchOutcome.NOT_SUBMITTED if failure=='definite' else DispatchOutcome.UNCERTAIN)
        assert repo.responses[0][2]==('RETIRED' if failure=='definite' else 'UNCERTAIN')


@pytest.mark.parametrize('state',['COMPLETED','TERMINAL','RETIRED','RETRY_READY','BUDGET_EXHAUSTED'])
def test_sql_authority_checked_before_provider(state):
    class Repo:
        def get(self,_):return Submission(uuid4(),uuid4(),uuid4(),runtime(),runtime().fingerprint)
        def reconcile(self,*_):return state
    assert reconcile_once(Repo(),None,uuid4(),approval=lambda:True)==state
