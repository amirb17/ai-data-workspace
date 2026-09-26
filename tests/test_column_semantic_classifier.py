from app.processing.column_semantic_classifier import classify_column


def test_record_identifier():
    assert classify_column(
        column_name="transaction_id",
        inferred_type="int64",
        distinct_count=100,
        total_rows=100,
    ) == "RECORD_IDENTIFIER"


def test_reference_identifier():
    assert classify_column(
        column_name="customer_id",
        inferred_type="int64",
        distinct_count=50,
        total_rows=100,
    ) == "REFERENCE_IDENTIFIER"


def test_measure():
    assert classify_column(
        column_name="sales_amount",
        inferred_type="float64",
        distinct_count=100,
        total_rows=100,
    ) == "MEASURE"


def test_datetime_by_name():
    assert classify_column(
        column_name="order_date",
        inferred_type="object",
        distinct_count=100,
        total_rows=100,
    ) == "DATETIME"


def test_low_cardinality_text_is_category():
    assert classify_column(
        column_name="notes",
        inferred_type="object",
        distinct_count=3,
        total_rows=100,
    ) == "CATEGORY"


def test_high_cardinality_text_is_text():
    assert classify_column(
        column_name="notes",
        inferred_type="object",
        distinct_count=95,
        total_rows=100,
    ) == "TEXT"
