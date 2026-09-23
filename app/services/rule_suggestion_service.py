from app.db.file_repository import (
    get_dataset_profile_summary,
    get_dataset_profiles,
)
from app.processing.column_semantic_classifier import classify_column


def generate_rule_suggestions(file_id: int) -> list[dict]:
    summary = get_dataset_profile_summary(file_id)

    if not summary:
        raise ValueError(
            f"Dataset profile summary not found for file {file_id}"
        )

    profiles = get_dataset_profiles(file_id)

    if not profiles:
        raise ValueError(
            f"Column profiles not found for file {file_id}"
        )

    total_rows = summary[2]

    if total_rows <= 0:
        return []

    suggestions = []

    for profile in profiles:
        column_name = profile[2]
        inferred_type = profile[3]
        null_count = profile[4]
        distinct_count = profile[5]
        duplicate_count = profile[6]
        negative_count = profile[9]

        role = classify_column(
            column_name=column_name,
            inferred_type=inferred_type,
            distinct_count=distinct_count,
            total_rows=total_rows,
        )

        uniqueness_ratio = distinct_count / total_rows

        # ==================================================
        # RECORD IDENTIFIER
        # Example: transaction_id, order_id, invoice_id
        # ==================================================
        if role == "RECORD_IDENTIFIER":

            if (
                uniqueness_ratio >= 0.98
                and null_count == 0
            ):
                suggestions.append(
                    {
                        "column_name": column_name,
                        "semantic_role": role,
                        "suggested_rule_type": "UNIQUE",
                        "reason": (
                            f"'{column_name}' appears to identify "
                            f"individual records and is unique in "
                            f"{uniqueness_ratio:.1%} of rows."
                        ),
                        "question": (
                            f"Should '{column_name}' uniquely identify "
                            "each record?"
                        ),
                        "options": [
                            "YES",
                            "NO",
                        ],
                    }
                )

            suggestions.append(
                {
                    "column_name": column_name,
                    "semantic_role": role,
                    "suggested_rule_type": "NOT_NULL",
                    "reason": (
                        f"'{column_name}' appears to identify individual "
                        f"records. {null_count} null value(s) were observed."
                    ),
                    "question": (
                        f"Is '{column_name}' required for every record?"
                    ),
                    "options": [
                        "YES",
                        "NO",
                    ],
                }
            )

        # ==================================================
        # REFERENCE IDENTIFIER
        # Example: customer_id, product_id, account_id
        # ==================================================
        elif role == "REFERENCE_IDENTIFIER":

            suggestions.append(
                {
                    "column_name": column_name,
                    "semantic_role": role,
                    "suggested_rule_type": "NOT_NULL",
                    "reason": (
                        f"'{column_name}' appears to reference another "
                        f"business entity. {null_count} null value(s) "
                        "were observed."
                    ),
                    "question": (
                        f"Is '{column_name}' required for every record?"
                    ),
                    "options": [
                        "YES",
                        "NO",
                    ],
                }
            )

        # ==================================================
        # GENERIC IDENTIFIER
        # Unknown identifier semantics
        # ==================================================
        elif role == "IDENTIFIER":

            suggestions.append(
                {
                    "column_name": column_name,
                    "semantic_role": role,
                    "suggested_rule_type": "NOT_NULL",
                    "reason": (
                        f"'{column_name}' appears to be an identifier. "
                        f"{null_count} null value(s) were observed."
                    ),
                    "question": (
                        f"Is '{column_name}' required for every record?"
                    ),
                    "options": [
                        "YES",
                        "NO",
                    ],
                }
            )

        # ==================================================
        # MEASURE
        # Example: amount, revenue, price, quantity
        # ==================================================
        elif role == "MEASURE":
            suggestions.append(
                {
                    "column_name": column_name,
                    "semantic_role": role,
                    "suggested_rule_type": "DATA_TYPE",
                    "reason": (
                        f"'{column_name}' is classified as a numeric measure "
                        "and should be stored using a numeric type."
                    ),
                    "question": (
                        f"Should '{column_name}' be converted to a decimal "
                        "numeric type?"
                    ),
                    "options": [
                        "DECIMAL",
                        "NO",
                    ],
                }
            )

            suggestions.append(
                {
                    "column_name": column_name,
                    "semantic_role": role,
                    "suggested_rule_type": "ALLOW_NEGATIVE",
                    "reason": (
                        f"'{column_name}' is classified as a numeric measure. "
                        f"{negative_count or 0} negative value(s) were observed."
                    ),
                    "question": (
                        f"Are negative values valid for '{column_name}'?"
                    ),
                    "options": [
                        "YES",
                        "NO",
                    ],
                }
            )

            if null_count > 0:
                suggestions.append(
                    {
                        "column_name": column_name,
                        "semantic_role": role,
                        "suggested_rule_type": "NOT_NULL",
                        "reason": (
                            f"{null_count} missing value(s) were detected "
                            f"in '{column_name}'."
                        ),
                        "question": (
                            f"Should '{column_name}' be required "
                            "for every record?"
                        ),
                        "options": [
                            "YES",
                            "NO",
                        ],
                    }
                )

        # ==================================================
        # DATETIME
        # ==================================================
        elif role == "DATETIME":
            suggestions.append(
                {
                    "column_name": column_name,
                    "semantic_role": role,
                    "suggested_rule_type": "DATA_TYPE",
                    "reason": (
                        f"'{column_name}' appears to represent date or "
                        "time information and can be standardized."
                    ),
                    "question": (
                        f"Should '{column_name}' be converted to a "
                        "datetime type?"
                    ),
                    "options": [
                        "DATETIME",
                        "NO",
                    ],
                }
            )

        

            if null_count > 0:
                suggestions.append(
                    {
                        "column_name": column_name,
                        "semantic_role": role,
                        "suggested_rule_type": "NOT_NULL",
                        "reason": (
                            f"{null_count} missing date/time value(s) "
                            f"were detected in '{column_name}'."
                        ),
                        "question": (
                            f"Is '{column_name}' required "
                            "for every record?"
                        ),
                        "options": [
                            "YES",
                            "NO",
                        ],
                    }
                )

        # ==================================================
        # CATEGORY
        # ==================================================
        elif role == "CATEGORY":

            if null_count > 0:
                suggestions.append(
                    {
                        "column_name": column_name,
                        "semantic_role": role,
                        "suggested_rule_type": "NOT_NULL",
                        "reason": (
                            f"{null_count} missing categorical value(s) "
                            f"were detected in '{column_name}'."
                        ),
                        "question": (
                            f"Is '{column_name}' required?"
                        ),
                        "options": [
                            "YES",
                            "NO",
                        ],
                    }
                )

        # ==================================================
        # DUPLICATE OBSERVATION
        #
        # Do not apply this automatically to reference IDs.
        # Repeated customer_id/product_id values may be valid.
        # ==================================================
        if (
            duplicate_count > 0
            and role in {"RECORD_IDENTIFIER", "IDENTIFIER"}
        ):
            suggestions.append(
                {
                    "column_name": column_name,
                    "semantic_role": role,
                    "suggested_rule_type": "DUPLICATE_HANDLING",
                    "reason": (
                        f"{duplicate_count} repeated value(s) "
                        f"were observed in identifier '{column_name}'."
                    ),
                    "question": (
                        f"How should duplicate values in "
                        f"'{column_name}' be handled?"
                    ),
                    "options": [
                        "ALLOW",
                        "KEEP_FIRST",
                        "KEEP_LATEST",
                        "QUARANTINE",
                    ],
                }
            )

    return suggestions