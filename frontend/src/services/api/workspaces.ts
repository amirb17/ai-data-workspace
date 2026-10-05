import type { WorkspaceListItem } from "../../features/workspaces/types"
import { backendId, request } from "./client"
type WorkspaceResponse = { workspace_id: number; workspace_name: string; description?: string | null; status: "ACTIVE" | "ARCHIVED"; updated_at?: string }
export function mapWorkspace(value: WorkspaceResponse): WorkspaceListItem {
  return { id: backendId(value.workspace_id), name: value.workspace_name, description: value.description ?? "", status: value.status, updatedAt: value.updated_at ?? "Unavailable" }
}
export async function listWorkspaces(signal?: AbortSignal) {
  const result = await request<{ workspaces: WorkspaceResponse[] }>("/workspaces", { signal })
  return result.workspaces.map(mapWorkspace)
}
export async function getWorkspace(id: string, signal?: AbortSignal) {
  const result = mapWorkspace(await request<WorkspaceResponse>(`/workspaces/${backendId(id)}`, { signal }))
  if (String(result.id) !== id) throw new Error("Workspace ownership response mismatch.")
  return result
}
export async function createWorkspace(input: { name: string; description: string }) {
  return mapWorkspace(await request<WorkspaceResponse>("/workspaces", { method: "POST", body: JSON.stringify({ workspace_name: input.name, description: input.description }) }))
}
