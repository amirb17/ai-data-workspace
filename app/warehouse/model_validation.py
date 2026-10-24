"""Pure warehouse configuration validation against a pinned source schema."""
from app.schemas.warehouse_modeling import DimensionConfiguration, FactConfiguration, SourceSchema

TECHNICAL_PREFIXES = ('_dq_', '_source_', '_bronze_', '_gold_', '_datarise_', '_application_')
KEY_TYPES = {'STRING', 'INTEGER', 'DECIMAL', 'BOOLEAN', 'DATE', 'TIMESTAMP'}
MEASURE_TYPES = {'INTEGER', 'DECIMAL'}
FACT_ATTRIBUTE_TYPES = {
    'STRING',
    'INTEGER',
    'DECIMAL',
    'BOOLEAN',
    'DATE',
    'TIMESTAMP',
}
def validate_model(
    configuration: FactConfiguration | DimensionConfiguration,
    schema: SourceSchema,
) -> dict:
    if not isinstance(
        configuration,
        (FactConfiguration, DimensionConfiguration),
    ):
        raise ValueError(
            'Explicit typed FACT/DIMENSION configuration required'
        )

    names = [c.name for c in schema.columns]

    if len(names) != len(set(names)):
        raise ValueError(
            'Source schema contains duplicate column names'
        )

    source = {c.name: c for c in schema.columns}

    if isinstance(configuration, FactConfiguration):
        identity = configuration.grain.identity_columns
        measures = configuration.measures
        attributes = configuration.attributes
        time = configuration.event_time
    else:
        identity = configuration.business_keys
        measures = ()
        attributes = (
            configuration.tracked_attributes
            + configuration.nonhistorical_attributes
        )
        time = configuration.effective_time

    roles = (
        list(identity)
        + list(measures)
        + list(attributes)
        + ([time.column] if time else [])
    )

    if len(roles) != len(set(roles)):
        raise ValueError(
            'Configured roles must be distinct: '
            'key, measures, attributes and time cannot overlap'
        )

    for name in roles:
        if (
            name != name.strip()
            or name.lower().startswith(TECHNICAL_PREFIXES)
            or any(ord(c) < 32 for c in name)
        ):
            raise ValueError(
                'Only exact business column names are supported'
            )

        if name not in source:
            raise ValueError(
                'Configured column is absent from pinned source schema'
            )

    for name in identity:
        if source[name].data_type not in KEY_TYPES:
            raise ValueError(
                'Floating-point business/event identities are unsupported'
            )

    for name in measures:
        if source[name].data_type not in MEASURE_TYPES:
            raise ValueError(
                'Fact measures must use exact numeric source columns'
            )
    if isinstance(configuration, FactConfiguration):
        for name in attributes:
            if source[name].data_type not in FACT_ATTRIBUTE_TYPES:
                raise ValueError(
                    'Fact attributes must use canonically supported source columns'
                )
    if (
        time
        and source[time.column].data_type
        not in ('DATE', 'TIMESTAMP')
    ):
        raise ValueError(
            'Event/effective time must use a typed DATE or TIMESTAMP column'
        )

    return dict(
        valid=True,
        identity_columns=list(identity),
        identity_null_policy='REJECT',
        source_key_nullability={
            name: source[name].nullable
            for name in identity
        },
        history_start_known=(
            False
            if isinstance(configuration, DimensionConfiguration)
            else None
        ),
    )

def validate_source_policy(configuration, *, load_strategy: str, snapshot_coverage: str | None):
    if isinstance(configuration, DimensionConfiguration) and configuration.snapshot_absence_policy=='DEACTIVATE_ON_COMPLETE':
        if load_strategy!='SNAPSHOT' or snapshot_coverage!='COMPLETE':
            raise ValueError('Absence deactivation requires a source contract authorizing COMPLETE snapshots')
