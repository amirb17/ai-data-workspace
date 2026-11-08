"""Spawn-importable synthetic process compositions. No cloud client is permitted."""
import hashlib
import io
import json
import os
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from pathlib import Path
from threading import Event
from uuid import uuid4


def deny_cloud():
    import socket,boto3
    from app import config
    def blocked(*a,**k):raise AssertionError('Cloud access forbidden in process test')
    class Offline:
        def client(self,*a,**k):return self
        def __getattr__(self,_):return blocked
    config.get_boto3_session=lambda:Offline()
    boto3.client=blocked;boto3.Session=blocked
    connect=socket.socket.connect
    def local(sock,address):
        if not isinstance(address,tuple) or address[0] not in ('127.0.0.1','::1'):return blocked()
        return connect(sock,address)
    socket.socket.connect=local;socket.socket.connect_ex=blocked


def mark(cfg,name):
    Path(cfg['root'],name).write_text(str(os.getpid()))


class FakeLease:
    def __init__(self,cfg):self.cfg=cfg;self.lease=None
    def claim(self,job,seconds):
        from app.db import processing_job_repository as jobs
        outcome=jobs.ClaimOutcome(self.cfg.get('outcome','CLAIMED'))
        self.lease=jobs.JobLease(job,uuid4(),datetime.now(timezone.utc)+timedelta(seconds=seconds),1)
        return jobs.ClaimResult(outcome,lease=self.lease if outcome==jobs.ClaimOutcome.CLAIMED else None)
    def renew(self,lease,seconds):
        if self.cfg.get('fault')=='completion_race' and Path(self.cfg['root'],'status').exists():return None
        if Path(self.cfg['root'],'ready').exists():
            if self.cfg.get('fault')=='heartbeat_fail':raise ConnectionError('PRIVATE')
            if self.cfg.get('fault')=='heartbeat_hang':Event().wait(60)
        with Path(self.cfg['root'],'renewals').open('a') as file:file.write('renew\n')
        return replace(lease,lease_expires_at=datetime.now(timezone.utc)+timedelta(seconds=seconds))
    def observe(self,job):
        mark(self.cfg,'terminal-observed')
        return Path(self.cfg['root'],'status').read_text() if Path(self.cfg['root'],'status').exists() else None
    def fence(self,lease):mark(self.cfg,'fenced');return True


@contextmanager
def fake_agent(cfg):
    deny_cloud();yield FakeLease(cfg)


@contextmanager
def fake_child(cfg):
    deny_cloud()
    from app.db import processing_job_repository as jobs
    jobs.get_execution_scope=lambda *a,**k:jobs.JobExecutionScope(1,1,1,1,1,'BRONZE')
    def finish(*a,**k):
        if k.get('failure_code'):return {'status':'RETRY_WAIT'}
        if cfg.get('fault')=='completion_fail':raise ConnectionError('PRIVATE')
        Path(cfg['root'],'status').write_text('SUCCEEDED');return {'status':'SUCCEEDED'}
    jobs.finish_job=finish
    def execute(boundary):
        mark(cfg,'ready')
        if cfg.get('fault')=='crash':os._exit(73)
        if cfg.get('fault')=='executor_fail':raise RuntimeError('PRIVATE')
        if cfg.get('fault') in ('hang','heartbeat_fail','heartbeat_hang'):
            Event().wait(60)
        if cfg.get('fault')=='wait_release':
            # Bounded test rendezvous; parent can observe DB renewals while this
            # stands in for a single blocking native call without checkpoints.
            import time
            until=time.monotonic()+15
            while not Path(cfg['root'],'release').exists():
                if time.monotonic()>until:raise TimeoutError('Test release missing')
                Event().wait(.02)
    yield execute
    if cfg.get('fault')=='completion_race':
        import time
        end=time.monotonic()+5
        while not Path(cfg['root'],'terminal-observed').exists():
            if time.monotonic()>end:raise TimeoutError('Completion observation missing')
            Event().wait(.01)


class FileClient:
    """Immutable local object emulator shared across separate spawned processes."""
    def __init__(self,root,fault=None):self.root=Path(root);self.root.mkdir(exist_ok=True);self.fault=fault
    def _latest(self):
        path=self.root/'latest.json';return json.loads(path.read_text()) if path.exists() else {}
    def _path(self,key,version):return self.root/(hashlib.sha256(key.encode()).hexdigest()+'_'+version)
    def seed(self,key,body,version=None):
        from app.storage.versioned_artifacts import ObjectIdentity
        version=version or uuid4().hex
        with self._path(key,version).open('xb') as file:file.write(body)
        latest=self._latest();latest[key]=version
        staging=self.root/'latest.pending';staging.write_text(json.dumps(latest));staging.replace(self.root/'latest.json')
        return ObjectIdentity(key,version,hashlib.sha256(body).hexdigest(),len(body))
    def put_object(self,*,Bucket,Key,Body,IfNoneMatch):
        if IfNoneMatch!='*' or Key in self._latest():raise ValueError('Immutable overwrite rejected')
        identity=self.seed(Key,Body)
        if self.fault=='crash_write':os._exit(73)
        return {'VersionId':identity.version}
    def head_object(self,*,Bucket,Key):
        version=self._latest()[Key]
        return dict(VersionId=version,ContentLength=self._path(Key,version).stat().st_size)
    def get_object(self,*,Bucket,Key,VersionId):
        payload=self._path(Key,VersionId).read_bytes()
        return dict(VersionId=VersionId,ContentLength=len(payload),Body=io.BytesIO(payload))


def configure_pg(cfg):
    deny_cloud()
    import re,psycopg
    from psycopg.conninfo import conninfo_to_dict,make_conninfo
    from app.db import database
    schema=cfg['schema']
    if not re.fullmatch(r'test_phase5a_[0-9a-f]{32}',schema):raise ValueError('Disposable schema required')
    settings=conninfo_to_dict(database.DATABASE_URL)
    if settings.get('host') not in ('localhost','127.0.0.1'):raise ValueError('Loopback database required')
    settings.update(hostaddr='127.0.0.1',connect_timeout='2')
    dsn=make_conninfo(**settings);original=psycopg.connect
    def connect(*a,**k):
        import time
        began=time.perf_counter()
        try:return original(dsn,options=f'-c search_path={schema} -c statement_timeout=2000 -c lock_timeout=1000')
        finally:
            with Path(cfg['root'],'connection-timings.jsonl').open('a') as file:file.write(json.dumps(dict(pid=os.getpid(),seconds=time.perf_counter()-began))+'\n')
    database.psycopg.connect=connect
    from app.services import business_rule_service,rule_reuse_service
    business_rule_service.generate_rule_suggestions=lambda _:[dict(column_name='id',suggested_rule_type='NOT_NULL',options=['YES','NO'])]
    rule_reuse_service.generate_rule_suggestions=business_rule_service.generate_rule_suggestions
    return connect


@contextmanager
def pg_agent(cfg):
    connect=configure_pg(cfg)
    from app.processing_worker.supervision import LeaseRepository
    class Agent(LeaseRepository):
        def renew(self,lease,seconds):
            if Path(cfg['root'],'ready').exists():
                if cfg.get('fault')=='heartbeat_fail':raise ConnectionError('PRIVATE')
                if cfg.get('fault')=='expired':
                    with connect() as conn:conn.execute("UPDATE processing_jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s",(lease.job_id,))
                if cfg.get('fault')=='cancel':
                    from app.db.batch_scheduling_repository import BatchRepository
                    BatchRepository().cancel(lease.job_id,cfg['actor'])
            try:value=super().renew(lease,seconds)
            except Exception as error:
                Path(cfg['root'],'agent-error').write_text(type(error).__name__)
                raise
            with Path(cfg['root'],'renewals').open('a') as file:file.write('renew\n')
            return value
    yield Agent()


@contextmanager
def pg_child(cfg):
    connect=configure_pg(cfg)
    from app.config import S3_BUCKET_NAME
    from app.storage.versioned_artifacts import VersionedArtifacts
    from app.processing_worker.workload_limits import LimitedClient,BoundedExecutor
    from app.processing_worker.integrated_executor import IntegratedExecutor
    from app.processing_worker.verification_service import RegistryVerifier
    client=FileClient(Path(cfg['root'])/'objects',cfg.get('fault'))
    limited=LimitedClient(client)
    verifier=RegistryVerifier(VersionedArtifacts(client,S3_BUCKET_NAME),connect,isolated_test=True)
    def verify(candidate):
        value=verifier.verify(candidate)
        if cfg.get('fault') in ('block','expired','cancel','heartbeat_fail'):
            mark(cfg,'ready');Event().wait(60)
        if cfg.get('fault')=='wait_release':
            import time
            mark(cfg,'ready');end=time.monotonic()+40
            while not Path(cfg['root'],'release').exists():
                if time.monotonic()>end:raise TimeoutError('Controlled verification release missing')
                Event().wait(.02)
        return value
    adapter=IntegratedExecutor(lambda check:VersionedArtifacts(limited,S3_BUCKET_NAME,checkpoint=check),verify,enabled=True,isolated_test=True)
    if cfg.get('fault')=='completion_fail':
        from app.db import processing_job_repository as jobs
        jobs.finish_job=lambda *a,**k:(_ for _ in ()).throw(ConnectionError('PRIVATE'))
    yield BoundedExecutor(adapter,VersionedArtifacts(limited,S3_BUCKET_NAME))


def host(root):
    from threading import Event
    from app.processing_worker.supervision import supervise,Settings,with_signals
    stop=Event()
    result=with_signals(lambda:supervise(uuid4(),agent_factory=fake_agent,child_factory=fake_child,
        configuration=dict(root=root,fault='hang'),approval=lambda:True,
        settings=Settings(budget_seconds=30,lease_seconds=8,heartbeat_seconds=.25,database_response_seconds=2,grace_seconds=.2,kill_seconds=.5),stop=stop,scratch_directory=root),stop)
    Path(root,'result.json').write_text(json.dumps(result.as_dict()))
    raise SystemExit(int(result.exit_code))


def pg_host():
    import sys
    from app.processing_worker.supervision import supervise,Settings
    request=json.loads(sys.stdin.read(4096))
    result=supervise(request['job'],agent_factory=pg_agent,child_factory=pg_child,configuration=request['configuration'],
                     approval=lambda:True,settings=Settings(budget_seconds=90,heartbeat_seconds=1),scratch_directory=request['scratch'])
    Path(request['configuration']['root'],'result.json').write_text(json.dumps(result.as_dict()))
    raise SystemExit(int(result.exit_code))
