"""Deterministic MetricExecutionPlan -> governed BigQuery query compilation.

Pure compilation only:
- no database access
- no provider access
- no metric consumer switch
- no caller-supplied SQL
"""

from dataclasses import dataclass
import re
from typing import Literal

from app.processing.metric_execution import (
    MAX_GROUPS,
    MAX_OUTPUT_BYTES,
    MetricExecutionPlan,
    scalar,
)
import json

from app.query_governance.providers.base import (
    CompiledQuery,
    QueryExecutionResult,
)
from app.processing.relationship_evidence import digest
from app.schemas.metrics import MetricProposal


COMPILER_VERSION = 1

_HEX64 = re.compile(r"^[a-f0-9]{64}$")
_PHYSICAL_COLUMN = re.compile(r"^c[0-9]+$")

_PHYSICAL_TYPES = {
    "STRING",
    "INT64",
    "BOOL",
    "BIGNUMERIC",
    "DATE",
    "TIMESTAMP",
}

_NUMERIC_TYPES = {
    "INT64",
    "BIGNUMERIC",
}


@dataclass(frozen=True)
class MetricWarehouseSource:
    """Trusted published warehouse binding resolved before compilation."""

    dataset_id: int
    state_id: int

    model_id: int
    model_version_id: int

    resource_id: int
    generation: int

    publication_id: int
    publication_revision: int
    serving_materialization_id: int
    serving_sha256: str
    serving_row_count: int

    physical_table: str

    columns: tuple[
        tuple[str, str, str],
        ...
    ]

    grain_keys: tuple[str, ...]

    configuration_sha256: str
    source_vector_sha256: str

    model_type: Literal[
        "FACT",
        "DIMENSION",
    ] = "FACT"

    business_keys: tuple[str, ...] = ()

    event_time_column: str | None = None
    event_time_interpretation: (
        Literal[
            "UTC",
            "SOURCE_TIMEZONE",
        ]
        | None
    ) = None
    event_time_timezone: str | None = None

    def __post_init__(self):
        positive = (
            self.dataset_id,
            self.state_id,
            self.model_id,
            self.model_version_id,
            self.resource_id,
            self.generation,
            self.publication_id,
            self.publication_revision,
            self.serving_materialization_id,
        )

        if any(
            type(value) is not int
            or value < 1
            for value in positive
        ):
            raise ValueError(
                "Positive warehouse publication pins required"
            )

        if (
            not _HEX64.fullmatch(
                self.configuration_sha256
            )
            or not _HEX64.fullmatch(
                self.source_vector_sha256
            )
            or not _HEX64.fullmatch(
                self.serving_sha256
            )
        ):
            raise ValueError(
                "Pinned warehouse contract hashes required"
            )
        if (
            type(self.serving_row_count)
            is not int
            or self.serving_row_count < 0
        ):
            raise ValueError(
                "Valid serving row count required"
            )
        if (
            not isinstance(
                self.physical_table,
                str,
            )
            or not self.physical_table
        ):
            raise ValueError(
                "Physical warehouse table required"
            )

        if (
            not 1 <= len(self.columns) <= 128
            or any(
                not isinstance(item, tuple)
                or len(item) != 3
                for item in self.columns
            )
        ):
            raise ValueError(
                "Bounded physical column mapping required"
            )

        logical = [
            item[0]
            for item in self.columns
        ]

        physical = [
            item[1]
            for item in self.columns
        ]

        if (
            len(set(logical)) != len(logical)
            or len(set(physical))
            != len(physical)
        ):
            raise ValueError(
                "Distinct warehouse columns required"
            )

        for (
            logical_name,
            physical_name,
            kind,
        ) in self.columns:
            if (
                not isinstance(
                    logical_name,
                    str,
                )
                or not logical_name
                or not _PHYSICAL_COLUMN.fullmatch(
                    physical_name
                )
                or kind not in _PHYSICAL_TYPES
            ):
                raise ValueError(
                    "Invalid warehouse column mapping"
                )

        if self.model_type == "FACT":
            if (
                not self.grain_keys
                or len(set(self.grain_keys))
                != len(self.grain_keys)
                or any(
                    key not in logical
                    for key in self.grain_keys
                )
            ):
                raise ValueError(
                    "Explicit FACT grain required"
                )

            if self.business_keys:
                raise ValueError(
                    "FACT cannot declare DIMENSION business keys"
                )

        elif self.model_type == "DIMENSION":
            if self.grain_keys:
                raise ValueError(
                    "DIMENSION cannot declare FACT grain"
                )

            if (
                not self.business_keys
                or len(set(self.business_keys))
                != len(self.business_keys)
                or any(
                    key not in logical
                    for key in self.business_keys
                )
            ):
                raise ValueError(
                    "Explicit DIMENSION business keys required"
                )

            if (
                self.event_time_column is not None
                or self.event_time_interpretation
                is not None
                or self.event_time_timezone
                is not None
            ):
                raise ValueError(
                    "DIMENSION event-time semantics are not FACT semantics"
                )

            return

        else:
            raise ValueError(
                "Supported warehouse model type required"
            )

        if self.event_time_column is None:
            if (
                self.event_time_interpretation
                is not None
                or self.event_time_timezone
                is not None
            ):
                raise ValueError(
                    "Time policy requires event-time column"
                )

            return

        mapping = {
            name: kind
            for name, _, kind in self.columns
        }

        if mapping.get(
            self.event_time_column
        ) not in (
            "DATE",
            "TIMESTAMP",
        ):
            raise ValueError(
                "Event-time column must be typed"
            )

        if self.event_time_interpretation not in (
            "UTC",
            "SOURCE_TIMEZONE",
        ):
            raise ValueError(
                "Explicit event-time interpretation required"
            )

        if (
            self.event_time_interpretation
            == "UTC"
            and self.event_time_timezone
            is not None
        ):
            raise ValueError(
                "UTC event time cannot declare timezone"
            )

        if (
            self.event_time_interpretation
            == "SOURCE_TIMEZONE"
            and not self.event_time_timezone
        ):
            raise ValueError(
                "Source timezone required"
            )

    def pin(self):
        return {
            "dataset_id": self.dataset_id,
            "state_id": self.state_id,
            "model_id": self.model_id,
            "model_version_id": (
                self.model_version_id
            ),
            "resource_id": self.resource_id,
            "generation": self.generation,
            "publication_id": (
                self.publication_id
            ),
            "publication_revision": (
                self.publication_revision
            ),
            "physical_table": (
                self.physical_table
            ),
            "model_type": self.model_type,
            "configuration_sha256": (
                self.configuration_sha256
            ),
            "source_vector_sha256": (
                self.source_vector_sha256
            ),
            "serving_materialization_id": (
                self.serving_materialization_id
            ),
            "serving_sha256": (
                self.serving_sha256
            ),
            "serving_row_count": (
                self.serving_row_count
            ),
        }

def _mapping(source: MetricWarehouseSource):
    return {
        logical: (
            physical,
            kind,
        )
        for logical, physical, kind
        in source.columns
    }


def _column(
    source: MetricWarehouseSource,
    dataset_id: int,
    name: str,
    alias: str,
):
    if dataset_id != source.dataset_id:
        raise ValueError(
            "WAREHOUSE_SOURCE_REQUIRED"
        )

    value = _mapping(source).get(name)

    if value is None:
        raise ValueError(
            "WAREHOUSE_COLUMN_NOT_APPROVED"
        )

    physical, kind = value

    return (
        f"{alias}.{physical}",
        kind,
    )

def _time_expression(
    source: MetricWarehouseSource,
    metric: MetricProposal,
    alias: str,
):
    if source.model_type != "FACT":
        raise ValueError(
            "TIME_SEMANTICS_UNRESOLVED"
        )

    if (
        metric.time_field is None
        or metric.time_bucket is None
    ):
        raise ValueError(
            "TIME_POLICY_REQUIRED"
        )

    if (
        metric.time_field.dataset_id
        != source.dataset_id
        or metric.time_field.column
        != source.event_time_column
    ):
        raise ValueError(
            "TIME_SEMANTICS_UNRESOLVED"
        )

    column, kind = _column(
        source,
        metric.time_field.dataset_id,
        metric.time_field.column,
        alias,
    )

    bucket = metric.time_bucket

    part = (
        "WEEK(MONDAY)"
        if bucket == "WEEK"
        else bucket
    )

    if kind == "DATE":
        return (
            f"DATE_TRUNC({column}, {part})"
        )

    if kind != "TIMESTAMP":
        raise ValueError(
            "TIME_TYPE_UNSUPPORTED"
        )

    timezone = (
        "UTC"
        if source.event_time_interpretation
        == "UTC"
        else source.event_time_timezone
    )

    if not timezone:
        raise ValueError(
            "TIME_SEMANTICS_UNRESOLVED"
        )

    return (
        "DATETIME_TRUNC("
        f"DATETIME({column}, '{timezone}'), "
        f"{part}"
        ")"
    )

def _formula_expression(
    metric: MetricProposal,
    source: MetricWarehouseSource,
    alias: str,
):
    expressions = {}
    kinds = {}

    for node in metric.formula.nodes:
        if node.id in expressions:
            raise ValueError(
                "INVALID_METRIC_GRAPH"
            )

        if node.op == "COLUMN":
            if (
                node.column is None
                or node.args
            ):
                raise ValueError(
                    "INVALID_METRIC_GRAPH"
                )

            expression, kind = _column(
                source,
                node.column.dataset_id,
                node.column.column,
                alias,
            )

            expressions[node.id] = expression
            kinds[node.id] = (
                "ROW",
                kind,
            )

            continue

        args = []

        for arg in node.args:
            if arg not in expressions:
                raise ValueError(
                    "INVALID_METRIC_GRAPH"
                )

            args.append(
                expressions[arg]
            )

        if node.op == "MULTIPLY":
            if (
                len(args) != 2
                or any(
                    kinds[arg][0] != "ROW"
                    for arg in node.args
                )
                or any(
                    kinds[arg][1]
                    not in _NUMERIC_TYPES
                    for arg in node.args
                )
            ):
                raise ValueError(
                    "INVALID_METRIC_GRAPH"
                )

            expressions[node.id] = (
                f"({args[0]} * {args[1]})"
            )

            kinds[node.id] = (
                "ROW",
                "BIGNUMERIC",
            )

        elif node.op == "COUNT":
            if (
                node.column is not None
                or node.args
            ):
                raise ValueError(
                    "INVALID_METRIC_GRAPH"
                )

            expressions[node.id] = (
                "COUNT(*)"
            )

            kinds[node.id] = (
                "AGG",
                "INT64",
            )

        elif node.op in (
            "COUNT_DISTINCT",
            "SUM",
            "AVG",
            "MIN",
            "MAX",
        ):
            if (
                node.column is not None
                or len(args) != 1
                or kinds[node.args[0]][0]
                != "ROW"
            ):
                raise ValueError(
                    "INVALID_METRIC_GRAPH"
                )

            operand_kind = (
                kinds[node.args[0]][1]
            )

            if (
                node.op
                in (
                    "SUM",
                    "AVG",
                    "MIN",
                    "MAX",
                )
                and operand_kind
                not in _NUMERIC_TYPES
            ):
                raise ValueError(
                    "WAREHOUSE_NUMERIC_REQUIRED"
                )

            if node.op == "COUNT_DISTINCT":
                expression = (
                    f"COUNT(DISTINCT {args[0]})"
                )
                result_kind = "INT64"

            else:
                expression = (
                    f"{node.op}({args[0]})"
                )
                result_kind = (
                    "BIGNUMERIC"
                    if node.op == "AVG"
                    else operand_kind
                )

            expressions[node.id] = expression
            kinds[node.id] = (
                "AGG",
                result_kind,
            )

        elif node.op == "DIVIDE":
            if (
                node.column is not None
                or len(args) != 2
                or any(
                    kinds[arg][0] != "AGG"
                    for arg in node.args
                )
            ):
                raise ValueError(
                    "INVALID_METRIC_GRAPH"
                )

            numerator, denominator = args

            expressions[node.id] = (
                "("
                f"CASE WHEN {numerator} IS NULL "
                f"OR {denominator} IS NULL "
                f"OR {denominator} = 0 "
                "THEN NULL "
                f"ELSE {numerator} / {denominator} "
                "END"
                ")"
            )

            kinds[node.id] = (
                "AGG",
                "BIGNUMERIC",
            )

        else:
            raise ValueError(
                "UNSUPPORTED_EXPRESSION"
            )

    if (
        metric.formula.root
        not in expressions
        or kinds[
            metric.formula.root
        ][0]
        != "AGG"
    ):
        raise ValueError(
            "INVALID_METRIC_GRAPH"
        )

    result = expressions[
        metric.formula.root
    ]

    if metric.formula.scale != 1:
        result = (
            f"({result} * "
            f"{metric.formula.scale})"
        )

    return result

def _resolve_sources(
    plan: MetricExecutionPlan,
    metric: MetricProposal,
    sources: dict[
        int,
        MetricWarehouseSource,
    ],
):
    expected = {
        pin["dataset_id"]
        for pin in plan.sources
    }

    if set(sources) != expected:
        raise ValueError(
            "EXACT_WAREHOUSE_SOURCE_SET_REQUIRED"
        )

    for pin in plan.sources:
        source = sources.get(
            pin["dataset_id"]
        )

        if (
            not isinstance(
                source,
                MetricWarehouseSource,
            )
            or source.dataset_id
            != pin["dataset_id"]
            or source.state_id
            != pin["state_id"]
        ):
            raise ValueError(
                "WAREHOUSE_SOURCE_PIN_MISMATCH"
            )

    fact = sources.get(
        metric.base_dataset_id
    )

    if (
        fact is None
        or fact.model_type != "FACT"
    ):
        raise ValueError(
            "BASE_FACT_SOURCE_REQUIRED"
        )

    if not plan.joins:
        if len(sources) != 1:
            raise ValueError(
                "UNUSED_WAREHOUSE_SOURCE"
            )

        return (
            fact,
            None,
            {
                fact.dataset_id: "f",
            },
            None,
        )

    if (
        len(plan.joins) != 1
        or len(sources) != 2
    ):
        raise ValueError(
            "WAREHOUSE_JOIN_NOT_SUPPORTED"
        )

    join = plan.joins[0]

    parent = join["parent"]
    child = join["child"]

    if (
        child["dataset_id"]
        != fact.dataset_id
        or join["cardinality"]
        not in (
            "ONE_TO_MANY",
            "ONE_TO_ONE",
        )
        or len(parent["columns"]) != 1
        or len(child["columns"]) != 1
    ):
        raise ValueError(
            "FANOUT_UNSAFE"
        )

    dimension = sources.get(
        parent["dataset_id"]
    )

    if (
        dimension is None
        or dimension.model_type
        != "DIMENSION"
    ):
        raise ValueError(
            "DIMENSION_SOURCE_REQUIRED"
        )

    parent_columns = tuple(
        parent["columns"]
    )

    if (
        parent_columns
        != dimension.business_keys
    ):
        raise ValueError(
            "DIMENSION_KEY_MISMATCH"
        )

    _column(
        fact,
        fact.dataset_id,
        child["columns"][0],
        "f",
    )

    _column(
        dimension,
        dimension.dataset_id,
        parent["columns"][0],
        "d0",
    )

    aliases = {
        fact.dataset_id: "f",
        dimension.dataset_id: "d0",
    }

    return (
        fact,
        dimension,
        aliases,
        join,
    )
def compile_metric_query(
    plan: MetricExecutionPlan,
    sources: dict[
        int,
        MetricWarehouseSource,
    ],
) -> CompiledQuery:
    if not isinstance(
        plan,
        MetricExecutionPlan,
    ):
        raise ValueError(
            "Metric execution plan required"
        )

    metric = MetricProposal.model_validate(
        plan.definition
    )

    if (
        metric.default_filters
        or any(
            node.filters
            for node
            in metric.formula.nodes
        )
    ):
        raise ValueError(
            "UNRESOLVED_FILTER"
        )

    (
        fact,
        dimension,
        aliases,
        join,
    ) = _resolve_sources(
        plan,
        metric,
        sources,
    )

    if (
        metric.grain == "ENTITY"
        and tuple(
            metric.grain_keys
        )
        != fact.grain_keys
    ):
        raise ValueError(
            "GRAIN_MISMATCH"
        )

    aggregate = _formula_expression(
        metric,
        fact,
        aliases[fact.dataset_id],
    )

    grouping = []

    for dimension_ref in metric.dimensions:
        source = sources.get(
            dimension_ref.dataset_id
        )

        if source is None:
            raise ValueError(
                "WAREHOUSE_SOURCE_REQUIRED"
            )

        expression, _ = _column(
            source,
            dimension_ref.dataset_id,
            dimension_ref.column,
            aliases[
                dimension_ref.dataset_id
            ],
        )

        grouping.append(
            expression
        )

    if metric.time_field is not None:
        # A1.1b deliberately keeps metric time semantics on
        # the FACT event calendar. Historical/as-of DIMENSION
        # time interpretation requires separate reviewed semantics.
        if (
            metric.time_field.dataset_id
            != fact.dataset_id
        ):
            raise ValueError(
                "TIME_SEMANTICS_UNRESOLVED"
            )

        grouping.append(
            _time_expression(
                fact,
                metric,
                aliases[fact.dataset_id],
            )
        )

    table = fact.physical_table

    from_sql = (
        f"FROM `{table}` AS f"
    )

    missing_dimension = "0"

    physical_tables = [
        table,
    ]

    if dimension is not None:
        parent_column = (
            join["parent"]["columns"][0]
        )

        child_column = (
            join["child"]["columns"][0]
        )

        parent_expression, _ = _column(
            dimension,
            dimension.dataset_id,
            parent_column,
            "d0",
        )

        child_expression, _ = _column(
            fact,
            fact.dataset_id,
            child_column,
            "f",
        )

        from_sql += (
            "\nLEFT JOIN "
            f"`{dimension.physical_table}` "
            "AS d0\n"
            f"  ON {child_expression} "
            f"= {parent_expression}\n"
            "  AND d0.is_current = TRUE"
        )

        missing_dimension = (
            "COUNTIF("
            "d0.member_sk IS NULL"
            ")"
        )

        physical_tables.append(
            dimension.physical_table
        )

    if not grouping:
        sql = (
            "SELECT\n"
            f"  {aggregate} "
            "AS __metric_value,\n"
            "  COUNT(*) "
            "AS __input_rows,\n"
            f"  {missing_dimension} "
            "AS __missing_dimension_rows\n"
            f"{from_sql}\n"
            "WHERE f.is_active = TRUE"
        )

    else:
        select_groups = ",\n".join(
            f"  {expression} "
            f"AS __group_{index}"
            for index, expression
            in enumerate(grouping)
        )

        group_by = ", ".join(
            grouping
        )

        order_by = ", ".join(
            f"__group_{index} "
            "ASC NULLS LAST"
            for index in range(
                len(grouping)
            )
        )

        missing_window = (
            "0"
            if dimension is None
            else (
                "SUM(COUNTIF("
                "d0.member_sk IS NULL"
                ")) OVER ()"
            )
        )

        sql = (
            "SELECT\n"
            f"{select_groups},\n"
            f"  {aggregate} "
            "AS __metric_value,\n"
            "  SUM(COUNT(*)) OVER () "
            "AS __input_rows,\n"
            "  COUNT(*) OVER () "
            "AS __total_groups,\n"
            f"  {missing_window} "
            "AS __missing_dimension_rows\n"
            f"{from_sql}\n"
            "WHERE f.is_active = TRUE\n"
            f"GROUP BY {group_by}\n"
            f"ORDER BY {order_by}\n"
            f"LIMIT {MAX_GROUPS + 1}"
        )

    warehouse_pins = [
        sources[dataset_id].pin()
        for dataset_id
        in sorted(sources)
    ]

    request_token = digest(
        [
            "datarise-metric-query-request",
            COMPILER_VERSION,
            plan.workspace_id,
            plan.candidate_id,
            plan.signature,
            warehouse_pins,
        ]
    )

    intent_sha256 = digest(
        [
            "datarise-metric-query-intent",
            COMPILER_VERSION,
            request_token,
            sql,
            physical_tables,
        ]
    )

    return CompiledQuery(
        workspace_id=plan.workspace_id,
        request_token=request_token,
        intent_sha256=intent_sha256,
        sql=sql,
        physical_tables=tuple(
            physical_tables
        ),
        compiler_version=COMPILER_VERSION,
    )
def decode_metric_query_result(
    plan: MetricExecutionPlan,
    result: QueryExecutionResult,
):
    if not isinstance(
        plan,
        MetricExecutionPlan,
    ):
        raise ValueError(
            "Metric execution plan required"
        )

    if not isinstance(
        result,
        QueryExecutionResult,
    ):
        raise ValueError(
            "Governed query result required"
        )

    metric = MetricProposal.model_validate(
        plan.definition
    )

    grouped = bool(
        metric.dimensions
        or metric.time_field
    )

    rows = [
        dict(row)
        for row in result.rows
    ]

    if not grouped:
        if len(rows) != 1:
            raise ValueError(
                "WAREHOUSE_RESULT_SHAPE"
            )

        row = rows[0]

        required = {
            "__metric_value",
            "__input_rows",
            "__missing_dimension_rows",
        }

        if set(row) != required:
            raise ValueError(
                "WAREHOUSE_RESULT_SHAPE"
            )

        input_rows = row[
            "__input_rows"
        ]

        missing = row[
            "__missing_dimension_rows"
        ]

        if (
            type(input_rows) is not int
            or input_rows < 0
            or type(missing) is not int
            or missing < 0
        ):
            raise ValueError(
                "WAREHOUSE_RESULT_SHAPE"
            )

        if missing:
            raise ValueError(
                "JOIN_COVERAGE"
            )

        output = {
            "kind": "scalar",
            "value": scalar(
                row["__metric_value"]
            ),
            "groups": [],
            "input_rows": input_rows,
            "total_groups": 0,
            "truncated": False,
            "display_hint": "scalar",
        }

    else:
        group_count = (
            len(metric.dimensions)
            + (
                1
                if metric.time_field
                is not None
                else 0
            )
        )

        expected = {
            "__metric_value",
            "__input_rows",
            "__total_groups",
            "__missing_dimension_rows",
            *{
                f"__group_{index}"
                for index
                in range(group_count)
            },
        }

        if not rows:
            output = {
                "kind": "grouped",
                "value": None,
                "groups": [],
                "input_rows": 0,
                "total_groups": 0,
                "truncated": False,
                "display_hint": (
                    "line"
                    if (
                        metric.time_field
                        and not metric.dimensions
                    )
                    else "table"
                ),
            }

        else:
            first = rows[0]

            if set(first) != expected:
                raise ValueError(
                    "WAREHOUSE_RESULT_SHAPE"
                )

            input_rows = first[
                "__input_rows"
            ]

            total_groups = first[
                "__total_groups"
            ]

            missing = first[
                "__missing_dimension_rows"
            ]

            if (
                type(input_rows) is not int
                or input_rows < 0
                or type(total_groups)
                is not int
                or total_groups < 0
                or type(missing) is not int
                or missing < 0
            ):
                raise ValueError(
                    "WAREHOUSE_RESULT_SHAPE"
                )

            for row in rows:
                if (
                    set(row) != expected
                    or row["__input_rows"]
                    != input_rows
                    or row["__total_groups"]
                    != total_groups
                    or row[
                        "__missing_dimension_rows"
                    ]
                    != missing
                ):
                    raise ValueError(
                        "WAREHOUSE_RESULT_SHAPE"
                    )

            if missing:
                raise ValueError(
                    "JOIN_COVERAGE"
                )

            if len(rows) > MAX_GROUPS + 1:
                raise ValueError(
                    "WAREHOUSE_RESULT_SHAPE"
                )

            groups = []

            for row in rows[
                :MAX_GROUPS
            ]:
                groups.append(
                    {
                        "dimensions": [
                            scalar(
                                row[
                                    f"__group_{index}"
                                ]
                            )
                            for index
                            in range(
                                group_count
                            )
                        ],
                        "value": scalar(
                            row[
                                "__metric_value"
                            ]
                        ),
                    }
                )

            output = {
                "kind": "grouped",
                "value": None,
                "groups": groups,
                "input_rows": input_rows,
                "total_groups": (
                    total_groups
                ),
                "truncated": (
                    total_groups
                    > MAX_GROUPS
                ),
                "display_hint": (
                    "line"
                    if (
                        metric.time_field
                        and not metric.dimensions
                    )
                    else "table"
                ),
            }

    if (
        len(
            json.dumps(
                output,
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        )
        > MAX_OUTPUT_BYTES
    ):
        raise ValueError(
            "OUTPUT_LIMIT"
        )

    return output