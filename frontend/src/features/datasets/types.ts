  export type DatasetStatus =
  | "ACTIVE"
  | "ARCHIVED"
  | "EMPTY"
  | "READY"
  | "PROCESSING"
  | "FAILED"
  | "NEEDS_ATTENTION"

export type DatasetListItem = {
  id: number
  name: string
  description: string
  status: DatasetStatus
  latestVersion?: number
  rowCount?: number
  columnCount?: number
  updatedAt: string
}