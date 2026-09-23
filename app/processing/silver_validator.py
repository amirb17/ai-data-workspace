import pandas as pd

def _apply_not_null_rule(
    df: pd.DataFrame,
    column_name: str,
    null_mask: pd.Series | None = None,
) -> None:

    if column_name not in df.columns:
        raise ValueError(
            f"Column '{column_name}' required by NOT_NULL "
            "rule does not exist in dataset"
        )

    invalid_mask = (
        null_mask
        if null_mask is not None
        else df[column_name].isna()
    )

    for index in df.index[invalid_mask]:
        df.at[index, "_dq_violations"].append(
            f"{column_name}:NOT_NULL"
        )

def _apply_allow_negative_rule(
    df: pd.DataFrame,
    column_name: str,
    allow_negative: bool,
) -> None:

    if column_name not in df.columns:
        raise ValueError(
            f"Column '{column_name}' required by ALLOW_NEGATIVE "
            "rule does not exist in dataset"
        )

    # If negatives are allowed, there is nothing to validate.
    if allow_negative:
        return

    numeric_series = pd.to_numeric(
        df[column_name],
        errors="coerce",
    )

    invalid_mask = numeric_series < 0

    for index in df.index[invalid_mask]:
        df.at[index, "_dq_violations"].append(
            f"{column_name}:ALLOW_NEGATIVE"
        )

def _apply_unique_rule(
    df: pd.DataFrame,
    column_name: str,
) -> None:

    if column_name not in df.columns:
        raise ValueError(
            f"Column '{column_name}' required by UNIQUE "
            "rule does not exist in dataset"
        )

    # Mark every occurrence of a duplicated non-null value.
    duplicate_mask = (
        df[column_name].notna()
        & df[column_name].duplicated(keep=False)
    )

    for index in df.index[duplicate_mask]:
        df.at[index, "_dq_violations"].append(
            f"{column_name}:UNIQUE"
        )

def apply_business_rules(
    df: pd.DataFrame,
    rules: list,
    transformation_errors: dict[str, pd.Series] | None = None,
    original_null_masks: dict[str, pd.Series] | None = None,
) -> pd.DataFrame:

    result = df.copy()

    result["_dq_violations"] = [
        [] for _ in range(len(result))
    ]
    if transformation_errors:
        for violation_name, invalid_mask in transformation_errors.items():

            for index in result.index[invalid_mask]:
                result.at[index, "_dq_violations"].append(
                    violation_name
                )

    for rule in rules:
        column_name = rule[2]
        rule_type = rule[3]
        rule_config = rule[4]

        if rule_type == "NOT_NULL":
            if rule_config.get("required", False):

                null_mask = None

                if original_null_masks:
                    null_mask = original_null_masks.get(
                        column_name
                    )

                _apply_not_null_rule(
                    result,
                    column_name,
                    null_mask=null_mask,
                )

        elif rule_type == "UNIQUE":
            if rule_config.get("required", False):
                _apply_unique_rule(
                    result,
                    column_name,
                )

        elif rule_type == "ALLOW_NEGATIVE":
            _apply_allow_negative_rule(
                result,
                column_name,
                allow_negative=rule_config.get(
                    "allow_negative",
                    True,
                ),
            )

        elif rule_type == "DUPLICATE_HANDLING":
            _apply_duplicate_handling_rule(
                result,
                column_name,
                strategy=rule_config.get(
                    "strategy",
                    "ALLOW",
                ),
            )

    result["_dq_violation_count"] = result[
        "_dq_violations"
    ].apply(len)

    result["_dq_is_valid"] = (
        result["_dq_violation_count"] == 0
    )

    return result

def _apply_duplicate_handling_rule(
    df: pd.DataFrame,
    column_name: str,
    strategy: str,
) -> None:

    if column_name not in df.columns:
        raise ValueError(
            f"Column '{column_name}' required by "
            "DUPLICATE_HANDLING rule does not exist"
        )

    if strategy == "ALLOW":
        return

    if strategy == "QUARANTINE":
        duplicate_mask = (
            df[column_name].notna()
            & df[column_name].duplicated(keep=False)
        )

        for index in df.index[duplicate_mask]:
            df.at[index, "_dq_violations"].append(
                f"{column_name}:DUPLICATE"
            )

        return

    raise ValueError(
        f"Duplicate strategy '{strategy}' "
        "is not supported by Silver V1"
    )
def calculate_dq_issue_counts(
    quarantine_df: pd.DataFrame,
) -> list[dict]:

    issue_counts = {}

    if quarantine_df.empty:
        return []

    for violations in quarantine_df["_dq_violations"]:

        # During validation this should normally still be a Python list.
        # This also handles JSON text defensively.
        if isinstance(violations, str):
            import json
            violations = json.loads(violations)

        for violation in violations:

            column_name, rule_type = violation.split(":", 1)

            key = (
                column_name,
                rule_type,
            )

            issue_counts[key] = (
                issue_counts.get(key, 0) + 1
            )

    return [
        {
            "column_name": column_name,
            "rule_type": rule_type,
            "violation_count": count,
        }
        for (column_name, rule_type), count
        in issue_counts.items()
    ]