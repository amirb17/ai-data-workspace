from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from contextlib import nullcontext
from app.services import delivery_lifecycle_service as lifecycle

@pytest.fixture(autouse=True)
def lifecycle_stubs(monkeypatch):
    # This suite isolates the API bridge; real lifecycle locks are exercised in PG tests.
    monkeypatch.setattr(lifecycle, "lifecycle_lock", lambda _: nullcontext())
    monkeypatch.setattr(lifecycle, "archive_state", lambda _: (None, None))
from fastapi.testclient import TestClient

from app.main import app
from app.api import files
from app.api.identity import get_current_user
from app.schemas.business_rules import BusinessRuleAnswer
from app.services import business_rule_service as rules, processing_context_service as context, processing_service as processing

USER = {"user_id": 1, "owner_key": "dev:test"}


@pytest.fixture
def scope(monkeypatch):
    app.dependency_overrides[get_current_user] = lambda: USER
    monkeypatch.setattr(context, "validate_upload_context", lambda *args: None)
    monkeypatch.setattr(context, "get_dataset_by_workspace", lambda *args: (20, "Orders", None, USER["owner_key"]))
    monkeypatch.setattr(context, "get_upload_request_by_id", lambda _: (40, 1, 30, 10, 20, "UPLOADED", None))
    monkeypatch.setattr(context, "get_physical_file_by_id", lambda _: (30, "a.csv", 10, "hash", "private", "UPLOADED", "schema"))
    monkeypatch.setattr(context, "get_dataset_version_file_by_id", lambda _: (60, 50, 30, "AWAITING_RULES", None, 20, 10))
    monkeypatch.setattr(context, "get_upload_processing_association", lambda *args: (60, 50, 30, "AWAITING_RULES", 1, 0, None, False, None))
    monkeypatch.setattr(context, "get_active_processing_attempt", lambda _: None)
    monkeypatch.setattr(context, "get_active_business_rules_for_dataset_version", lambda _: [])
    monkeypatch.setattr(context, "get_approved_policy", lambda *args: [])
    monkeypatch.setattr(context, "get_delivery_attempts", lambda *args: [])
    monkeypatch.setattr(context, "get_latest_successful_dq_run_for_dataset_version_file", lambda _: None)
    monkeypatch.setattr(context, "get_dataset_profile_summary", lambda _: None)
    yield TestClient(app)
    app.dependency_overrides.pop(get_current_user, None)


def test_completed_upload_can_prepare_bronze_and_exposes_pending_rules(scope, monkeypatch):
    calls = []
    monkeypatch.setattr(files, "start_processing", lambda id: calls.append(id))
    result = scope.post("/files/uploads/40/process?workspace_id=10&dataset_id=20")
    assert result.status_code == 200
    assert calls == [40]
    assert result.json()["status"] == "AWAITING_RULES"
    assert result.json()["silver_can_proceed"] is False
    assert "private" not in result.text


@pytest.mark.parametrize("status", ["INITIATED", "FAILED"])
def test_incomplete_upload_cannot_start_bronze(scope, monkeypatch, status):
    monkeypatch.setattr(context, "get_upload_request_by_id", lambda _: (40, 1, None, 10, 20, status, None))
    monkeypatch.setattr(files, "start_processing", lambda _: pytest.fail("Bronze must not start"))
    assert scope.post("/files/uploads/40/process").status_code == 400


@pytest.mark.parametrize("query", ["workspace_id=11&dataset_id=20", "workspace_id=10&dataset_id=21"])
def test_wrong_context_rejected_on_all_rule_routes(scope, query):
    root = "/files/dataset-version-files/60/business-rules"
    assert scope.get(root + "/suggestions?" + query).status_code == 403
    assert scope.get("/files/uploads/40/processing-context?" + query).status_code == 403
    # Mutations validate context inside their transaction; ownership helper is
    # checked directly here without requiring a real database for route tests.
    with pytest.raises(PermissionError):
        context.owned_rule_context(60, USER, 11 if query.startswith("workspace_id=11") else 10,
                                   21 if query.endswith("21") else 20)


def test_upload_wrong_user_is_rejected(scope, monkeypatch):
    monkeypatch.setattr(context, "get_upload_request_by_id", lambda _: (40, 2, 30, 10, 20, "UPLOADED", None))
    assert scope.get("/files/uploads/40/processing-context").status_code == 403


def test_questions_require_dataset_owner(scope, monkeypatch):
    monkeypatch.setattr(context, "get_dataset_by_workspace", lambda *args: (20, "Orders", None, "dev:other"))
    assert scope.get("/files/dataset-version-files/60/business-rules/suggestions").status_code == 403


def test_public_questions_and_saved_answers_read_after_finalization(scope, monkeypatch):
    monkeypatch.setattr(context, "get_business_rule_questions", lambda _: {
        "questions": [{"column_name": "id", "suggested_rule_type": "NOT_NULL", "options": ["YES", "NO"]}],
        "answers": [{"column_name": "id", "rule_type": "NOT_NULL", "answer": "YES"}], "rule_version": 3,
    })
    monkeypatch.setattr(context, "get_dataset_version_file_by_id", lambda _: (60, 50, 30, "READY_FOR_SILVER", None, 20, 10))
    first = scope.get("/files/dataset-version-files/60/business-rules/suggestions").json()
    second = scope.get("/files/dataset-version-files/60/business-rules/suggestions").json()
    assert first == second
    assert first["rule_state"] == "FINALIZED"
    assert first["answers"][0]["answer"] == "YES"


@pytest.fixture
def rule_state(monkeypatch):
    state = {"status": "AWAITING_RULES", "version": 0, "answers": {}, "active": {}}
    monkeypatch.setattr(rules, "get_dataset_version_file_by_id", lambda _: (60, 50, 30, state["status"]))
    monkeypatch.setattr(rules, "generate_rule_suggestions", lambda _: [{"column_name": "id", "suggested_rule_type": "NOT_NULL", "options": ["YES", "NO"]}])
    monkeypatch.setattr(rules, "get_business_rule_answer_for_dataset_version", lambda **kw: (state["answers"][kw["column_name"]],) if kw["column_name"] in state["answers"] else None)
    monkeypatch.setattr(rules, "save_business_rule_answer_for_dataset_version", lambda **kw: state["answers"].update({kw["column_name"]: kw["answer"]}))
    monkeypatch.setattr(rules, "save_business_rule_for_dataset_version", lambda **kw: state["active"].update({kw["column_name"]: kw["rule_config"]}))
    monkeypatch.setattr(rules, "deactivate_business_rule_for_dataset_version", lambda **kw: state["active"].pop(kw["column_name"], None))
    monkeypatch.setattr(rules, "get_active_business_rules_for_dataset_version", lambda _: list(state["active"].values()))
    monkeypatch.setattr(rules, "get_business_rule_answers_for_dataset_version", lambda _: [(1, 50, k, "NOT_NULL", v) for k, v in state["answers"].items()])
    monkeypatch.setattr(rules, "get_dataset_version_rule_version", lambda _: state["version"])
    def increment(_):
        state["version"] += 1
        return state["version"]
    def transition(**kw):
        state["status"] = kw["status"]
        return (60, 50, 30, state["status"])
    monkeypatch.setattr(rules, "increment_dataset_version_rule_version", increment)
    monkeypatch.setattr(rules, "update_dataset_version_file_status", transition)
    monkeypatch.setattr(rules, "get_rule_approval_context", lambda _: (50, state["version"], state.get("approved"), state.get("applied"), False, True))
    def approve(_):
        state["approved"] = state["version"]
        state["applied"] = state["version"]
        return transition(status="READY_FOR_SILVER")
    monkeypatch.setattr(rules, "approve_association_rules", approve)
    return state


def test_save_finalize_and_repeat_preserve_rule_version(rule_state):
    answers = [BusinessRuleAnswer(column_name="id", rule_type="NOT_NULL", answer="YES")]
    assert rules.submit_business_rule_answers(60, answers)["rule_version"] == 1
    assert rule_state["status"] == "AWAITING_RULES"  # saving is not approval
    assert rules.submit_business_rule_answers(60, answers)["rule_version"] == 1
    assert rules.finalize_business_rules(60)["finalized"] is True
    assert rule_state["status"] == "READY_FOR_SILVER"
    assert rules.finalize_business_rules(60)["already_finalized"] is True
    assert rule_state["version"] == 1


def test_all_no_cannot_finalize_to_unusable_silver_state(rule_state):
    rules.submit_business_rule_answers(60, [BusinessRuleAnswer(column_name="id", rule_type="NOT_NULL", answer="NO")])
    assert rules.finalize_business_rules(60)["finalized"] is False
    assert rule_state["status"] == "AWAITING_RULES"


@pytest.mark.parametrize("answer", ["DECIMAL", "ALLOW"])
def test_cross_type_answer_rejected_before_any_write(rule_state, answer):
    with pytest.raises(ValueError, match="Invalid"):
        rules.submit_business_rule_answers(60, [BusinessRuleAnswer(column_name="id", rule_type="NOT_NULL", answer=answer)])
    assert rule_state["answers"] == {}
    assert rule_state["version"] == 0


def test_changed_draft_answer_increments_existing_version(rule_state):
    for answer in ("YES", "NO", "YES"):
        rules.submit_business_rule_answers(60, [BusinessRuleAnswer(column_name="id", rule_type="NOT_NULL", answer=answer)])
    assert rule_state["version"] == 3
    assert rule_state["active"] == {"id": {"required": True}}


@pytest.mark.parametrize("status", ["READY_FOR_SILVER", "SILVER_PROCESSING", "READY_FOR_GOLD", "GOLD_PROCESSING", "SUCCESS", "SILVER_FAILED", "GOLD_FAILED"])
def test_repeated_bronze_uses_non_regressing_initializer(monkeypatch, status):
    monkeypatch.setattr(processing, "get_upload_request_by_id", lambda _: (40, 1, 30, 10, 20))
    monkeypatch.setattr(processing, "get_physical_file_by_id", lambda _: (30, "a.csv", 10, "hash", "private", "UPLOADED", "schema"))
    monkeypatch.setattr(processing, "resolve_dataset_version_by_id", lambda **kw: {"dataset_version_id": 50})
    monkeypatch.setattr(processing, "assign_physical_file_to_dataset_version", lambda **kw: (60, 50, 30, status))
    monkeypatch.setattr(processing, "initialize_dataset_version_file_rules", lambda **kw: (60, 50, 30, status))
    monkeypatch.setattr(processing, "update_dataset_version_file_status", lambda **kw: pytest.fail("Unconditional reset"))
    assert processing.start_processing(40)["dataset_version_file"][3] == status


def test_public_state_reports_real_silver_prerequisites(scope, monkeypatch):
    monkeypatch.setattr(context, "get_upload_processing_association", lambda *args: (60, 50, 30, "READY_FOR_SILVER", 1, 3, 3, False, 3))
    monkeypatch.setattr(context, "get_active_business_rules_for_dataset_version", lambda _: [(1,)])
    monkeypatch.setattr(context, "get_approved_policy", lambda *args: [(1,)])
    result = scope.get("/files/uploads/40/processing-context").json()
    assert result["rule_version"] == 3 and result["silver_can_proceed"] is True
    assert result["valid_rows"] is None


def test_rule_mutation_rejects_stale_version(scope, monkeypatch):
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, query, params): self.query = query
        def fetchone(self): return (3,) if "rule_version" in self.query else None
    @contextmanager
    def transaction():
        yield SimpleNamespace(cursor=lambda: Cursor())
    monkeypatch.setattr(context, "repository_transaction", transaction)
    with pytest.raises(context.StaleRuleVersion):
        with context.rule_mutation(60, USER, 2):
            pytest.fail("Stale request must not write")


def test_processing_errors_are_sanitized(scope, monkeypatch):
    monkeypatch.setattr(files, "start_processing", lambda _: (_ for _ in ()).throw(RuntimeError("private-internal-location")))
    result = scope.post("/files/uploads/40/process")
    assert result.status_code == 409
    assert "private-internal-location" not in result.text
