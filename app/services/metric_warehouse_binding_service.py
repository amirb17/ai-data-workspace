"""Resolve metric source pins to exact published warehouse bindings.

Read-only metadata resolution only:
- no BigQuery access
- no credentials
- no metric execution
- no consumer switch
"""

from uuid import UUID

from pydantic import TypeAdapter

from app.db import (
    warehouse_repository as repository,
)
from app.processing.metric_execution import (
    MetricExecutionPlan,
)
from app.processing.metric_warehouse_compiler import (
    MetricWarehouseSource,
)
from app.schemas.warehouse_modeling import (
    ModelConfiguration,
    SourceSchema,
)
from app.warehouse.accepted_changes import (
    digest,
)
from app.warehouse.fact_planning import (
    FactContext,
)
from app.warehouse.providers import (
    fact_physical,
    physical,
)
from app.warehouse.providers.base import (
    Binding,
)
from app.warehouse.scd_types import (
    ModelContext,
    SourceContractPin,
)


ADAPTER = TypeAdapter(
    ModelConfiguration
)


def _publication_state_id(row):
    vector = row["source_vector"]

    # Current warehouse execution is V1:
    # exactly one PRIMARY source per model version.
    if (
        not isinstance(vector, list)
        or len(vector) != 1
    ):
        raise ValueError(
            "WAREHOUSE_PUBLICATION_INVALID"
        )

    entry = vector[0]

    if not isinstance(entry, dict):
        raise ValueError(
            "WAREHOUSE_PUBLICATION_INVALID"
        )

    if (
        entry.get("mapping_id")
        != row["source_mapping_id"]
        or entry.get("dataset_id")
        != row["source_dataset_id"]
    ):
        raise ValueError(
            "WAREHOUSE_PUBLICATION_INVALID"
        )

    pin = entry.get("pin")

    if not isinstance(pin, dict):
        raise ValueError(
            "WAREHOUSE_PUBLICATION_INVALID"
        )

    state_id = pin.get(
        "state_id"
    )

    if (
        type(state_id) is not int
        or state_id < 1
    ):
        raise ValueError(
            "WAREHOUSE_PUBLICATION_INVALID"
        )

    return state_id


def _physical_table(
    row,
    workspace_id,
):
    if (
        row["provider"] != "BIGQUERY"
        or row["environment"]
        != "development"
    ):
        raise ValueError(
            "WAREHOUSE_PROVIDER_UNSUPPORTED"
        )

    reference = row[
        "physical_reference"
    ]

    if not isinstance(
        reference,
        dict,
    ):
        raise ValueError(
            "WAREHOUSE_RESOURCE_DRIFT"
        )

    binding = Binding(
        reference.get("project", ""),
        workspace_id,
        row["warehouse_model_id"],
        row[
            "warehouse_model_version_id"
        ],
        row["resource_id"],
        row["generation"],
        row["environment"],
        row["location"],
    )

    expected = {
        "reference_version": 1,
        "project": binding.project,
        "dataset": binding.serving,
        "table": binding.table,
    }

    if reference != expected:
        raise ValueError(
            "WAREHOUSE_RESOURCE_DRIFT"
        )

    return (
        f"{binding.project}."
        f"{binding.serving}."
        f"{binding.table}"
    )


def _context(row):
    configuration = ADAPTER.validate_python(
        row["configuration"]
    )

    schema = SourceSchema.model_validate(
        row["source_schema"]
    )

    contract = SourceContractPin(
        row["source_dataset_id"],
        row["source_dataset_version_id"],
        row["source_policy_id"],
        row["source_rule_version"],
    )

    namespace = UUID(
        str(row["key_namespace"])
    )

    if configuration.model_type == "FACT":
        return FactContext(
            row["warehouse_model_id"],
            row[
                "warehouse_model_version_id"
            ],
            namespace,
            configuration,
            schema,
            contract,
            key_algorithm_version=row[
                "key_algorithm_version"
            ],
            configuration_sha256=digest(
                row["configuration"]
            ),
        )

    return ModelContext(
        row["warehouse_model_id"],
        row[
            "warehouse_model_version_id"
        ],
        namespace,
        configuration,
        schema,
        contract,
        key_algorithm_version=row[
            "key_algorithm_version"
        ],
    )


def _source(
    row,
    workspace_id,
    state_id,
):
    context = _context(row)

    table = _physical_table(
        row,
        workspace_id,
    )

    if (
        row["head_revision"]
        != row["publication_revision"]
    ):
        raise ValueError(
            "WAREHOUSE_PUBLICATION_INVALID"
        )

    if isinstance(
        context,
        FactContext,
    ):
        configuration = (
            context.configuration
        )

        time = (
            configuration.event_time
        )

        return MetricWarehouseSource(
            dataset_id=row[
                "source_dataset_id"
            ],
            state_id=state_id,
            model_id=row[
                "warehouse_model_id"
            ],
            model_version_id=row[
                "warehouse_model_version_id"
            ],
            resource_id=row[
                "resource_id"
            ],
            generation=row[
                "generation"
            ],
            publication_id=row[
                "publication_id"
            ],
            publication_revision=row[
                "publication_revision"
            ],
            physical_table=table,
            columns=(
                fact_physical.columns(
                    context
                )
            ),
            grain_keys=tuple(
                configuration
                .grain
                .identity_columns
            ),
            business_keys=(),
            configuration_sha256=digest(
                row["configuration"]
            ),
            source_vector_sha256=digest(
                row["source_vector"]
            ),
            model_type="FACT",
            event_time_column=(
                time.column
                if time
                else None
            ),
            event_time_interpretation=(
                time.interpretation
                if time
                else None
            ),
            event_time_timezone=(
                time.timezone
                if time
                else None
            ),
        )

    configuration = (
        context.configuration
    )

    return MetricWarehouseSource(
        dataset_id=row[
            "source_dataset_id"
        ],
        state_id=state_id,
        model_id=row[
            "warehouse_model_id"
        ],
        model_version_id=row[
            "warehouse_model_version_id"
        ],
        resource_id=row[
            "resource_id"
        ],
        generation=row[
            "generation"
        ],
        publication_id=row[
            "publication_id"
        ],
        publication_revision=row[
            "publication_revision"
        ],
        physical_table=table,
        columns=physical.columns(
            context
        ),
        grain_keys=(),
        business_keys=tuple(
            configuration.business_keys
        ),
        configuration_sha256=digest(
            row["configuration"]
        ),
        source_vector_sha256=digest(
            row["source_vector"]
        ),
        model_type="DIMENSION",
    )


def resolve_metric_warehouse_sources(
    conn,
    plan: MetricExecutionPlan,
    *,
    environment="development",
):
    if not isinstance(
        plan,
        MetricExecutionPlan,
    ):
        raise ValueError(
            "Metric execution plan required"
        )

    # Query-governed BigQuery serving is currently
    # development-only and CompiledQuery deliberately
    # accepts only dr_dev_w... warehouse tables.
    if environment != "development":
        raise ValueError(
            "WAREHOUSE_ENVIRONMENT_UNSUPPORTED"
        )

    pins = tuple(
        plan.sources
    )

    if not pins:
        raise ValueError(
            "WAREHOUSE_SOURCE_REQUIRED"
        )

    dataset_ids = []

    for pin in pins:
        dataset_id = pin.get(
            "dataset_id"
        )

        state_id = pin.get(
            "state_id"
        )

        if (
            type(dataset_id) is not int
            or dataset_id < 1
            or type(state_id) is not int
            or state_id < 1
        ):
            raise ValueError(
                "WAREHOUSE_SOURCE_PIN_INVALID"
            )

        dataset_ids.append(
            dataset_id
        )

    if (
        len(set(dataset_ids))
        != len(dataset_ids)
    ):
        raise ValueError(
            "WAREHOUSE_SOURCE_PIN_INVALID"
        )

    rows = (
        repository
        .metric_warehouse_bindings(
            conn,
            plan.workspace_id,
            dataset_ids,
            environment,
        )
    )

    by_dataset = {}

    for row in rows:
        by_dataset.setdefault(
            row["source_dataset_id"],
            [],
        ).append(row)

    resolved = {}

    for pin in pins:
        dataset_id = pin[
            "dataset_id"
        ]

        state_id = pin[
            "state_id"
        ]

        exact = []

        for row in by_dataset.get(
            dataset_id,
            (),
        ):
            published_state = (
                _publication_state_id(
                    row
                )
            )

            if (
                published_state
                == state_id
            ):
                exact.append(row)

        if not exact:
            raise ValueError(
                "WAREHOUSE_PUBLICATION_NOT_CURRENT"
            )

        if len(exact) != 1:
            # Multiple models/generations are legitimate metadata,
            # but the metric plan contains no reviewed warehouse-model
            # selection. Never guess between them.
            raise ValueError(
                "WAREHOUSE_BINDING_AMBIGUOUS"
            )

        resolved[
            dataset_id
        ] = _source(
            exact[0],
            plan.workspace_id,
            state_id,
        )

    return resolved