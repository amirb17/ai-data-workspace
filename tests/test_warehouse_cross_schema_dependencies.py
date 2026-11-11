"""Focused offline tests for catalog-level cross-schema dependency inventory."""
from tools import warehouse_cross_schema_dependencies as dep


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class _Connection:
    def execute(self, query, params=()):
        if "current_database()" in query:
            return _Rows([("ai_data_workspace",)])
        if "FROM pg_catalog.pg_constraint" in query:
            return _Rows([
                (
                    "datarise_warehouse_dev",
                    "warehouse_query_attempt_registry",
                    "workspace_fk",
                    "datarise_warehouse_dev", "workspaces",
                    "FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id)",
                ),
                (
                    "datarise_warehouse_dev",
                    "warehouse_query_api_requests",
                    "attempt_fk",
                    "datarise_warehouse_dev",
                    "warehouse_query_attempt_registry",
                    "FOREIGN KEY (attempt_id) REFERENCES warehouse_query_attempt_registry",
                ),
            ])
        if "FROM pg_catalog.pg_trigger" in query:
            return _Rows([(
                "warehouse_query_attempt_registry",
                "warehouse_query_attempt_registry_validate",
                "warehouse_query_attempt_registry_guard",
                "datarise_warehouse_dev", False,
                "CREATE FUNCTION guard() RETURNS trigger AS $$ "
                "SELECT 1 FROM workspaces WHERE workspace_id=NEW.workspace_id; "
                "$$ LANGUAGE plpgsql",
            )])
        if "count(*) FROM pg_catalog.pg_class" in query:
            return _Rows([(14,)])
        raise AssertionError("unexpected query")


def test_inventory_detects_unsafe_isolated_workspace_reference():
    result = dep.inspect(_Connection())
    assert result["query_schema"] == "datarise_warehouse_dev"
    assert result["foreign_keys_to_isolated_canonical_copies"] == 1
    assert result["isolated_canonical_targets"] == ["workspaces"]
    assert result["functions_with_unqualified_canonical_references"] == 1
    assert result["safe_to_activate_query_api"] is False
    assert result["changes_applied"] is False


def test_no_false_canonical_flag_on_query_protocol_fk():
    fks = dep.inspect(_Connection())["foreign_keys"]
    assert fks[1]["referenced_table"] == "warehouse_query_attempt_registry"
    assert not fks[1]["binds_isolated_canonical_copy"]
