"""
Golden test for the Silver transform/validate pipeline, using the
exact dataset and rules from the project spec:

    transaction_id NOT_NULL = YES
    transaction_id DUPLICATE_HANDLING = QUARANTINE
    customer_id NOT_NULL = YES
    amount DATA_TYPE = DECIMAL
    amount ALLOW_NEGATIVE = YES
    transaction_date DATA_TYPE = DATETIME

Expected:
    Valid:   301, 306
    Rejected: 302, 302, 304, 305

    amount           | INVALID_NUMERIC   | 1
    customer_id       | NOT_NULL          | 1
    transaction_date  | INVALID_DATETIME  | 1
    transaction_id    | DUPLICATE         | 2
"""

import io

import pandas as pd

from app.processing.silver_transformer import (
    apply_common_cleaning,
    apply_transformations,
)
from app.processing.silver_validator import (
    apply_business_rules,
    calculate_dq_issue_counts,
)


CSV_TEXT = """transaction_id,customer_id,amount,transaction_date,city
301,801,1200,2026-09-20,"  Pune  "
302,802,-250,2026-09-21,"Mumbai "
302,803,abc,2026-09-22," Bengaluru "
304,N/A,900,2026-09-23," Pune"
305,805,500,wrong-date,"Hyderabad "
306,806,750,2026-09-25,"  Chennai"
"""


def _rule(column_name, rule_type, rule_config):
    # Matches the tuple shape returned by
    # app.db.file_repository.get_active_business_rules:
    # (rule_id, file_id, column_name, rule_type, rule_config, is_active)
    return (None, None, column_name, rule_type, rule_config, True)


RULES = [
    _rule("transaction_id", "NOT_NULL", {"required": True}),
    _rule("transaction_id", "DUPLICATE_HANDLING", {"strategy": "QUARANTINE"}),
    _rule("customer_id", "NOT_NULL", {"required": True}),
    _rule("amount", "DATA_TYPE", {"type": "DECIMAL"}),
    _rule("amount", "ALLOW_NEGATIVE", {"allow_negative": True}),
    _rule("transaction_date", "DATA_TYPE", {"type": "DATETIME"}),
]


def _run_pipeline():
    df = pd.read_csv(io.StringIO(CSV_TEXT))

    cleaned = apply_common_cleaning(df)

    transformed, transformation_errors, original_null_masks = apply_transformations(
        df=cleaned,
        rules=RULES,
    )

    validated = apply_business_rules(
        df=transformed,
        rules=RULES,
        transformation_errors=transformation_errors,
        original_null_masks=original_null_masks,
    )

    return validated


def test_valid_rows_match_spec():
    validated = _run_pipeline()

    valid_ids = sorted(
        validated.loc[validated["_dq_is_valid"], "transaction_id"].tolist()
    )

    assert valid_ids == [301, 306]


def test_rejected_rows_match_spec():
    validated = _run_pipeline()

    rejected_ids = sorted(
        validated.loc[~validated["_dq_is_valid"], "transaction_id"].tolist()
    )

    assert rejected_ids == [302, 302, 304, 305]


def test_negative_amount_is_allowed():
    validated = _run_pipeline()

    row = validated.loc[validated["transaction_id"] == 302].iloc[0]

    assert not any(
        v.startswith("amount:ALLOW_NEGATIVE") for v in row["_dq_violations"]
    )


def test_dq_issue_counts_match_spec():
    validated = _run_pipeline()

    quarantine_df = validated[~validated["_dq_is_valid"]]

    issues = {
        (issue["column_name"], issue["rule_type"]): issue["violation_count"]
        for issue in calculate_dq_issue_counts(quarantine_df)
    }

    assert issues == {
        ("amount", "INVALID_NUMERIC"): 1,
        ("customer_id", "NOT_NULL"): 1,
        ("transaction_date", "INVALID_DATETIME"): 1,
        ("transaction_id", "DUPLICATE"): 2,
    }
