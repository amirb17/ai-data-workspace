import { datasetsByWorkspaceMock } from "./datasets.mock"
import type { DatasetListItem } from "../types"

export function datasetStorageKey(workspaceId: string) {
  return `datarise-workspace-${workspaceId}-datasets`
}

export function readWorkspaceDatasets(workspaceId: string): DatasetListItem[] {
  const fallback = datasetsByWorkspaceMock[workspaceId] ?? []
  try {
    const saved = localStorage.getItem(datasetStorageKey(workspaceId))
    if (!saved) return fallback
    const value: unknown = JSON.parse(saved)
    if (!Array.isArray(value) || !value.every((item) => item &&
      Number.isSafeInteger(item.id) && item.id > 0 &&
      typeof item.name === "string" && typeof item.description === "string" &&
      ["EMPTY", "READY", "PROCESSING", "FAILED", "NEEDS_ATTENTION"].includes(item.status) &&
      Number.isFinite(item.latestVersion) && Number.isFinite(item.rowCount) &&
      Number.isFinite(item.columnCount) && typeof item.updatedAt === "string")) return fallback
    return value
  } catch { return fallback }
}
