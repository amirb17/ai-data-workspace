"""Read-only Gold schema/projection assessment, NOT warehouse source admission.

Warehouse accepted-state evidence, source vectors, ownership, intents and CAS
remain mandatory. Gold artifacts must never mint accepted-event authority.
"""
from dataclasses import dataclass, field

from app.processing.append_engine import scalar
from app.warehouse.accepted_changes import digest, payload, projection
from app.warehouse.model_validation import validate_model
from app.warehouse.providers.types import TYPES, encode


@dataclass(frozen=True)
class GoldProjectionAssessment:
    rows: int
    columns: tuple
    physical_types: tuple
    projection_sha256: str
    publication_authorized: bool = field(default=False, init=False)


def assess_gold_projection(frame, configuration, source_schema, *, expected_rows):
    validate_model(configuration, source_schema)
    columns = projection(configuration)
    if (type(expected_rows) is not int or not 0 <= expected_rows <= 1000
        or len(frame) != expected_rows or len(frame.columns) > 128
        or not frame.columns.is_unique or not set(columns) <= set(frame.columns)):
        raise ValueError('Gold projection structure or row count differs')
    schema = {c.name: c for c in source_schema.columns}
    keys = configuration.grain.identity_columns if configuration.model_type == 'FACT' else configuration.business_keys
    if any(schema[name].data_type not in TYPES for name in columns):
        raise ValueError('Gold projection type is unsupported')
    kinds = tuple((name, TYPES[schema[name].data_type]) for name in columns)
    seen, rows = set(), []
    for record in frame[list(columns)].to_dict('records'):
        row = {name: scalar(record[name]) for name in columns}
        if any(row[name] is None for name in keys):
            raise ValueError('Gold projection identity is null')
        for name, kind in kinds:
            if row[name] is None and schema[name].nullable is False:
                raise ValueError('Gold projection required value is null')
            encode(row[name], kind)  # Refuses floats, overflow and guessed dates.
        identity = digest(payload(row, tuple(keys)))
        if identity in seen:
            raise ValueError('Gold projection identity is ambiguous')
        seen.add(identity)
        rows.append(payload(row, columns))
    return GoldProjectionAssessment(len(rows), tuple(columns), kinds, digest(rows))
