"""One trusted job, spawn-isolated execution and separately bounded lease I/O.

No default composition, cloud initialization, publication, polling worker or
credential/lease command-line interface. Internal Pipes are trusted pickle IPC.
"""
import math
import multiprocessing as mp
import os
import pickle
import signal
import time
from contextvars import Context
from dataclasses import dataclass
from threading import Event

from app.processing_worker.one_shot import Invocation, Outcome, Result, Stage
from app.processing_worker.process_guard import OwnedProcesses, child_parent_guard, reap, resident_bytes


@dataclass(frozen=True, slots=True)
class Settings:
    budget_seconds: float = 90
    lease_seconds: int = 120
    heartbeat_seconds: float = 30
    database_response_seconds: float = 5
    grace_seconds: float = 2
    kill_seconds: float = 2
    poll_seconds: float = .02

    def __post_init__(self):
        for name in ('budget_seconds','heartbeat_seconds','database_response_seconds','grace_seconds','kill_seconds','poll_seconds'):
            value=getattr(self,name)
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:
                raise ValueError('Finite positive supervisor bounds required')
        if (not 6<=self.budget_seconds<=900 or type(self.lease_seconds) is not int or not 1<=self.lease_seconds<=120
            or not .1<=self.heartbeat_seconds<self.lease_seconds/2 or self.database_response_seconds>=self.lease_seconds
            or self.grace_seconds>5 or self.kill_seconds>5 or self.poll_seconds>.2):
            raise ValueError('Unsafe supervisor timing relationship')


@dataclass(frozen=True, slots=True)
class SupervisedResult:
    result: Result
    renewals: int = 0
    children_reaped: bool = True
    fenced: bool = False

    @property
    def exit_code(self):return self.result.exit_code

    def as_dict(self):
        return dict(**self.result.as_dict(),renewals=self.renewals,children_reaped=self.children_reaped,fenced=self.fenced)


class LeaseRepository:
    """Use in an injected context manager with a bounded connection factory.

    Existing transactions/savepoints perform the CAS. Extra SQL timeouts apply
    only to this owned heartbeat transaction, never a processing session.
    """
    def claim(self,job_id,seconds):
        from app.db import processing_job_repository as jobs
        return jobs.claim_job_result(job_id,lease_seconds=seconds)

    def renew(self,lease,seconds):
        from app.db import database,processing_job_repository as jobs
        with database.repository_transaction() as conn:
            conn.execute("SET LOCAL statement_timeout='2s'")
            conn.execute("SET LOCAL lock_timeout='1s'")
            if jobs.get_execution_scope(lease.job_id,lease.lease_token) is None:return None
            # Once the execution contract exists, a rules/source/head revocation
            # must also stop independent renewal, not only eventual publication.
            pinned=conn.execute('SELECT input_contract FROM processing_job_inputs WHERE job_id=%s',(lease.job_id,)).fetchone()
            if pinned:
                from psycopg.types.json import Jsonb
                conn.execute('SELECT task20_require_input(%s,%s,%s)',(lease.job_id,lease.lease_token,Jsonb(pinned[0])))
            renewed=jobs.renew_job_lease(lease.job_id,lease.lease_token,lease_seconds=seconds)
            if renewed is None or jobs.get_execution_scope(lease.job_id,lease.lease_token) is None:return None
            return renewed

    def observe(self,job_id):
        from app.db import database
        with database.get_connection() as conn:
            row=conn.execute('SELECT status FROM processing_jobs WHERE job_id=%s',(job_id,)).fetchone()
            return row[0] if row else None

    def fence(self,lease):
        from app.db import processing_job_repository as jobs
        return jobs.finish_job(lease.job_id,lease.lease_token,failure_code='WORKER_INTERRUPTED',retry=True,retry_delay_seconds=30) is not None


def _quiet():
    # Exceptions/native/library prints cannot leak IPC capabilities to logs.
    with open(os.devnull,'w') as sink:
        os.dup2(sink.fileno(),1);os.dup2(sink.fileno(),2)


def _receive(pipe):
    # Spawn/configuration already uses trusted Python pickle. Bound the private
    # control frame as well; this protocol is never a socket/public endpoint.
    return pickle.loads(pipe.recv_bytes(8192))


def _agent(pipe,factory,configuration,expected_parent):
    _quiet();child_parent_guard(expected_parent)
    try:
        if _receive(pipe)!='START':return
        with factory(configuration) as repo:
            while True:
                request=_receive(pipe);serial,kind,*values=request
                try:
                    if kind=='CLAIM':value=repo.claim(values[0],values[1])
                    elif kind=='RENEW':value=repo.renew(values[0],values[1])
                    elif kind=='OBSERVE':value=repo.observe(values[0])
                    elif kind=='FENCE':value=repo.fence(values[0])
                    else:return
                    pipe.send((serial,'OK',value))
                except Exception:pipe.send((serial,'ERROR',None))
    except (EOFError,BrokenPipeError):pass
    finally:pipe.close()


def _child(pipe,factory,configuration,expected_parent,stop,deadline,scratch_directory):
    _quiet();child_parent_guard(expected_parent)
    try:
        lease=_receive(pipe)
        import tempfile
        for name in ('TMP','TEMP','TMPDIR'):os.environ[name]=str(scratch_directory)
        tempfile.tempdir=None
        from app.processing_worker.execution import execute_claimed_job
        stage=None
        def remaining():return 0 if stop.is_set() else max(0,int((deadline-time.monotonic())*1000))
        with factory(configuration) as executor:
            def controlled(boundary):
                nonlocal stage
                stage=Stage(boundary.checkpoint().operation)
                return executor(boundary)
            execute_claimed_job(lease,boundary_executor=controlled,remaining_time_ms=remaining)
        pipe.send((Outcome.SUCCEEDED.value,stage.value))
    except Exception:
        try:pipe.send((Outcome.RETRY_REQUIRED.value,None))
        except (EOFError,BrokenPipeError):pass
    finally:pipe.close()


def supervise(job_id,submission_id=None,*,agent_factory=None,child_factory=None,configuration=None,
              approval=None,settings=None,stop=None,scratch_directory=None,memory_bytes=512*1024*1024):
    """Trusted backend composition. Child factory owns all its resources.

    Failures return safe results; the parent never calls a publication operation
    or records success. Callbacks/configuration must be spawn-picklable trusted
    server objects, never message-selected imports or an arbitrary command.
    """
    try:invocation=Invocation(job_id,submission_id)
    except (TypeError,ValueError,AttributeError):return SupervisedResult(Result(Outcome.INVALID_INVOCATION))
    def disabled():return SupervisedResult(Result(Outcome.DISABLED,invocation.job_id,invocation.submission_id))
    if not callable(agent_factory) or not callable(child_factory) or not callable(approval):return disabled()
    try:
        if approval() is not True:return disabled()
        cfg=Settings() if settings is None else settings
        if not isinstance(cfg,Settings) or (stop is not None and not isinstance(stop,Event)) or type(memory_bytes) is not int or not 0<memory_bytes<=512*1024*1024:return disabled()
        from app.processing_worker.workload_limits import scratch_usage
        if scratch_directory is None:return disabled()
        scratch_usage(scratch_directory)
    except Exception:return disabled()
    return Context().run(_supervise,invocation,agent_factory,child_factory,configuration,cfg,stop or Event(),scratch_directory,memory_bytes)


class _Observed(Exception):
    def __init__(self,outcome,retry_at):self.outcome,self.retry_at=outcome,retry_at


def _supervise(invocation,agent_factory,child_factory,configuration,cfg,stop,scratch_directory,memory_bytes):
    ctx=mp.get_context('spawn');halt=ctx.Event();agent=child=None;ap=cp=ac=cc=None;guard=None
    lease=None;renewals=0;fenced=False;reaped=True;outcome=Outcome.RETRY_REQUIRED;stage=None;retry_at=None;serial=0
    start=time.monotonic();deadline=start+cfg.budget_seconds
    def stopping():return stop.is_set() or time.monotonic()>=deadline
    def request(message,*,allow_stopped=False):
        nonlocal serial
        serial+=1;wanted=serial
        began=time.monotonic();ap.send((wanted,*message))
        while time.monotonic()-began<cfg.database_response_seconds:
            if (not allow_stopped and stopping()) or not agent.is_alive():return None
            if ap.poll(cfg.poll_seconds):
                answer=_receive(ap)
                if not isinstance(answer,tuple) or len(answer)!=3:return None
                if answer[0]!=wanted:continue
                return answer[2] if answer[1]=='OK' else None
            if child is not None and not child.is_alive() and message[0]=='RENEW':return None
        return None
    try:
        if stopping():outcome=Outcome.CANCELLED;return SupervisedResult(Result(outcome,invocation.job_id,invocation.submission_id))
        guard=OwnedProcesses();ap,ac=ctx.Pipe()
        agent=ctx.Process(target=_agent,args=(ac,agent_factory,configuration,os.getpid()))
        agent.start();ac.close();guard.add(agent);ap.send('START')
        sent=time.monotonic();claimed=request(('CLAIM',invocation.job_id,cfg.lease_seconds))
        from app.db.processing_job_repository import ClaimResult,ClaimOutcome
        if not isinstance(claimed,ClaimResult):raise ValueError('Claim unavailable')
        if claimed.outcome!=ClaimOutcome.CLAIMED:
            raise _Observed(Outcome(claimed.outcome.value),claimed.retry_at)
        lease=claimed.lease
        if lease is None or lease.job_id!=invocation.job_id:raise ValueError('Invalid committed claim')
        confirmed_until=sent+cfg.lease_seconds;next_renew=time.monotonic()
        cp,cc=ctx.Pipe();child=ctx.Process(target=_child,args=(cc,child_factory,configuration,os.getpid(),halt,deadline,scratch_directory))
        child.start();cc.close();guard.add(child);cp.send(lease)
        while True:
            if stop.is_set():outcome=Outcome.CANCELLED;break
            now=time.monotonic()
            if now>=deadline:outcome=Outcome.DEADLINE_EXCEEDED;break
            if not child.is_alive():
                if cp.poll() and child.exitcode==0:
                    message=_receive(cp)
                    if message[0]==Outcome.SUCCEEDED.value and request(('OBSERVE',invocation.job_id))=='SUCCEEDED':
                        outcome=Outcome.SUCCEEDED;stage=Stage(message[1])
                break
            if now>=confirmed_until:outcome=Outcome.OWNERSHIP_UNAVAILABLE;break
            if resident_bytes(child)>memory_bytes:raise RuntimeError('Observed memory budget exceeded')
            if scratch_directory is not None:
                from app.processing_worker.workload_limits import scratch_usage
                scratch_usage(scratch_directory)
            if now>=next_renew:
                sent=now;renewed=request(('RENEW',lease,cfg.lease_seconds))
                if renewed is None:
                    if not child.is_alive():continue
                    # Successful fenced finish clears the lease before the
                    # child exits. That is completion, not permission to renew
                    # or evidence of failure. Still require its clean result.
                    if request(('OBSERVE',invocation.job_id))=='SUCCEEDED':
                        child.join(cfg.grace_seconds)
                        if not child.is_alive():continue
                    outcome=Outcome.OWNERSHIP_UNAVAILABLE;break
                if renewed.job_id!=lease.job_id or renewed.lease_token!=lease.lease_token:raise ValueError('Invalid renewal')
                lease=renewed;renewals+=1;confirmed_until=sent+cfg.lease_seconds
                next_renew=time.monotonic()+cfg.heartbeat_seconds
            time.sleep(cfg.poll_seconds)
    except _Observed as observed:outcome,retry_at=observed.outcome,observed.retry_at
    except (Exception,KeyboardInterrupt):outcome=Outcome.RETRY_REQUIRED
    finally:
        halt.set()
        # No new renew request after observed child death/stop. A previously
        # started SQL renewal can race physical death; fencing is best effort.
        if lease is not None and outcome!=Outcome.SUCCEEDED and agent is not None and agent.is_alive():
            try:fenced=request(('FENCE',lease),allow_stopped=True) is True
            except Exception:pass
        try:reaped=reap(child,cfg.grace_seconds,cfg.kill_seconds)
        except Exception:reaped=False
        if ap is not None:ap.close()
        try:reaped=reap(agent,cfg.grace_seconds,cfg.kill_seconds) and reaped
        except Exception:reaped=False
        if cp is not None:cp.close()
        for endpoint in (ac,cc):
            if endpoint is not None:endpoint.close()
        if guard is not None:guard.close()
        for process in (child,agent):
            if process is not None and not process.is_alive():process.close()
    if not reaped:outcome=Outcome.RETRY_REQUIRED
    return SupervisedResult(Result(outcome,invocation.job_id,invocation.submission_id,stage,retry_at),renewals,reaped,fenced)


def with_signals(run,stop):
    """Main-thread host wrapper preserves the actual result/exit code."""
    previous={}
    try:
        for number in (signal.SIGTERM,signal.SIGINT):previous[number]=signal.signal(number,lambda *_:stop.set())
        return run()
    finally:
        for number,handler in previous.items():signal.signal(number,handler)


if __name__=='__main__':
    # No deployable factory, environment-selected imports or cloud bootstrap.
    raise SystemExit(78)
