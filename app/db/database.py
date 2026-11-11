import os
from contextlib import contextmanager
from contextvars import ContextVar
import psycopg
from threading import Lock
from weakref import WeakSet
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
_transaction_connection = ContextVar("transaction_connection", default=None)
_connection_boundary = ContextVar('connection_boundary',default=None)
_boundary_connections=WeakSet()
_boundary_lock=Lock()


def require_connection_boundary(duty):
    active=_connection_boundary.get()
    if active is not None and active[0]!=duty:
        raise RuntimeError('Cross-duty database use is forbidden')


@contextmanager
def connection_boundary(duty, factory):
    """Trusted process factory only; labels are not database privileges.

    No DSN/default role mapping is invented. An active boundary cannot switch
    factories or borrow across duties, even when the outer transaction is idle.
    """
    allowed={'query_control','provider_submission','recovery_attestation','accounting',
        'result_authorization','publication','retention_inspection'}
    if duty not in allowed or not callable(factory):raise ValueError('Explicit warehouse process factory required')
    if _transaction_connection.get() is not None or _connection_boundary.get() is not None:
        raise RuntimeError('Connection boundary cannot nest or switch an active transaction')
    token=_connection_boundary.set((duty,factory))
    try:yield
    finally:_connection_boundary.reset(token)


def _open_connection():
    boundary=_connection_boundary.get()
    if boundary is None:return psycopg.connect(DATABASE_URL)
    connection=boundary[1]()
    if isinstance(connection,_BorrowedConnection) or connection.closed:
        raise RuntimeError('Fresh owned boundary connection required')
    from psycopg.pq import TransactionStatus
    if connection.info.transaction_status!=TransactionStatus.IDLE:
        raise RuntimeError('Boundary factory returned an active transaction')
    with _boundary_lock:
        if connection in _boundary_connections:raise RuntimeError('Boundary connection reuse is forbidden')
        _boundary_connections.add(connection)
    return connection


class _BorrowedConnection:
    """Repository calls participate in the outer transaction without committing it."""
    def __init__(self, connection):
        self.connection = connection

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def commit(self):
        pass

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@contextmanager
def repository_transaction():
    """Atomically reuse existing repository functions in a service operation."""
    if _transaction_connection.get() is not None:
        raise RuntimeError("Nested repository transactions are not supported")
    with _open_connection() as connection:
        token = _transaction_connection.set(_BorrowedConnection(connection))
        try:
            yield connection
        finally:
            _transaction_connection.reset(token)


def get_connection():
    if _transaction_connection.get() is not None:
        return _transaction_connection.get()
    return _open_connection()


@contextmanager
def repository_savepoint():
    """Explicit composable operation; repository_transaction still rejects nesting.

    Borrow an existing outer transaction and roll back only this operation on
    failure, or own/commit a connection when called independently.
    """
    from psycopg.pq import TransactionStatus
    with get_connection() as connection:
        token = None
        if _transaction_connection.get() is None:
            token = _transaction_connection.set(_BorrowedConnection(connection))
        try:
            if connection.info.transaction_status == TransactionStatus.IDLE:
                connection.execute('BEGIN')
            with connection.transaction():
                yield connection
        finally:
            if token is not None:
                _transaction_connection.reset(token)
