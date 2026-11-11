"""Offline tests for real-login warehouse worker acceptance gate."""
import pytest

from tools.warehouse_runtime_role_acceptance import (
    _check_worker, _table_privileges, accept, DUTIES, _PRIVILEGES,
)
from tools.warehouse_worker_acl import READS, WRITES, ROLES


class Row:
    def __init__(self, value): self.value = value
    def fetchone(self): return self.value
    def fetchall(self): return self.value


class Catalog:
    def __init__(self, duty, *, overgrant=None, undergrant=None):
        self.duty=duty
        self.overgrant=overgrant
        self.undergrant=undergrant
    def execute(self, query, params=()):
        if "FROM pg_class c" in query:
            entries=READS[self.duty] | set(WRITES[self.duty])
            entries |= {("public","users"), ("public","workspaces")}
            return Row(sorted(entries))
        if "has_table_privilege" in query:
            assert len(params)==3*len(_PRIVILEGES)
            qualified=params[1]
            assert all(params[i*3+1]==qualified for i in range(len(_PRIVILEGES)))
            schema, table=qualified.split(".", 1)
            allowed=set(WRITES[self.duty].get((schema,table), ()))
            if (schema,table) in READS[self.duty]:
                allowed.add("SELECT")
            if qualified == self.overgrant:
                allowed.add("UPDATE")
            if qualified == self.undergrant:
                allowed.discard("SELECT")
            return Row(tuple(name in allowed for name in _PRIVILEGES))
        if "has_schema_privilege" in query:
            return Row((False,))
        raise AssertionError("Unexpected catalog SQL")


@pytest.mark.parametrize("duty",tuple(ROLES))
def test_exact_worker_catalog_permissions(duty):
    result=_check_worker(Catalog(duty),duty)
    assert result["verified_tables"] == len(READS[duty] | set(WRITES[duty]))


@pytest.mark.parametrize("duty",tuple(ROLES))
def test_unrelated_public_write_is_blocked(duty):
    with pytest.raises(RuntimeError,match="GRANTS_DRIFT"):
        _check_worker(Catalog(duty,overgrant="public.users"), duty)


def test_missing_worker_read_is_blocked():
    with pytest.raises(RuntimeError, match="GRANTS_DRIFT"):
        _check_worker(Catalog("accounting",undergrant="public.query_governance_reservations"), "accounting")


def test_unconfigured_credentials_do_not_authorize_any_duty():
    with pytest.raises(ValueError,match="EXPLICIT_SINGLE_DUTY_DSN"):
        accept("provider_submission","")
    with pytest.raises(ValueError,match="EXPLICIT_SINGLE_DUTY_DSN"):
        accept("unknown","fake")
    assert "query_control" in DUTIES
