"""Opt-in I/O/publication seams shared by existing processors and services.

No context means the historical synchronous behavior. A context never changes
transformation algorithms; it supplies immutable I/O and fenced transactions.
"""
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar

from app.db.database import repository_transaction

_active = ContextVar('durable_stage_io', default=None)


def current():
    return _active.get()


@contextmanager
def using(value):
    token = _active.set(value)
    try:
        yield value
    finally:
        _active.reset(token)


class LazyClient:
    def __init__(self, factory):
        self.factory, self.value = factory, None
    def __getattr__(self, name):
        if self.value is None:
            self.value = self.factory()
        return getattr(self.value, name)


def client(default=None):
    if current() is not None:
        return current()
    if default is not None:
        return default
    from app.storage.s3_service import get_s3_client
    return get_s3_client()


def result(value):
    return current().translate(value) if current() is not None else value


def publication(*, bronze=False):
    if current() is not None:
        return current().publication()
    return nullcontext() if bronze else repository_transaction()


def failure(*, bronze=False):
    if current() is not None:
        return current().failure()
    return nullcontext() if bronze else repository_transaction()


def attempt_transaction():
    return current().failure() if current() is not None else nullcontext()


def attempt_created(attempt_id):
    if current() is not None:
        current().attempt_created(attempt_id)


def require_legacy_source(file_id):
    """Keep every historical service entry point off opted-in immutable sources."""
    import os
    if current() is not None or os.getenv('DATARISE_PROCESSING_READERS') != 'true':
        return
    from app.db.database import get_connection
    with get_connection() as conn:
        if conn.execute('SELECT task20_file_managed(%s)', (file_id,)).fetchone()[0]:
            raise RuntimeError('Durable source requires an asynchronous stage job')
