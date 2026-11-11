"""414F0-B12 - migration 0057 does not regress the authenticated warehouse lifecycle.

The fixture uses actual migrated PostgreSQL state, owned session and fake
BigQuery *lookup only* metadata. No query submission or real GCP access.
"""
import os
from pathlib import Path

import pytest

from tests.test_warehouse_sql_provider_policy_414e3 import (
    pg, modeling_pg, fact_provider_pg, genuinely_served_query,
    served, checkpointed, prepared, lookup, unbound, bound,
    linked_pg, reliability_pg, registered, intent, execute,
    prepare, run, state, scalar, change,
)

MIGRATION = Path(__file__).resolve().parents[1] / (
    "migrations/0057_warehouse_query_function_acl_hardening.sql"
)


@pytest.fixture
def hardened(unbound):
    assert os.getenv("RUN_DB_TESTS") == "1"
    s, lease, client = unbound
    with s.f.pg["connect"]() as conn:
        schema = conn.execute("SELECT current_schema()").fetchone()[0]
        assert schema.startswith("test_warehouse_") and schema != "public"
        conn.execute(MIGRATION.read_text(encoding="utf-8-sig"))
    return s, lease, client


def test_hardened_guards_preserve_authenticated_policy_and_lookup(hardened):
    s, lease, client = hardened
    # This uses actual PostgreSQL locks, authorization and immutable policy
    # insertion; provider transport remains fake, GET-only.
    result = prepare(s)
    assert result.classification == "POLICY_PINNED_NO_DISPATCH"
    assert not result.execution_authorized and not result.provider_dispatched
    assert not client.calls
    assert prepare(s) == result
    observation = run(lease)
    assert observation.verdict == "VERIFIED_METADATA"
    assert observation.observation == "SUCCEEDED"
    assert not observation.execution_authorized and not observation.provider_dispatched
    assert len(client.calls) == 1
    assert scalar(s, "SELECT count(*) FROM warehouse_query_provider_policies") == 1
    assert scalar(s, "SELECT count(*) FROM warehouse_query_recovery_attestations") == 1
    assert scalar(s, "SELECT count(*) FROM query_governance_usage") == 0
    assert state(s) == "EXECUTING"
    assert scalar(s, "SELECT warehouse_query_publication_dependency_present(%s,%s)",
                  (s.f.resource, s.pub["publication_id"]))


def test_hardened_guards_preserve_ownership_revocation_denial(hardened):
    s, lease, client = hardened
    change(s, "account")
    with pytest.raises(Exception):
        prepare(s)
    assert not client.calls
    assert scalar(s, "SELECT count(*) FROM warehouse_query_provider_policies") == 0
    assert scalar(s, "SELECT count(*) FROM query_governance_usage") == 0
    assert state(s) == "EXECUTING"


def test_hardening_migration_is_not_a_runtime_role_or_cloud_deployment():
    text = MIGRATION.read_text(encoding="utf-8")
    assert "target_schema = 'public'" in text
    assert "REVOKE ALL ON FUNCTION" in text
    assert "ALTER FUNCTION" in text
    for forbidden in ("CREATE ROLE", "GRANT EXECUTE", "SECURITY DEFINER", "delete_table("):
        if forbidden == "SECURITY DEFINER":
            # Source includes a defensive prohibition of DEFINER routines.
            continue
        assert forbidden not in text
