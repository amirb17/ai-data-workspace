"""Serial outbox supervision over the existing token-fenced dispatch operation."""
import logging
import os
from threading import Event

logger = logging.getLogger(__name__)


def require_dispatch_approval():
    for flag in ('DATARISE_PROCESSING_DEPLOYMENT_APPROVED', 'DATARISE_PROCESSING_READERS',
                 'DATARISE_DISPATCH_APPROVED'):
        if os.getenv(flag) != 'true':
            raise PermissionError('Dispatcher activation is not approved')


class DispatchSupervisor:
    def __init__(self, dispatch_once, *, close, stop=None, idle_seconds=3,
                 idle_cap_seconds=30, retry_seconds=1, retry_cap_seconds=30, error_limit=5, enabled=False):
        if not enabled:
            raise PermissionError('Dispatcher is disabled')
        for value in (idle_seconds, idle_cap_seconds, retry_seconds, retry_cap_seconds, error_limit):
            if type(value) is not int or not 1 <= value <= 60:
                raise ValueError('Invalid dispatcher safety bound')
        if retry_seconds > retry_cap_seconds or idle_seconds > idle_cap_seconds or not callable(dispatch_once) or not callable(close):
            raise ValueError('Invalid dispatcher dependencies')
        self.dispatch_once, self.close = dispatch_once, close
        self.stop = stop if stop is not None else Event()
        self.idle, self.base, self.cap, self.limit = idle_seconds, retry_seconds, retry_cap_seconds, error_limit
        self.idle_cap = idle_cap_seconds

    def run(self):
        errors = 0
        empty = 0
        try:
            require_dispatch_approval()
            while not self.stop.is_set():
                try:
                    delivered = self.dispatch_once()
                    if type(delivered) is not bool:
                        raise TypeError('Invalid dispatch result')
                except Exception:
                    empty = 0
                    errors += 1
                    logger.warning('Outbox dispatch deferred')
                    if errors >= self.limit:
                        raise RuntimeError('Dispatcher error budget exhausted') from None
                    self.stop.wait(min(self.cap, self.base * 2 ** (errors - 1)))
                else:
                    errors = 0
                    # Initial small-host policy: at most one successful send per
                    # second, with an interruptible wait rather than a busy loop.
                    empty = 0 if delivered else min(empty + 1, 10)
                    self.stop.wait(1 if delivered else min(self.idle_cap, self.idle * 2 ** (empty - 1)))
        finally:
            self.close()


def main(*, session=None, stop=None):
    """Dedicated process only. AWS identity binding requires separate review.

    No ambient EC2 worker credential fallback: sharing an instance profile does
    not establish dispatcher/worker isolation. A reviewed bootstrap must inject
    the separately authorized SDK session, never a queue message or public API.
    """
    require_dispatch_approval()
    if session is None:
        raise PermissionError('Dedicated dispatcher AWS identity is not configured')
    region = os.getenv('AWS_REGION') or os.getenv('AWS_DEFAULT_REGION')
    queue = os.getenv('DATARISE_PROCESSING_QUEUE_URL')
    secret = os.getenv('DATARISE_DISPATCH_DB_SECRET_ARN')
    if not region or not queue or not secret:
        raise ValueError('Dispatcher configuration is incomplete')
    if secret in {os.getenv('DATARISE_WORKER_DB_SECRET_ARN'), os.getenv('DATARISE_VERIFIER_DB_SECRET_ARN')}:
        raise PermissionError('Dispatcher requires a distinct secret reference')
    if any(os.getenv(name) not in (None, region) for name in ('AWS_REGION', 'AWS_DEFAULT_REGION')) or getattr(session, 'region_name', None) != region:
        raise PermissionError('Dedicated dispatcher region binding differs')
    os.environ['PYTHON_DOTENV_DISABLED'] = '1'
    from app.processing_worker.container_entrypoint import initialize_database
    from app.processing_worker.runtime_clients import client_config
    initialize_database(session, purpose='dispatch')
    from app.processing_worker.dispatch import dispatch_one
    from app.processing_worker.process_lifecycle import run_with_signals
    sender = session.client('sqs', config=client_config('sqs'))
    try:
        loop = DispatchSupervisor(lambda: dispatch_one(sender, queue, enabled=True),
                                  close=sender.close, stop=stop, enabled=True)
    except BaseException:
        sender.close()
        raise
    return run_with_signals(loop.run, loop.stop, loop.close)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    # Intentionally no ambient SDK binding in the CLI.
    try:
        raise SystemExit(main())
    except Exception:
        logger.error('Dispatcher startup refused')
        raise SystemExit(2)
