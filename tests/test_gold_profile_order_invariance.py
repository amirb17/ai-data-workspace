"""Regression: Gold planner must agree with the separate trusted verifier.

The worker reads profiles from PostgreSQL; the verifier receives a JSON snapshot
whose order may differ. Artifact identity and required roles must be identical.
"""
from app.processing.gold_planner import build_gold_plan


def test_gold_plan_does_not_depend_on_profile_row_order():
    names = ["payment_id", "employee_id", "payment_date", "payment_amount", "payment_status"]
    types = ["object", "int64", "object", "int64", "object"]
    profiles = [
        (i, 4, name, dtype, 0, 3 if name != "payment_id" else 9, 0, None, None, 0)
        for i, (name, dtype) in enumerate(zip(names, types), 1)
    ]
    expected = build_gold_plan(sorted(profiles, key=lambda row: row[2])).to_dict()
    assert build_gold_plan(profiles).to_dict() == expected
    assert build_gold_plan(list(reversed(profiles))).to_dict() == expected
    # Lexical primary identifier is stable: employee_id precedes payment_id.
    assert "employee_id_summary" in [x["artifact_name"] for x in expected["artifacts"]]
