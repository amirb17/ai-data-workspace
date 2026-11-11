"""414F0-B2 no-write inventory evaluation: expected denial and tamper cases."""
from app.security.warehouse_privilege_assessment import (
    ROLES, RUNTIME_ROLES, TABLES, assess_inventory,
)


def inventory():
    roles = [
        dict(role_name=n, exists=True, safe_attributes=True)
        for n in sorted(ROLES)
    ]
    memberships = [
        dict(member=a, target=b, has_membership=False)
        for a in sorted(ROLES) for b in sorted(ROLES) if a != b
    ]
    relations = [
        dict(role_name=r, relation_name=t, relation_exists=True,
             owns_relation=False, can_select=True,
             can_insert=False, can_update=False, can_delete=False, can_truncate=False)
        for r in sorted(RUNTIME_ROLES) for t in sorted(TABLES)
    ]
    return roles, memberships, relations, []


def check(data=None, schema="warehouse_test"):
    return assess_inventory(*(data if data is not None else inventory()), audited_schema=schema)


def test_even_clean_inventory_is_not_an_execution_permit():
    a = check()
    assert a.verdict == "NO_GO" and not a.approved
    assert a.blockers == ("FURTHER_RUNTIME_ACCEPTANCE_REQUIRED",)
    assert a.role_rows == 5 and a.membership_rows == 20
    assert a.relation_rows == 36


def test_actual_public_schema_and_unprovisioned_roles_fail_closed():
    roles, members, relations, cols = inventory()
    for r in roles:
        r.update(exists=False, safe_attributes=False)
    for m in members:
        m["has_membership"] = None
    for row in relations:
        row.update(relation_exists=False, owns_relation=None, can_select=None,
                   can_insert=None, can_update=None, can_delete=None, can_truncate=None)
    result = check((roles, members, relations, cols), schema="public")
    assert result.verdict == "NO_GO" and not result.approved
    for blocker in ("UNREVIEWED_OR_PUBLIC_SCHEMA", "ROLE_NOT_PROVISIONED",
                    "WAREHOUSE_METADATA_TABLE_NOT_PRESENT",
                    "CROSS_ROLE_MEMBERSHIP_OR_UNKNOWN"):
        assert blocker in result.blockers


def test_missing_duplicate_and_unsafe_records_denied():
    for index, field, value in (
        (0, "safe_attributes", False),
        (1, "has_membership", True),
        (2, "owns_relation", True),
        (2, "can_delete", True),
        (2, "can_insert", None),
    ):
        data = inventory()
        data[index][0][field] = value
        assert check(data).blockers != ("FURTHER_RUNTIME_ACCEPTANCE_REQUIRED",)
    for index in (0, 1, 2):
        data = inventory()
        data[index].pop()
        assert check(data).blockers != ("FURTHER_RUNTIME_ACCEPTANCE_REQUIRED",)
        data = inventory()
        data[index].append(dict(data[index][0]))
        assert check(data).blockers != ("FURTHER_RUNTIME_ACCEPTANCE_REQUIRED",)


def test_column_writes_are_never_silently_approved():
    data = inventory()
    data[3].append(dict(role_name="datarise_api", relation_name="warehouse_query_send_journal",
                        column_name="phase", can_insert=False, can_update=True))
    assert "COLUMN_WRITE_PRIVILEGE_REQUIRES_REVIEW" in check(data).blockers


def test_assessment_has_no_database_or_provider_dependency():
    import ast, inspect
    import app.security.warehouse_privilege_assessment as m
    tree = ast.parse(inspect.getsource(m))
    imports = [
        alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
        for alias in node.names
    ]
    assert not any(s.startswith(("psycopg", "google", "boto3")) for s in imports)
