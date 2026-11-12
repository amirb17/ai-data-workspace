"""Offline unit tests for read-only operational ACL readiness report."""
from tools.warehouse_worker_operation_readiness import inspect, REQUIREMENTS
from tools.warehouse_worker_acl import ROLES


class Row:
    def __init__(self, value): self.value=value
    def fetchone(self): return self.value


class Catalog:
    def __init__(self, denies=()):
        self.denies=set(denies)
        self.calls=[]
        self.calls_sql=[]
    def execute(self, statement, params):
        assert statement.strip()=="SELECT has_table_privilege(%s, %s, %s)"
        assert len(params)==3
        self.calls.append(params)
        self.calls_sql.append((statement,params))
        return Row((params not in self.denies,))


def test_audit_passes_if_all_required_grants_are_present():
    conn=Catalog()
    report=inspect(conn)
    assert report["all_operations_permission_ready"] is True
    assert report["query_execution_enabled"] is False
    assert len(conn.calls)==sum(map(len,REQUIREMENTS.values()))
    assert set(report["workers"])==set(ROLES)


def test_reports_missing_lock_privilege_without_attempting_update():
    role=ROLES["recovery_attestation"]
    missing=(role,"datarise_warehouse_dev.warehouse_query_send_intents","UPDATE")
    conn=Catalog(denies={missing})
    report=inspect(conn)
    assert not report["all_operations_permission_ready"]
    blockers=report["workers"]["recovery_attestation"]["missing_privileges"]
    assert any(b["relation"]==missing[1] and b["privilege"]=="UPDATE"
               for b in blockers)
    assert all(sql.strip()=="SELECT has_table_privilege(%s, %s, %s)"
               for sql, params in conn.calls_sql)


def test_every_duty_requires_real_reservation_lock_permission():
    for duty,requirements in REQUIREMENTS.items():
        if duty == "result_authorization":
            assert not any(privilege == "UPDATE" for _, privilege, _ in requirements)
            assert ("datarise_warehouse_dev.warehouse_query_request_envelopes",
                    "SELECT", "pinned_request") in requirements
            continue
        assert ("public.query_governance_reservations","UPDATE",
                "reservation_for_update") in requirements
        if duty == "accounting":
            assert not any(privilege == "UPDATE" and relation.startswith(
                "datarise_warehouse_dev.") for relation, privilege, _ in requirements)
            assert ("public.workspaces", "SELECT", "canonical_owner") in requirements
            assert ("datarise_warehouse_dev.warehouse_query_request_envelopes",
                    "SELECT", "pinned_request_evidence") in requirements
            continue
        assert ("datarise_warehouse_dev.warehouse_query_send_intents",
                "UPDATE","send_intent_for_update") in requirements
