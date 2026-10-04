import type { DatasetContract } from "./types"

export type SchemaMatchStatus =
  | "NO_CONTRACT"
  | "MATCH"
  | "WARNING"
  | "BREAKING"
  | "WRONG_DATASET_LIKELY"

export type SchemaMatchResult = {
  status: SchemaMatchStatus

  matchPercentage: number

  matchedColumns: string[]
  missingRequiredColumns: string[]
  unexpectedColumns: string[]
}

function normalizeColumn(
  value: string,
) {
  return value
    .trim()
    .toLowerCase()
}

export function compareSchema(
  incomingColumns: string[],
  contract?: DatasetContract,
): SchemaMatchResult {
  if (!contract) {
    return {
      status: "NO_CONTRACT",
      matchPercentage: 0,
      matchedColumns: [],
      missingRequiredColumns: [],
      unexpectedColumns:
        incomingColumns,
    }
  }

  const incoming =
    incomingColumns.map(
      normalizeColumn,
    )

  const expected =
    contract.columns.map(
      (column) =>
        normalizeColumn(
          column.name,
        ),
    )

  const required =
    contract.columns
      .filter(
        (column) =>
          column.required || contract.primaryKey.some((key) => normalizeColumn(key) === normalizeColumn(column.name)),
      )
      .map(
        (column) =>
          normalizeColumn(
            column.name,
          ),
      )

  const matchedColumns =
    expected.filter(
      (column) =>
        incoming.includes(
          column,
        ),
    )

  const missingRequiredColumns =
    required.filter(
      (column) =>
        !incoming.includes(
          column,
        ),
    )

  const unexpectedColumns =
    incoming.filter(
      (column) =>
        !expected.includes(
          column,
        ),
    )

  const matchPercentage =
    expected.length === 0
      ? 0
      : Math.round(
          (
            matchedColumns.length /
            expected.length
          ) * 100,
        )

  let status: SchemaMatchStatus =
    "MATCH"

  if (
    matchPercentage < 40
  ) {
    status =
      "WRONG_DATASET_LIKELY"
  } else if (
    missingRequiredColumns.length >
    0 || (contract.schemaEvolutionPolicy === "STRICT" && unexpectedColumns.length > 0)
  ) {
    status = "BREAKING"
  } else if (
    unexpectedColumns.length >
    0 || matchedColumns.length !== expected.length
  ) {
    status = "WARNING"
  }

  return {
    status,
    matchPercentage,
    matchedColumns,
    missingRequiredColumns,
    unexpectedColumns,
  }
}