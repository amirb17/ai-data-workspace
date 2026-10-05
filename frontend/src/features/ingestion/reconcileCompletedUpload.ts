import type { CompletedUpload, UploadContext } from "../../services/api/files"
import type { CsvInspectionResult, DatasetFile } from "../files/types"
import { saveCompletedFile, readDatasetFiles } from "../files/data/storage"
import { saveCompletedBatch, readDatasetBatches } from "./data/storage"
import type { IngestionBatch } from "./types"

// The durable backend completion is the only source of physical/logical identities.
export function reconcileCompletedUpload(context: UploadContext, completed: CompletedUpload, inspection: CsvInspectionResult, acceptanceId: string) {
  const { workspaceId, datasetId } = context
  if (completed.workspaceId !== Number(workspaceId) || completed.datasetId !== Number(datasetId) || completed.userId !== context.userId) throw new Error("Completed upload belongs to another scope.")
  const files = readDatasetFiles(workspaceId, datasetId)
  const batches = readDatasetBatches(workspaceId, datasetId)
  const prior = files.find((item) => item.id === completed.file.id)
  if (prior && (prior.contentHash !== completed.file.hash || prior.sizeBytes !== completed.file.sizeBytes)) throw new Error("Canonical physical file metadata changed unexpectedly.")
  const file: DatasetFile = {
    id: completed.file.id, workspaceId: completed.workspaceId, datasetId: completed.datasetId,
    acceptanceId, fileName: completed.file.name, fileType: "CSV", sizeBytes: completed.file.sizeBytes,
    contentHash: completed.file.hash, status: completed.file.status, uploadedAt: prior?.uploadedAt ?? completed.createdAt,
    rowCount: inspection.rowCount, columnCount: inspection.columnCount, isDuplicate: completed.isDuplicate,
    uploadRequestIds: [...new Set([...(prior?.uploadRequestIds ?? []), completed.uploadRequestId])],
  }
  const batch: IngestionBatch = {
    id: `upload-${completed.uploadRequestId}`, uploadRequestId: completed.uploadRequestId,
    workspaceId: completed.workspaceId, datasetId: completed.datasetId, sourceFileId: file.id,
    sourceFileName: inspection.fileName, rowCount: inspection.rowCount, columnCount: inspection.columnCount,
    createdAt: completed.createdAt, status: "READY_TO_PROCESS",
    validRows: null, rejectedRows: null, duplicateRows: null, updatedRows: null,
  }
  const existing = batches.find((item) => item.uploadRequestId === completed.uploadRequestId)
  if (existing && (existing.sourceFileId !== file.id || existing.rowCount !== batch.rowCount || existing.columnCount !== batch.columnCount)) throw new Error("Upload batch lineage mismatch.")
  // File first. If the second write fails, retry reuses the completed backend result.
  saveCompletedFile(workspaceId, datasetId, file)
  return existing ?? saveCompletedBatch(workspaceId, datasetId, batch)
}
