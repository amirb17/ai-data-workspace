"""Deterministic physical representation of provider-neutral FACT state.

No database access, BigQuery client, SQL generation or publication behavior.
"""

from app.warehouse.accepted_changes import projection,digest
from app.warehouse.fact_planning import (
    FactContext,
    FactLogicalPlan,
    FactState,
    fact_state_digest,
    fact_values,
)
from app.warehouse.keys import surrogate_key
from app.warehouse.providers.base import ErrorCode, ProviderError
from app.warehouse.providers.types import TYPES, decode, encode


def columns(context: FactContext):
    """Stable logical-to-physical business-column mapping."""
    if not isinstance(context, FactContext):
        raise ValueError("Typed FACT context required")

    source = {
        column.name: column
        for column in context.source_schema.columns
    }

    result = []

    for index, name in enumerate(
        projection(context.configuration)
    ):
        column = source.get(name)

        if column is None:
            raise ProviderError(
                ErrorCode.SCHEMA_MISMATCH
            )

        kind = TYPES.get(column.data_type)

        if kind is None:
            raise ProviderError(
                ErrorCode.SCHEMA_MISMATCH
            )

        result.append(
            (
                name,
                f"c{index}",
                kind,
            )
        )

    return tuple(result)


def schema(context: FactContext):
    """Physical V1 FACT row, excluding future publication-revision envelope."""
    identity = set(
        context.configuration.grain.identity_columns
    )

    business = [
        (
            physical_name,
            kind,
            (
                "REQUIRED"
                if logical_name in identity
                else "NULLABLE"
            ),
        )
        for logical_name, physical_name, kind
        in columns(context)
    ]

    return tuple(
        [
            (
                "fact_sk",
                "STRING",
                "REQUIRED",
            ),
            (
                "is_active",
                "BOOL",
                "REQUIRED",
            ),
            (
                "source_position",
                "INT64",
                "REQUIRED",
            ),
            (
                "last_event_id",
                "STRING",
                "REQUIRED",
            ),
            (
                "last_event_checksum",
                "STRING",
                "REQUIRED",
            ),
            (
                "state_sha256",
                "STRING",
                "REQUIRED",
            ),
        ]
        + business
    )


def _state_values(
    context: FactContext,
    state: FactState,
):
    if (
        not isinstance(state, FactState)
        or state.context != context
    ):
        raise ProviderError(
            ErrorCode.TARGET_INCONSISTENT
        )

    values = {}

    for name, value in (
        state.identity
        + state.measures
        + state.attributes
    ):
        if name in values:
            raise ProviderError(
                ErrorCode.TARGET_INCONSISTENT
            )

        values[name] = value

    time = context.configuration.event_time

    if time:
        if time.column in values:
            raise ProviderError(
                ErrorCode.TARGET_INCONSISTENT
            )

        values[time.column] = state.event_time

    elif state.event_time is not None:
        raise ProviderError(
            ErrorCode.TARGET_INCONSISTENT
        )

    if set(values) != set(
        projection(context.configuration)
    ):
        raise ProviderError(
            ErrorCode.TARGET_INCONSISTENT
        )

    try:
        (
            identity,
            measures,
            attributes,
            event_time,
        ) = fact_values(
            context,
            values,
        )
    except (ValueError, TypeError, KeyError):
        raise ProviderError(
            ErrorCode.TARGET_INCONSISTENT
        ) from None

    if (
        identity != state.identity
        or measures != state.measures
        or attributes != state.attributes
        or event_time != state.event_time
    ):
        raise ProviderError(
            ErrorCode.TARGET_INCONSISTENT
        )

    try:
        expected_sk = surrogate_key(
            context.namespace,
            "FACT",
            context.configuration.grain.identity_columns,
            values,
            algorithm_version=(
                context.key_algorithm_version
            ),
        )
    except (ValueError, TypeError):
        raise ProviderError(
            ErrorCode.TARGET_INCONSISTENT
        ) from None

    if expected_sk != state.fact_sk:
        raise ProviderError(
            ErrorCode.TARGET_INCONSISTENT
        )

    return values


def rows(plan: FactLogicalPlan):
    """Encode exact candidate FACT states for a future physical provider."""
    if not isinstance(plan, FactLogicalPlan):
        raise ValueError(
            "FACT logical plan required"
        )

    context = plan.context
    mapping = columns(context)

    result = []

    for state in plan.after:
        values = _state_values(
            context,
            state,
        )

        row = {
            "fact_sk": state.fact_sk,
            "is_active": encode(
                state.active,
                "BOOL",
            ),
            "source_position": encode(
                state.source_position,
                "INT64",
            ),
            "last_event_id": encode(
                state.last_event_id,
                "STRING",
            ),
            "last_event_checksum": encode(
                state.last_event_checksum,
                "STRING",
            ),
            "state_sha256": fact_state_digest(
                state
            ),
        }

        for (
            logical_name,
            physical_name,
            kind,
        ) in mapping:
            row[physical_name] = encode(
                values[logical_name],
                kind,
            )

        result.append(row)

    return result


def states(
    context: FactContext,
    physical_rows,
):
    """Reconstruct and independently validate physical FACT rows."""
    if not isinstance(context, FactContext):
        raise ValueError(
            "Typed FACT context required"
        )

    mapping = columns(context)

    expected_fields = {
        name
        for name, _, _
        in schema(context)
    }

    result = {}
    time = context.configuration.event_time

    for incoming in physical_rows:
        row = dict(incoming)

        if set(row) != expected_fields:
            raise ProviderError(
                ErrorCode.TARGET_INCONSISTENT
            )

        values = {}

        try:
            for (
                logical_name,
                physical_name,
                kind,
            ) in mapping:
                values[logical_name] = decode(
                    row[physical_name],
                    kind,
                )

            (
                identity,
                measures,
                attributes,
                event_time,
            ) = fact_values(
                context,
                values,
            )

            fact_sk = surrogate_key(
                context.namespace,
                "FACT",
                context.configuration.grain.identity_columns,
                values,
                algorithm_version=(
                    context.key_algorithm_version
                ),
            )

            if row["fact_sk"] != fact_sk:
                raise ValueError(
                    "FACT identity mismatch"
                )

            state = FactState(
                context=context,
                fact_sk=fact_sk,
                identity=identity,
                measures=measures,
                attributes=attributes,
                event_time=event_time,
                active=decode(
                    row["is_active"],
                    "BOOL",
                ),
                source_position=decode(
                    row["source_position"],
                    "INT64",
                ),
                last_event_id=decode(
                    row["last_event_id"],
                    "STRING",
                ),
                last_event_checksum=decode(
                    row["last_event_checksum"],
                    "STRING",
                ),
            )

            if (
                row["state_sha256"]
                != fact_state_digest(state)
            ):
                raise ValueError(
                    "FACT state digest mismatch"
                )

        except ProviderError:
            raise
        except (
            ValueError,
            TypeError,
            KeyError,
        ):
            raise ProviderError(
                ErrorCode.TARGET_INCONSISTENT
            ) from None

        if fact_sk in result:
            raise ProviderError(
                ErrorCode.TARGET_INCONSISTENT
            )

        result[fact_sk] = state

    return tuple(
        result[key]
        for key in sorted(result)
    )
def schema_hash(context: FactContext):
    return digest(
        [
            schema(context),
            columns(context),
        ]
    )