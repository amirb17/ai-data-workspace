import { datasetContractsMock } from "./contracts.mock"
import { datasetsByWorkspaceMock } from "../data/datasets.mock"
import type { DatasetContract } from "./types"

export function contractStorageKey(workspaceId: string, datasetId: string) {
  return `datarise-workspace-${workspaceId}-dataset-${datasetId}-contract`
}

function validContract(value: unknown, datasetId: string): value is DatasetContract {
  if (!value || typeof value !== "object") return false
  const contract = value as DatasetContract
  if (String(contract.datasetId) !== datasetId || typeof contract.datasetName !== "string" ||
      !["APPEND", "UPSERT", "SNAPSHOT"].includes(contract.loadMode) ||
      !Array.isArray(contract.columns) || !contract.columns.length ||
      !Array.isArray(contract.primaryKey)) return false
  const names = new Set<string>()
  for (const column of contract.columns) {
    if (!column || typeof column.name !== "string" || !column.name.trim() ||
        typeof column.required !== "boolean" ||
        !["STRING", "INTEGER", "DECIMAL", "DATE", "BOOLEAN"].includes(column.dataType)) return false
    const name = column.name.trim().toLowerCase()
    if (names.has(name)) return false
    names.add(name)
  }
  return contract.primaryKey.every((key) => typeof key === "string" && names.has(key.trim().toLowerCase()))
}

export function getDatasetContract(workspaceId: string, datasetId: string): DatasetContract | undefined {
  // Legacy dataset-only storage is ambiguous across workspaces; never reuse it.
  let stored: string | null
  try { stored = localStorage.getItem(contractStorageKey(workspaceId, datasetId)) }
  catch { throw new Error("Unable to read the local dataset contract. Enable browser storage and retry.") }
  if (stored) {
    let value: unknown
    try { value = JSON.parse(stored) } catch { throw new Error("The saved dataset contract is invalid. Review local contract storage before continuing.") }
    if (!validContract(value, datasetId)) throw new Error("The saved dataset contract is invalid. Review local contract storage before continuing.")
    return value
  }
  const isMockDataset = datasetsByWorkspaceMock[workspaceId]?.some((dataset) => String(dataset.id) === datasetId)
  return isMockDataset ? datasetContractsMock[datasetId] : undefined
}
