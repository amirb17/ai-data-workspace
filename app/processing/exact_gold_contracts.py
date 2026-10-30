"""Deterministic physical contracts for Exact Decimal Gold.

Pure planning only. Does not publish artifacts or approve
aggregation policies.
"""

from app.processing.silver_transformer import exact_decimal_spec


def plan_exact_gold_contracts(
    artifacts,
    approved_rules,
    avg_scales,
):
    if not artifacts or not isinstance(avg_scales, dict):
        raise ValueError("Invalid Exact Gold planning input")

    sources = {}

    for rule in approved_rules:
        if rule[3] != "DATA_TYPE":
            continue

        spec = exact_decimal_spec(rule[4])

        if spec is None:
            continue

        column = rule[2]

        if column in sources:
            raise ValueError("Duplicate Exact Decimal source rule")

        sources[column] = {
            "precision": spec.precision,
            "scale": spec.scale,
        }

    if not sources:
        raise ValueError("No approved Exact Decimal source rules")

    if set(avg_scales) != set(sources):
        raise ValueError(
            "Explicit AVG scale required for every "
            "Exact Decimal source"
        )

    for column, scale in avg_scales.items():
        source = sources[column]

        if (
            type(scale) is not int
            or scale < source["scale"]
            or scale > 76
        ):
            raise ValueError("Invalid Exact Decimal AVG scale")

        average_precision = (
            source["precision"]
            - source["scale"]
            + scale
        )

        if average_precision > 76:
            raise ValueError(
                "Gold AVG precision exceeds Decimal256"
            )

    base_frame, base_metadata = artifacts[0]

    if base_metadata["artifact_type"] != "BASE":
        raise ValueError("First Gold artifact must be BASE")

    if not set(sources).issubset(base_frame.columns):
        raise ValueError("Approved Decimal source missing from BASE")

    state_rows = len(base_frame)

    # ceil(log10(n)) using integers only.
    # For n=0 or n=1, no extra SUM digit is required.
    growth_digits = (
        len(str(state_rows - 1))
        if state_rows > 1
        else 0
    )

    result = {}

    for frame, metadata in artifacts:
        artifact_name = metadata["artifact_name"]

        if artifact_name in result:
            raise ValueError("Duplicate Gold artifact identity")

        catalog = metadata["columns"]

        if [
            item["column_name"]
            for item in catalog
        ] != list(frame.columns):
            raise ValueError(
                "Gold artifact columns differ from catalog"
            )

        contract = {}

        for item in catalog:
            column = item["column_name"]
            source_column = item["source_column"]
            aggregation = item["aggregation_type"]

            if metadata["artifact_type"] == "BASE":
                if column in sources:
                    if source_column != column:
                        raise ValueError(
                            "Gold BASE source mapping differs"
                        )
                    contract[column] = sources[column].copy()
                continue

            if source_column not in sources:
                continue

            source = sources[source_column]
            precision = source["precision"]
            scale = source["scale"]

            if aggregation is None:
                # Exact Decimal used as a dimension.
                contract[column] = source.copy()

            elif aggregation == "SUM":
                output_precision = precision + growth_digits

                if output_precision > 76:
                    raise ValueError(
                        "Gold SUM precision exceeds Decimal256"
                    )

                contract[column] = {
                    "precision": output_precision,
                    "scale": scale,
                }

            elif aggregation == "MEAN":
                output_scale = avg_scales[source_column]

                contract[column] = {
                    "precision": (
                        precision - scale + output_scale
                    ),
                    "scale": output_scale,
                }

            elif aggregation in ("MIN", "MAX"):
                contract[column] = source.copy()

            else:
                raise ValueError(
                    "Unsupported Exact Decimal aggregation"
                )

        if not contract:
            raise ValueError(
                "Gold artifact has no Exact Decimal contract"
            )

        result[artifact_name] = contract

    return {
        "contract_version": 2,
        "state_rows": state_rows,
        "approved_sources": sources,
        "avg_scales": dict(avg_scales),
        "artifacts": result,
    }