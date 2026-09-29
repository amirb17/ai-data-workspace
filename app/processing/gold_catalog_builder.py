from dataclasses import asdict, dataclass

from app.processing.gold_planner import GoldArtifactPlan


@dataclass(frozen=True)
class GoldCatalogColumn:
    column_name: str
    column_role: str
    source_column: str | None
    aggregation_type: str | None
    ordinal_position: int
    data_type: str | None = None


@dataclass(frozen=True)
class GoldArtifactCatalog:
    artifact_name: str
    grain: str
    columns: list[GoldCatalogColumn]

    def to_dict(self) -> dict:
        return asdict(self)


def build_gold_artifact_catalog(
    plan: GoldArtifactPlan,
) -> GoldArtifactCatalog:
    """
    Build machine-readable semantic metadata for a Gold MART.

    The catalog describes the same analytical contract executed
    by gold_mart_builder.py.
    """

    if plan.artifact_type != "MART":
        raise ValueError(
            f"Unsupported artifact type for semantic catalog: "
            f"{plan.artifact_type}"
        )

    if not plan.dimensions:
        raise ValueError(
            f"Mart '{plan.artifact_name}' has no dimensions"
        )

    if not plan.measures:
        raise ValueError(
            f"Mart '{plan.artifact_name}' has no measures"
        )

    if not plan.aggregations:
        raise ValueError(
            f"Mart '{plan.artifact_name}' has no aggregations"
        )

    columns: list[GoldCatalogColumn] = []
    ordinal_position = 1

    # ---------------------------------------------------------
    # Dimensions
    # ---------------------------------------------------------
    for dimension in plan.dimensions:
        columns.append(
            GoldCatalogColumn(
                column_name=dimension,
                column_role="DIMENSION",
                source_column=dimension,
                aggregation_type=None,
                ordinal_position=ordinal_position,
            )
        )

        ordinal_position += 1

    # ---------------------------------------------------------
    # Aggregated measures
    #
    # This order intentionally mirrors pandas aggregation output:
    #
    # amount_sum
    # amount_mean
    # amount_min
    # amount_max
    # discount_sum
    # ...
    # ---------------------------------------------------------
    for measure in plan.measures:
        for aggregation in plan.aggregations:
            columns.append(
                GoldCatalogColumn(
                    column_name=(
                        f"{measure}_{aggregation}"
                    ),
                    column_role="MEASURE",
                    source_column=measure,
                    aggregation_type=aggregation.upper(),
                    ordinal_position=ordinal_position,
                )
            )

            ordinal_position += 1

    # ---------------------------------------------------------
    # Standard mart metric
    # ---------------------------------------------------------
    columns.append(
        GoldCatalogColumn(
            column_name="record_count",
            column_role="METRIC",
            source_column=None,
            aggregation_type="COUNT",
            ordinal_position=ordinal_position,
            data_type="int64",
        )
    )

    grain = ",".join(plan.dimensions)

    return GoldArtifactCatalog(
        artifact_name=plan.artifact_name,
        grain=grain,
        columns=columns,
    )

def validate_catalog_against_dataframe(
    catalog: GoldArtifactCatalog,
    df,
) -> None:
    """
    Ensure semantic catalog metadata exactly matches the
    physical dataframe produced for the Gold artifact.

    Validates:
        - column names
        - column ordering
    """

    catalog_columns = [
        column.column_name
        for column in catalog.columns
    ]

    dataframe_columns = list(df.columns)

    if catalog_columns != dataframe_columns:
        raise ValueError(
            "Gold semantic catalog does not match "
            f"physical artifact '{catalog.artifact_name}'. "
            f"Catalog columns={catalog_columns}, "
            f"DataFrame columns={dataframe_columns}"
        )

def enrich_catalog_data_types(
    catalog: GoldArtifactCatalog,
    df,
) -> GoldArtifactCatalog:
    """
    Return a new semantic catalog enriched with the actual
    physical pandas dtypes produced by the Gold artifact.

    The catalog must match the dataframe before enrichment.
    """

    validate_catalog_against_dataframe(
        catalog=catalog,
        df=df,
    )

    enriched_columns = []

    for column in catalog.columns:
        physical_dtype = str(
            df[column.column_name].dtype
        )

        enriched_columns.append(
            GoldCatalogColumn(
                column_name=column.column_name,
                column_role=column.column_role,
                source_column=column.source_column,
                aggregation_type=column.aggregation_type,
                ordinal_position=column.ordinal_position,
                data_type=physical_dtype,
            )
        )

    return GoldArtifactCatalog(
        artifact_name=catalog.artifact_name,
        grain=catalog.grain,
        columns=enriched_columns,
    )