"""414F0-B4 requirement inventory is not a deployable permission script."""
from dataclasses import FrozenInstanceError
import pytest

from app.security.warehouse_permission_requirements import (
    CONTROLLERS, REQUIREMENTS, Requirement, inspect_controller,
)
from app.security.warehouse_privilege_assessment import TABLES


@pytest.mark.parametrize("controller", sorted(CONTROLLERS))
def test_each_controller_explicit_and_no_go(controller):
    review = inspect_controller(controller)
    assert review.requirements
    assert all(r.controller == controller for r in review.requirements)
    assert review.status == "NO_GO"
    assert review.grants_authorized is False
    assert "FUNCTION_TRIGGER_LOCK_SEQUENCE_DEPENDENCIES_NOT_REVIEWED" in review.blockers


def test_only_known_tables_append_or_read_and_no_duplicate_entries():
    keys = [(r.controller, r.table, r.privilege) for r in REQUIREMENTS]
    assert len(keys) == len(set(keys))
    assert {r.table for r in REQUIREMENTS} <= TABLES
    assert {r.privilege for r in REQUIREMENTS} == {"SELECT", "INSERT"}
    assert all(r.review_status == "DRAFT_NOT_GRANTED" for r in REQUIREMENTS)


def test_mutation_and_unknown_controller_rejected():
    with pytest.raises(ValueError):
        Requirement("untrusted_browser", "warehouse_query_send_journal", "INSERT")
    with pytest.raises(ValueError):
        Requirement("recovery_observer", "warehouse_query_send_journal", "DELETE")
    with pytest.raises(ValueError):
        Requirement("recovery_observer", "users", "SELECT")
    with pytest.raises(ValueError):
        inspect_controller("datarise_api")
    with pytest.raises(FrozenInstanceError):
        REQUIREMENTS[0].table = "users"


def test_no_database_cloud_or_grant_execution_imported():
    import ast, inspect
    import app.security.warehouse_permission_requirements as m
    source = inspect.getsource(m)
    tree = ast.parse(source)
    imports = [alias.name for node in ast.walk(tree)
               if isinstance(node, ast.Import) for alias in node.names]
    assert not any(name.startswith(("psycopg", "boto3", "google")) for name in imports)
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                   and node.func.id in {"exec", "eval"} for node in ast.walk(tree))
