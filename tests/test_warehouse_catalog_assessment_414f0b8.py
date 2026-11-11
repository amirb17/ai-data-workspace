"""414F0-B8: evaluate function/trigger catalog evidence without permissions."""
from dataclasses import replace

from app.security.warehouse_catalog_assessment import (
    EXPECTED_FUNCTIONS, EXPECTED_TRIGGERS, assess_catalog,
)


def sample():
    identity = [dict(audited_schema="test_warehouse_abc123")]
    functions = [
        dict(expected_function=name, exists=True, security_definer=False,
             public_execute=False, function_owner="postgres",
             function_settings=["search_path=pg_catalog,test_warehouse_abc123"])
        for name in sorted(EXPECTED_FUNCTIONS)
    ]
    triggers = [
        dict(expected_trigger=name, exists=True, enabled_mode="O",
             table_name="synthetic_table", function_target="guard()")
        for name in sorted(EXPECTED_TRIGGERS)
    ]
    return identity, functions, triggers


def test_clean_catalog_is_still_no_go():
    data = sample()
    result = assess_catalog(*data)
    assert result.verdict == "NO_GO"
    assert not result.permissions_approved
    assert result.blockers == ("AUTHENTICATED_ROLE_AND_TRANSITIVE_PRIVILEGE_REVIEW_REQUIRED",)
    assert result.functions_checked == 14 and result.triggers_checked == 20


def test_public_execute_and_unpinned_search_path_are_review_blockers():
    data = sample()
    data[1][0]["public_execute"] = True
    data[1][1]["function_settings"] = None
    result = assess_catalog(*data)
    assert "PUBLIC_EXECUTE_REQUIRES_REVIEW" in result.blockers
    assert "FUNCTION_SEARCH_PATH_REQUIRES_REVIEW" in result.blockers
    assert len(result.public_execute_functions) == 1


def test_missing_function_and_disabled_trigger_fail_closed():
    data = sample()
    data[1][0]["exists"] = False
    data[2][0]["enabled_mode"] = "D"
    data[2][1]["function_target"] = None
    result = assess_catalog(*data)
    assert "FUNCTION_MISSING" in result.blockers
    assert "TRIGGER_DISABLED_OR_UNKNOWN" in result.blockers
    assert "TRIGGER_TARGET_UNKNOWN" in result.blockers


def test_missing_duplicate_and_unexpected_catalog_rows_denied():
    for index in (1, 2):
        data = sample()
        data[index].pop()
        assert any("COVERAGE" in b for b in assess_catalog(*data).blockers)
        data = sample()
        data[index].append(dict(data[index][0]))
        assert any("COVERAGE" in b for b in assess_catalog(*data).blockers)
    data = sample()
    data[0][0]["audited_schema"] = "public"
    assert "INVALID_CATALOG_SCOPE" in assess_catalog(*data).blockers


def test_assessment_has_no_database_provider_dependency():
    import ast
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "app/security/warehouse_catalog_assessment.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules = [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    assert not any(x.startswith(("psycopg", "google", "boto3")) for x in modules)
