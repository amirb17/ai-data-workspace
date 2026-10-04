export type WorkspaceStatus =
  | "ACTIVE"
  | "PROCESSING"
  | "NEEDS_ATTENTION"

export type WorkspaceListItem = {
  id: number
  name: string
  description: string
  status: WorkspaceStatus
  datasetCount: number
  analyticsReadyCount: number
  processingCount: number
  updatedAt: string
}