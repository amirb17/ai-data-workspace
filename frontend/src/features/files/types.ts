export type SourceFileType =
  | "CSV"
  | "EXCEL"

export type DatasetFileStatus =
  | "UPLOADING"
  | "INSPECTING"
  | "NEEDS_MAPPING"
  | "READY_TO_PROCESS"
  | "PROCESSING"
  | "PROCESSED"
  | "NEEDS_ATTENTION"
  | "FAILED"

export type DatasetFile = {
  acceptanceId?: string
  id: number
  datasetId: number
  workspaceId: number

  fileName: string
  fileType: SourceFileType
  sizeBytes: number

  status: DatasetFileStatus

  uploadedAt: string

  rowCount?: number
  columnCount?: number

  batchId?: number
}
export type CsvInspectionResult = {
  fileName: string
  sizeBytes: number
  fileType: "CSV"

  rowCount: number
  columnCount: number

  columns: string[]

  previewRows: Record<string, string>[]
}