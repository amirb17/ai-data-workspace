"""Serial container-worker entry point with injected transport; no AWS SDK."""
import logging
import signal
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Event
from time import monotonic
from typing import Protocol

from app.processing_worker import execution
from app.processing_worker.boundary import ExecutionBoundary
from app.processing_worker.messages import parse_job_message

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class QueueMessage:
    message_id: str
    body: str = field(repr=False)
    receipt_handle: str = field(repr=False)

    def __post_init__(self):
        if not all(isinstance(v, str) and v.strip() for v in (self.message_id, self.receipt_handle)):
            raise ValueError('A message identifier and delivery receipt are required')


class QueueTransport(Protocol):
    """Future SQS adapter: bounded receive of at most one message, never prefetch.

    retry must NOT delete/ack; it may defer visibility. close releases only
    local transport resources. Transport errors propagate for supervised restart.
    """
    def receive_one(self, stop: Event) -> QueueMessage | None: ...
    def acknowledge(self, message: QueueMessage) -> None: ...
    def retry(self, message: QueueMessage) -> None: ...
    def close(self) -> None: ...


class SerialWorker:
    def __init__(self, transport: QueueTransport, *, stop: Event | None = None,
                 boundary_executor: Callable[[ExecutionBoundary], None] | None = None,
                 job_budget_ms: int = 3600000, clock: Callable[[], float] = monotonic):
        if type(job_budget_ms) is not int or job_budget_ms <= 5000:
            raise ValueError('Job budget must exceed the completion reserve')
        self.transport = transport
        self.stop = stop if stop is not None else Event()
        self.boundary_executor = boundary_executor
        self.job_budget_ms = job_budget_ms
        self.clock = clock

    def run(self) -> None:
        """Run synchronously until stopped; always close, never ACK unfinished work."""
        try:
            while not self.stop.is_set():
                message = self.transport.receive_one(self.stop)
                if message is None:
                    self.stop.wait(1)  # Interruptible idle backoff, not stage synchronization.
                    continue
                if not isinstance(message, QueueMessage):
                    raise TypeError('Transport must return a QueueMessage')
                if self.stop.is_set():
                    self.transport.retry(message)
                    break
                deadline = self.clock()+self.job_budget_ms/1000
                def remaining_time_ms():
                    if self.stop.is_set():
                        return 0
                    return max(0, int((deadline-self.clock())*1000))
                try:
                    job_id = parse_job_message(message.body)
                    renew = getattr(self.transport, 'renew', None)
                    execution.execute_job(job_id, boundary_executor=self.boundary_executor,
                                          remaining_time_ms=remaining_time_ms,
                                          lease_observer=(lambda lease: renew(message, lease)) if renew else None)
                except Exception:
                    # Do not log message bodies, receipts or raw processing/driver errors.
                    logger.warning('Worker delivery deferred')
                    self.transport.retry(message)
                else:
                    # Completion already committed. ACK failure must not rerun execution here.
                    self.transport.acknowledge(message)
        finally:
            self.transport.close()


def main(transport: QueueTransport | None = None, *, boundary_executor=None,
         stop: Event | None = None) -> int:
    """Inject a transport in a reviewed bootstrap; CLI defaults to disabled.

    Signal handlers only request cooperative shutdown. They are restored on exit.
    Call from the main thread when installing process signal handlers.
    """
    if transport is None:
        logger.error('Worker disabled: no queue transport configured')
        return 2
    stop = stop if stop is not None else Event()
    previous = {}
    running = False
    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous[signum] = signal.signal(signum, lambda *_: stop.set())
        running = True
        SerialWorker(transport, stop=stop, boundary_executor=boundary_executor).run()
    finally:
        try:
            if not running:
                transport.close()
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)
    return 0
