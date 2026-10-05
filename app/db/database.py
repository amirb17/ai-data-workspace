import os
from contextlib import contextmanager
from contextvars import ContextVar
import psycopg
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
_transaction_connection = ContextVar("transaction_connection", default=None)


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
    with psycopg.connect(DATABASE_URL) as connection:
        token = _transaction_connection.set(_BorrowedConnection(connection))
        try:
            yield connection
        finally:
            _transaction_connection.reset(token)


def get_connection():
    if _transaction_connection.get() is not None:
        return _transaction_connection.get()
    return psycopg.connect(DATABASE_URL)
