"""Real outbox/jobs/stages/publication SQL, injected SQS and versioned storage."""
import json
from threading import Event

import pytest

from app.db import processing_job_repository as jobs
from app.processing_worker import execution
from app.processing_worker.dispatch import admit, dispatch_one
from app.processing_worker.sqs_transport import SqsTransport
from app.processing_worker.worker import SerialWorker
from tests.test_rule_approval_postgres import pg
from tests.test_processing_jobs_postgres import source
from tests.test_task20_processing_postgres import pipeline, new_delivery
from tests.task21h153_support import CSV


class Broker:
    """Single offline delivery; no inference about real visibility/DLQ timing."""
    def __init__(self, stop):
        self.stop, self.messages, self.events = stop, [], []
        self.lose_send = self.lose_ack = False
        self.before_ack = lambda: None

    def send_message(self, **kwargs):
        self.messages.append(kwargs['MessageBody'])
        self.events.append('send')
        if self.lose_send:
            self.lose_send = False
            raise TimeoutError('Synthetic accepted send with lost response')
        return {'MessageId': 'synthetic'}

    def receive_message(self, **kwargs):
        assert kwargs['MaxNumberOfMessages'] == 1
        return {'Messages': [dict(MessageId='synthetic', Body=self.messages[0], ReceiptHandle='synthetic-receipt')]}

    def change_message_visibility(self, **kwargs):
        self.events.append(('visibility', kwargs['VisibilityTimeout']))
        if kwargs['VisibilityTimeout'] == 30:
            self.stop.set()

    def delete_message(self, **kwargs):
        self.before_ack()
        self.events.append('ack')
        self.stop.set()
        if self.lose_ack:
            self.lose_ack = False
            raise TimeoutError('Synthetic uncertain ACK')

    def close(self):
        self.events.append('close')


@pytest.mark.parametrize('fault', ['none', 'send-response', 'ack-response', 'completion'])
def test_real_dispatch_worker_publication_and_redelivery(pipeline, monkeypatch, fault):
    src, client, run = pipeline
    connect, user, upload = src
    job = admit(user, upload, 'BRONZE', 'transport-lifecycle', isolated_test=True)
    stop = Event()
    broker = Broker(stop)
    def committed_before_ack():
        with connect() as conn:
            assert conn.execute('SELECT status FROM processing_jobs WHERE job_id=%s', (job,)).fetchone()[0] == 'SUCCEEDED'
            assert conn.execute('SELECT count(*) FROM processing_stage_receipts').fetchone()[0] == 1
    broker.before_ack = committed_before_ack
    broker.lose_send = fault == 'send-response'
    broker.lose_ack = fault == 'ack-response'
    if fault == 'send-response':
        with pytest.raises(TimeoutError):
            dispatch_one(broker, 'synthetic', enabled=True, isolated_test=True)
        with connect() as conn:
            assert conn.execute('SELECT delivered_at IS NULL FROM processing_dispatch_outbox WHERE job_id=%s', (job,)).fetchone()[0]
            conn.execute("UPDATE processing_dispatch_outbox SET claim_until=clock_timestamp()-interval '1 second' WHERE job_id=%s", (job,))
    assert dispatch_one(broker, 'synthetic', enabled=True, isolated_test=True)
    assert all(json.loads(body) == {'job_id': str(job)} for body in broker.messages)
    original = jobs.finish_job
    if fault == 'completion':
        monkeypatch.setattr(jobs, 'finish_job', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('Synthetic completion persistence failure')))
    transport = SqsTransport(broker, 'synthetic', enabled=True)
    worker = SerialWorker(transport, stop=stop, boundary_executor=run.adapter)
    if fault == 'ack-response':
        with pytest.raises(TimeoutError):
            worker.run()
    else:
        worker.run()
    with connect() as conn:
        assert conn.execute('SELECT count(*) FROM processing_stage_receipts').fetchone()[0] == 1
        assert conn.execute('SELECT generation FROM processing_stage_heads').fetchone()[0] == 1
        state = conn.execute('SELECT status FROM processing_jobs WHERE job_id=%s', (job,)).fetchone()[0]
        assert state == ('RUNNING' if fault == 'completion' else 'SUCCEEDED')
        if fault == 'completion':
            assert 'ack' not in broker.events
            conn.execute("UPDATE processing_jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s", (job,))
    writes = len([e for e in client.calls if e[0] == 'put'])
    monkeypatch.setattr(jobs, 'finish_job', original)
    # Re-delivery after terminal commit or receipt commit/completion loss must
    # reconcile, never rewrite a physical artifact or add a second receipt.
    stop.clear()
    SerialWorker(SqsTransport(broker, 'synthetic', enabled=True), stop=stop, boundary_executor=run.adapter).run()
    assert broker.events[-2:] == ['ack', 'close']
    assert len([e for e in client.calls if e[0] == 'put']) == writes
    with connect() as conn:
        assert conn.execute('SELECT status FROM processing_jobs WHERE job_id=%s', (job,)).fetchone()[0] == 'SUCCEEDED'
        assert conn.execute('SELECT count(*) FROM processing_stage_receipts').fetchone()[0] == 1


def test_realistic_csv_dq_metadata_and_verified_artifacts(pipeline, monkeypatch):
    import io
    import pandas as pd
    from app.schemas.business_rules import BusinessRuleAnswer
    from app.services import processing_context_service as context, business_rule_service as business
    src, client, run = pipeline
    upload, file_id = new_delivery(pipeline, CSV)
    run('BRONZE', upload=upload)
    answers = [('id', 'NOT_NULL', 'YES'), ('id', 'UNIQUE', 'YES'),
               ('city', 'NOT_NULL', 'YES'), ('amount', 'DATA_TYPE', 'DECIMAL'),
               ('amount', 'NOT_NULL', 'YES'), ('amount', 'ALLOW_NEGATIVE', 'NO'),
               ('ordered_on', 'DATA_TYPE', 'DATE'), ('ordered_on', 'NOT_NULL', 'YES')]
    suggestions = [dict(column_name=c, suggested_rule_type=k, options=[a]) for c, k, a in answers]
    monkeypatch.setattr(business, 'generate_rule_suggestions', lambda _: suggestions)
    with src[0]() as conn:
        association, ws, ds, file_id = conn.execute("""SELECT f.dataset_version_file_id,d.workspace_id,d.dataset_id,p.file_id
            FROM dataset_version_files f JOIN dataset_versions v USING(dataset_version_id)
            JOIN physical_files p ON p.file_id=f.file_id AND p.schema_hash=v.schema_hash
            JOIN datasets d USING(dataset_id) WHERE p.file_id=%s""", (file_id,)).fetchone()
    actor = {'user_id': src[1], 'owner_key': 'dev:test'}
    with context.rule_mutation(association, actor, 0, ws, ds):
        business.submit_business_rule_answers(association, [BusinessRuleAnswer(column_name=c,rule_type=k,answer=a) for c,k,a in answers])
    with context.rule_mutation(association, actor, 1, ws, ds):
        assert business.finalize_business_rules(association)['finalized']
    run('SILVER', upload=upload)
    run('GOLD', upload=upload)
    with src[0]() as conn:
        assert conn.execute('SELECT total_rows,valid_rows,rejected_rows FROM data_quality_runs').fetchone() == (8, 2, 6)
        issues = {(c, r): n for c, r, n in conn.execute('SELECT column_name,rule_type,violation_count FROM data_quality_issues')}
        assert issues == {('id', 'UNIQUE'): 2, ('city', 'NOT_NULL'): 1,
            ('amount', 'NOT_NULL'): 1, ('amount', 'ALLOW_NEGATIVE'): 1,
            ('amount', 'INVALID_NUMERIC'): 1, ('ordered_on', 'INVALID_DATETIME'): 1}
        assert conn.execute('SELECT count(*) FROM processing_stage_receipts').fetchone()[0] == 3
        assert conn.execute('SELECT count(*) FROM processing_artifact_candidates WHERE status=\'VERIFIED\'').fetchone()[0] == 3
        assert conn.execute('SELECT count(*) FROM processing_jobs WHERE status=\'SUCCEEDED\'').fetchone()[0] == 3
        data = conn.execute("SELECT c.artifact_identities->'data'->>'key' FROM processing_stage_heads h JOIN processing_artifact_candidates c USING(candidate_id) WHERE h.stage='SILVER'").fetchone()[0]
        quarantine = conn.execute("SELECT c.artifact_identities->'quarantine'->>'key' FROM processing_stage_heads h JOIN processing_artifact_candidates c USING(candidate_id) WHERE h.stage='SILVER'").fetchone()[0]
        gold = conn.execute("SELECT c.artifact_identities->'base'->>'key' FROM processing_stage_heads h JOIN processing_artifact_candidates c USING(candidate_id) WHERE h.stage='GOLD'").fetchone()[0]
    frame = pd.read_parquet(io.BytesIO(client.objects[data, client.latest[data]]))
    assert frame['id'].tolist() == [2, 6]
    assert set(frame['_source_file_id']) == {file_id}
    assert len(pd.read_parquet(io.BytesIO(client.objects[quarantine, client.latest[quarantine]]))) == 6
    assert pd.read_parquet(io.BytesIO(client.objects[gold, client.latest[gold]]))['id'].tolist() == [2, 6]


def test_concurrent_dispatchers_observe_real_committed_outbox_claim(pipeline):
    from concurrent.futures import ThreadPoolExecutor
    src, _, _ = pipeline
    job = admit(src[1], src[2], 'BRONZE', 'competing-dispatch', isolated_test=True)
    entered, release = Event(), Event()
    broker = Broker(Event())
    original = broker.send_message
    def held_send(**kwargs):
        entered.set()
        if not release.wait(120):
            raise TimeoutError('Test did not release dispatcher')
        return original(**kwargs)
    broker.send_message = held_send
    with ThreadPoolExecutor(max_workers=1) as pool:
        active = pool.submit(dispatch_one, broker, 'synthetic', enabled=True, isolated_test=True)
        try:
            assert entered.wait(120)
            with src[0]() as conn:
                assert conn.execute('SELECT claim_token IS NOT NULL AND claim_until>clock_timestamp() AND delivered_at IS NULL FROM processing_dispatch_outbox WHERE job_id=%s', (job,)).fetchone()[0]
            assert dispatch_one(broker, 'synthetic', enabled=True, isolated_test=True) is False
            assert broker.messages == []
        finally:
            release.set()
        assert active.result(timeout=120)
    assert len(broker.messages) == 1
