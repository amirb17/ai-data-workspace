export const ingestionBatchStatuses = ["READY_TO_PROCESS", "PROCESSING", "SUCCESS", "SUCCESS_WITH_WARNINGS", "FAILED"] as const
export type IngestionBatchStatus = typeof ingestionBatchStatuses[number]
export type IngestionBatch = {
  id: string
  workspaceId: number
  datasetId: number
  sourceFileId: number
  sourceFileName: string
  rowCount: number
  columnCount: number
  createdAt: string
  status: IngestionBatchStatus
  batchLabel?: string
  period?: string
  validRows: number | null
  rejectedRows: number | null
  duplicateRows: number | null
  updatedRows: number | null
}
