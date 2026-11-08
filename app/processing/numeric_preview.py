"""Pure, opt-in exact CSV preview. Never authorizes or publishes stage results."""
import hashlib
import io
import csv
from dataclasses import dataclass, field

import pandas as pd

from app.processing.exact_parquet import write_exact_parquet, read_exact_parquet
from app.processing.silver_transformer import exact_decimal_spec, apply_common_cleaning, apply_transformations
from app.processing.silver_validator import apply_business_rules, calculate_dq_issue_counts
from app.storage.versioned_artifacts import ObjectIdentity, validate_csv_source

MAX_PREVIEW_BYTES = 2*1024*1024
MAX_PREVIEW_ROWS = 10000
MAX_PREVIEW_COLUMNS = 64


@dataclass(frozen=True)
class NumericPreview:
    source_sha256: str
    total_rows: int
    valid_rows: int
    rejected_rows: int
    issues: tuple
    contract: dict = field(repr=False)
    valid_parquet: bytes = field(repr=False)
    quarantine_parquet: bytes = field(repr=False)
    publication_authorized: bool = field(default=False, init=False)


def preview_exact_csv(payload, identity, rules, monetary_columns):
    """Verify bytes, use lexical text, run existing rules, serialize exact types.

    identity/rules here are caller inputs, NOT proof of ownership or approval.
    Production processing must still use registered-source and approved-rule
    repositories and all existing gates. No inferred float is accepted as money.
    """
    from dataclasses import asdict
    identity = ObjectIdentity.decode(asdict(identity))
    if (not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_PREVIEW_BYTES
        or len(payload) != identity.size or hashlib.sha256(payload).hexdigest() != identity.sha256):
        raise ValueError('Numeric preview source identity differs')
    if (not isinstance(monetary_columns, (tuple, list)) or not monetary_columns
        or any(not isinstance(c, str) or not c for c in monetary_columns)
        or len(set(monetary_columns)) != len(monetary_columns)):
        raise ValueError('Explicit distinct monetary columns required')
    validate_csv_source(payload)
    rows = csv.reader(io.StringIO(payload.decode('utf-8-sig')))
    if len(next(rows)) > MAX_PREVIEW_COLUMNS:
        raise ValueError('Numeric preview column bound exceeded')
    for count, _ in enumerate(rows, 1):
        if count > MAX_PREVIEW_ROWS:
            raise ValueError('Numeric preview row bound exceeded')
    lexical = pd.read_csv(io.BytesIO(payload), dtype=str, keep_default_na=False, na_filter=False)
    if lexical.empty or not set(monetary_columns) <= set(lexical.columns):
        raise ValueError('Numeric preview columns or rows unavailable')
    specs = {}
    for rule in rules:
        if rule[3] == 'DATA_TYPE':
            name = rule[2]
            if name in specs:
                raise ValueError('Conflicting numeric type rules')
            specs[name] = exact_decimal_spec(rule[4])
    if any(specs.get(name) is None for name in monetary_columns):
        raise ValueError('Monetary preview requires explicit exact precision and scale')
    cleaned = apply_common_cleaning(lexical)
    frame, errors, nulls = apply_transformations(cleaned, rules, lexical_frame=lexical)
    validated = apply_business_rules(frame, rules, errors, nulls)
    valid = validated[validated['_dq_is_valid']].copy()
    rejected = validated[~validated['_dq_is_valid']].copy()
    def artifact(frame):
        import json
        output = frame.copy()
        output['_dq_violations'] = output['_dq_violations'].apply(json.dumps)
        body, contract = write_exact_parquet(output, rules, list(lexical.columns))
        read_exact_parquet(body, contract, rules, list(lexical.columns), expected_columns=list(output.columns))
        return body, contract
    accepted_bytes, contract = artifact(valid)
    rejected_bytes, rejected_contract = artifact(rejected)
    if contract != rejected_contract:
        raise ValueError('Numeric artifact contracts differ')
    issues = tuple((i['column_name'], i['rule_type'], i['violation_count']) for i in calculate_dq_issue_counts(rejected))
    return NumericPreview(identity.sha256, len(validated), len(valid), len(rejected),
                          issues, contract, accepted_bytes, rejected_bytes)
