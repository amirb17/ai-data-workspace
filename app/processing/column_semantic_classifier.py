import re


RECORD_IDENTIFIER_PATTERNS = (
    r"^transaction_id$",
    r"^order_id$",
    r"^invoice_id$",
    r"^payment_id$",
    r"^event_id$",
    r"^record_id$",
)

REFERENCE_IDENTIFIER_PATTERNS = (
    r"^customer_id$",
    r"^user_id$",
    r"^product_id$",
    r"^account_id$",
    r"^employee_id$",
    r"^supplier_id$",
)

IDENTIFIER_PATTERNS = (
    r"(^|_)id$",
    r"_id$",
    r"^id$",
    r"identifier",
    r"(^|_)key$",
)

MEASURE_PATTERNS = (
    r"amount",
    r"price",
    r"revenue",
    r"cost",
    r"salary",
    r"balance",
    r"quantity",
    r"qty",
    r"total",
    r"profit",
    r"discount",
)

DATETIME_PATTERNS = (
    r"date",
    r"time",
    r"timestamp",
    r"created_at",
    r"updated_at",
)

CATEGORY_PATTERNS = (
    r"status",
    r"category",
    r"type",
    r"segment",
    r"region",
    r"country",
    r"state",
)


def _matches(
    column_name: str,
    patterns: tuple[str, ...],
) -> bool:

    normalized = column_name.strip().lower()

    return any(
        re.search(pattern, normalized)
        for pattern in patterns
    )


def classify_column(
    column_name: str,
    inferred_type: str,
    distinct_count: int,
    total_rows: int,
) -> str:

    normalized_type = inferred_type.lower()

    # Most specific rules must come first
    if _matches(column_name, RECORD_IDENTIFIER_PATTERNS):
        return "RECORD_IDENTIFIER"

    if _matches(column_name, REFERENCE_IDENTIFIER_PATTERNS):
        return "REFERENCE_IDENTIFIER"

    if _matches(column_name, DATETIME_PATTERNS):
        return "DATETIME"

    if _matches(column_name, IDENTIFIER_PATTERNS):
        return "IDENTIFIER"

    if _matches(column_name, MEASURE_PATTERNS):
        return "MEASURE"

    if _matches(column_name, CATEGORY_PATTERNS):
        return "CATEGORY"

    # Physical-type fallback
    if "bool" in normalized_type:
        return "BOOLEAN"

    if (
        "datetime" in normalized_type
        or "date" in normalized_type
    ):
        return "DATETIME"

    if (
        "int" in normalized_type
        or "float" in normalized_type
    ):
        return "MEASURE"

    if (
        "object" in normalized_type
        or "string" in normalized_type
    ):
        if total_rows > 0:
            cardinality_ratio = distinct_count / total_rows

            if cardinality_ratio <= 0.20:
                return "CATEGORY"

        return "TEXT"

    return "UNKNOWN"