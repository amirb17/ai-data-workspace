import { columnDataTypes, datasetLoadModes, schemaEvolutionPolicies, type DatasetContract } from "./types"

export function validateContract(value: unknown): string | null {
  if (!value || typeof value !== "object") return "A dataset contract is required."
  const contract = value as DatasetContract
  if (!Number.isSafeInteger(contract.workspaceId) || contract.workspaceId <= 0 ||
      !Number.isSafeInteger(contract.datasetId) || contract.datasetId <= 0 ||
      typeof contract.datasetName !== "string" || !contract.datasetName.trim()) return "Valid workspace and dataset ownership are required."
  if (!datasetLoadModes.includes(contract.loadMode)) return "Choose a supported load mode."
  if (!schemaEvolutionPolicies.includes(contract.schemaEvolutionPolicy)) return "Choose a schema evolution policy."
  if (!Array.isArray(contract.columns) || !contract.columns.length) return "The contract must contain at least one column."
  const names = new Set<string>()
  for (const column of contract.columns) {
    if (!column || typeof column.name !== "string" || !column.name.trim()) return "Column names cannot be empty."
    const name = column.name.trim().toLowerCase()
    if (names.has(name)) return "Column names must be unique (ignoring case)."
    names.add(name)
    if (!columnDataTypes.includes(column.dataType) || typeof column.required !== "boolean") return "Each column needs a supported type and required state."
  }
  if (!Array.isArray(contract.primaryKey)) return "Choose valid business key columns."
  const keys = new Set<string>()
  for (const key of contract.primaryKey) {
    if (typeof key !== "string") return "Choose valid business key columns."
    const name = key.trim().toLowerCase()
    if (keys.has(name) || !names.has(name)) return "Business keys must be unique contract columns."
    keys.add(name)
    if (!contract.columns.find((column) => column.name.trim().toLowerCase() === name)?.required) return "Business key columns must be required."
  }
  if (["UPSERT","SNAPSHOT"].includes(contract.loadMode) && !keys.size) return `${contract.loadMode} requires at least one primary/business key.`
  return null
}

export function createInitialContract(workspaceId: string, datasetId: string, datasetName: string, columns: string[]): DatasetContract {
  return {
    workspaceId: Number(workspaceId), datasetId: Number(datasetId), datasetName,
    columns: columns.map((name) => ({ name, dataType: "STRING", required: false })),
    primaryKey: [], loadMode: "APPEND", schemaEvolutionPolicy: "ALLOW_ADDITIVE",
  }
}
