import type { DatasetListItem } from "../../features/datasets/types"
import { backendId, request } from "./client"
type DatasetResponse = { dataset_id: number; workspace_id: number; dataset_name: string; description?: string | null; status: "ACTIVE" | "ARCHIVED"; updated_at?: string }
export function mapDataset(value: DatasetResponse, workspaceId: string): DatasetListItem {
  if (backendId(value.workspace_id) !== backendId(workspaceId)) throw new Error("Dataset belongs to another workspace.")
  return { id: backendId(value.dataset_id), name: value.dataset_name, description: value.description ?? "", status: value.status, updatedAt: value.updated_at ?? "Unavailable" }
}
export async function listDatasets(workspaceId: string, signal?: AbortSignal) {
  const result = await request<{ workspace_id: number; datasets: DatasetResponse[] }>(`/workspaces/${backendId(workspaceId)}/datasets`, { signal })
  if (result.workspace_id !== backendId(workspaceId)) throw new Error("Workspace response mismatch.")
  return result.datasets.map((item) => mapDataset(item, workspaceId))
}
export async function getDataset(workspaceId: string, datasetId: string, signal?: AbortSignal) {
  const result = mapDataset(await request<DatasetResponse>(`/workspaces/${backendId(workspaceId)}/datasets/${backendId(datasetId)}`, { signal }), workspaceId)
  if (result.id !== backendId(datasetId)) throw new Error("Dataset response mismatch.")
  return result
}
export async function createDataset(workspaceId: string, input: { name: string; description: string }) {
  return mapDataset(await request<DatasetResponse>(`/workspaces/${backendId(workspaceId)}/datasets`, { method: "POST", body: JSON.stringify({ dataset_name: input.name, description: input.description }) }), workspaceId)
}
