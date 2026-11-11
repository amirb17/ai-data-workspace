"""Offline exact identifier safety for reviewed cross-schema FK cutover."""
import pytest

from tools.warehouse_query_fk_rebind_plan import temporary_constraint_name


def test_long_postgresql_name_does_not_collide_with_replacement():
    original = "warehouse_query_attempt_logic_workspace_id_warehouse_model_fkey"
    temporary = temporary_constraint_name(original)
    assert len(temporary.encode()) <= 63
    assert temporary != original
    assert temporary == temporary_constraint_name(original)


def test_distinct_constraints_get_distinct_deterministic_names():
    names = [
        "warehouse_query_api_requests_user_id_fkey",
        "warehouse_query_api_requests_workspace_id_fkey",
        "warehouse_query_attempt_registry_owner_key_fkey",
    ]
    assert len({temporary_constraint_name(name) for name in names}) == len(names)


@pytest.mark.parametrize("name", ["", "public.users", "quoted\"thing", "x" * 64, "9invalid"])
def test_untrusted_constraint_names_are_rejected(name):
    with pytest.raises(RuntimeError, match="INVALID_EXISTING_CONSTRAINT"):
        temporary_constraint_name(name)
