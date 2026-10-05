import { scopedStorageKey, hasBackendScope } from "../../../services/localScope"
import { datasetContractsMock } from "./contracts.mock"
import { datasetsByWorkspaceMock } from "../data/datasets.mock"
import type { DatasetContract } from "./types"
import { validateContract } from "./validation"

export function contractStorageKey(workspaceId: string, datasetId: string) {
  return scopedStorageKey(workspaceId, datasetId, "contract")
}

export function saveDatasetContract(workspaceId: string, datasetId: string, contract: DatasetContract) {
  const error = validateContract(contract)
  if (error) throw new Error(error)
  if (String(contract.workspaceId) !== workspaceId || String(contract.datasetId) !== datasetId) {
    throw new Error("The contract belongs to a different workspace or dataset.")
  }
  localStorage.setItem(contractStorageKey(workspaceId, datasetId), JSON.stringify(contract))
  window.dispatchEvent(new Event("datarise-contract-changed"))
}

export function getDatasetContract(workspaceId: string, datasetId: string): DatasetContract | undefined {
  // Dataset-only legacy keys cannot establish workspace ownership.
  const stored = localStorage.getItem(contractStorageKey(workspaceId, datasetId))
  if (stored !== null) {
    let value: unknown
    try { value = JSON.parse(stored) } catch { throw new Error("The saved dataset contract is invalid.") }
    if (!value || typeof value !== "object") throw new Error("The saved dataset contract is invalid.")
    const legacy = value as Partial<DatasetContract>
    // Only migrate the known Phase 1 shape from its already-scoped storage key.
    const contract = legacy.workspaceId === undefined && legacy.schemaEvolutionPolicy === undefined
      ? { ...legacy, workspaceId: Number(workspaceId), schemaEvolutionPolicy: "ALLOW_ADDITIVE" }
      : legacy
    const error = validateContract(contract)
    if (error || String(contract.workspaceId) !== workspaceId || String(contract.datasetId) !== datasetId) {
      throw new Error(error ?? "The saved contract belongs to a different workspace or dataset.")
    }
    return contract as DatasetContract
  }
  if (hasBackendScope()) return undefined
  const isMockDataset = datasetsByWorkspaceMock[workspaceId]?.some((dataset) => String(dataset.id) === datasetId)
  const contract = isMockDataset ? datasetContractsMock[datasetId] : undefined
  return contract && String(contract.workspaceId) === workspaceId ? contract : undefined
}
