export type WorkspaceStat = {
  id: string
  label: string
  value: number
  helper: string
  status?: "success" | "warning" | "error" | "neutral"
}

export type DatasetStatus =
  | "READY"
  | "PROCESSING"
  | "FAILED"
  | "NEEDS_ATTENTION"

export type RecentDataset = {
  id: number
  name: string
  status: DatasetStatus
  rows: number
  columns: number
  updatedAt: string
}

export type ProcessingStageStatus =
  | "COMPLETED"
  | "RUNNING"
  | "PENDING"
  | "FAILED"

export type ProcessingStage = {
  id: string
  label: string
  description: string
  status: ProcessingStageStatus
}

export type HomeDashboardData = {
  stats: WorkspaceStat[]
  recentDatasets: RecentDataset[]
  processingStages: ProcessingStage[]
}