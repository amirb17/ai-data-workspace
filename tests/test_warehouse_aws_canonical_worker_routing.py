"""Test the worker's actual canonical database identity gate (no database needed)."""
import pytest

from app.db.warehouse_canonical_worker_connection import verify_canonical_routing
from app.db.warehouse_restricted_connection import ROLE_BY_DUTY


class Result:
    def __init__(self, value):
        self.value = value

    def fetchone(self):
        return self.value


class Catalog:
    def __init__(self, duty, database, *, public_create=False, shadow=False):
        self.role = ROLE_BY_DUTY[duty]
        self.database = database
        self.public_create = public_create
        self.shadow = shadow
        self.calls = []

    def execute(self, query):
        self.calls.append(query)
        if "current_database()" in query:
            return Result((
                self.role, self.role, self.database, True,
                self.public_create, False,
            ))
        if "FROM pg_class" in query:
            return Result((1,) if self.shadow else None)
        raise AssertionError("Unexpected SQL query")


@pytest.mark.parametrize("duty", [
    "provider_submission", "recovery_attestation", "accounting",
    "result_authorization",
])
@pytest.mark.parametrize("database", ["aiworkspace", "ai_data_workspace"])
def test_reviewed_database_accepted_for_all_restricted_workers(duty, database):
    conn = Catalog(duty, database)
    verify_canonical_routing(conn, duty)
    assert len(conn.calls) == 2


@pytest.mark.parametrize("database", ["postgres", "template1", "aiworkspace_test"])
def test_other_database_names_rejected(database):
    with pytest.raises(RuntimeError, match="CANONICAL_WORKER_ROUTING_UNTRUSTED"):
        verify_canonical_routing(Catalog("accounting", database), "accounting")


def test_public_create_and_shadow_query_tables_remain_forbidden():
    with pytest.raises(RuntimeError, match="CANONICAL_WORKER_ROUTING_UNTRUSTED"):
        verify_canonical_routing(
            Catalog("accounting", "aiworkspace", public_create=True),
            "accounting",
        )
    with pytest.raises(RuntimeError, match="PUBLIC_QUERY_PROTOCOL_SHADOWING"):
        verify_canonical_routing(
            Catalog("accounting", "aiworkspace", shadow=True),
            "accounting",
        )


def test_unknown_duty_rejected_before_any_connection():
    with pytest.raises(RuntimeError, match="CANONICAL_WORKER_DUTY_FORBIDDEN"):
        verify_canonical_routing(Catalog("accounting", "aiworkspace"), "admin")
