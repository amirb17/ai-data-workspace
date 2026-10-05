import { scopedStorageKey } from "../../../services/localScope"
import { readDatasetFiles } from "../../files/data/storage"
import { ingestionBatchStatuses, type IngestionBatch } from "../types"

export function batchStorageKey(workspaceId: string, datasetId: string) {
  return scopedStorageKey(workspaceId, datasetId, "batches")
}

function validBatch(value: unknown): value is IngestionBatch {
  if (!value || typeof value !== "object") return false
  const batch = value as IngestionBatch
  const count = (value: unknown) => typeof value === "number" && Number.isSafeInteger(value) && value >= 0
  return typeof batch.id === "string" && !!batch.id &&
    Number.isSafeInteger(batch.workspaceId) && batch.workspaceId > 0 &&
    Number.isSafeInteger(batch.datasetId) && batch.datasetId > 0 &&
    Number.isSafeInteger(batch.sourceFileId) && batch.sourceFileId > 0 &&
    (batch.uploadRequestId === undefined || (Number.isSafeInteger(batch.uploadRequestId) && batch.uploadRequestId > 0)) &&
    typeof batch.sourceFileName === "string" && !!batch.sourceFileName &&
    count(batch.rowCount) && count(batch.columnCount) &&
    typeof batch.createdAt === "string" && Number.isFinite(Date.parse(batch.createdAt)) &&
    ingestionBatchStatuses.includes(batch.status) &&
    [batch.validRows, batch.rejectedRows, batch.duplicateRows, batch.updatedRows].every((value) => value === null || count(value)) &&
    (batch.batchLabel === undefined || typeof batch.batchLabel === "string") &&
    (batch.period === undefined || typeof batch.period === "string") &&
    (batch.status !== "READY_TO_PROCESS" || [batch.validRows, batch.rejectedRows, batch.duplicateRows, batch.updatedRows].every((value) => value === null))
}

export function readDatasetBatches(workspaceId: string, datasetId: string): IngestionBatch[] {
  const stored = localStorage.getItem(batchStorageKey(workspaceId, datasetId))
  if (stored === null) return []
  let value: unknown
  try { value = JSON.parse(stored) } catch { throw new Error("The saved ingestion batch list is invalid.") }
  if (!Array.isArray(value) || !value.every(validBatch)) throw new Error("The saved ingestion batch list is invalid.")
  if (value.some((batch) => String(batch.workspaceId) !== workspaceId || String(batch.datasetId) !== datasetId)) {
    throw new Error("The saved batches belong to a different workspace or dataset.")
  }
  if (new Set(value.map((batch) => batch.id)).size !== value.length ||
      new Set(value.map((batch) => batch.uploadRequestId === undefined ? `file-${batch.sourceFileId}` : `upload-${batch.uploadRequestId}`)).size !== value.length) {
    throw new Error("The saved ingestion batch list contains duplicate identities.")
  }
  return value
}

// Called only after contract validation and successful source-file persistence.
export function ensureBatchForAcceptedFile(workspaceId: string, datasetId: string, sourceFileId: number): IngestionBatch {
  const file = readDatasetFiles(workspaceId, datasetId).find((file) => file.id === sourceFileId)
  if (!file || !file.acceptanceId || file.fileType !== "CSV" || file.status !== "READY_TO_PROCESS" ||
      file.rowCount === undefined || file.columnCount === undefined) {
    throw new Error("A persisted accepted CSV is required to create an ingestion batch.")
  }
  const batches = readDatasetBatches(workspaceId, datasetId)
  const existing = batches.find((batch) => batch.sourceFileId === sourceFileId)
  if (existing) {
    if (existing.sourceFileName !== file.fileName || existing.rowCount !== file.rowCount || existing.columnCount !== file.columnCount) {
      throw new Error("The existing batch does not match its source file.")
    }
    return existing
  }
  const batch: IngestionBatch = {
    id: `batch-${sourceFileId}`,
    workspaceId: file.workspaceId,
    datasetId: file.datasetId,
    sourceFileId: file.id,
    sourceFileName: file.fileName,
    rowCount: file.rowCount,
    columnCount: file.columnCount,
    createdAt: new Date().toISOString(),
    status: "READY_TO_PROCESS",
    validRows: null, rejectedRows: null, duplicateRows: null, updatedRows: null,
  }
  if (!validBatch(batch)) throw new Error("The source file has invalid batch metadata.")
  localStorage.setItem(batchStorageKey(workspaceId, datasetId), JSON.stringify([batch, ...batches]))
  window.dispatchEvent(new Event("datarise-batches-changed"))
  return batch
}

export function saveCompletedBatch(workspaceId: string, datasetId: string, batch: IngestionBatch) {
  if (!validBatch(batch) || !batch.uploadRequestId || String(batch.workspaceId) !== workspaceId || String(batch.datasetId) !== datasetId) throw new Error("Invalid completed batch metadata or ownership.")
  const file = readDatasetFiles(workspaceId, datasetId).find((item) => item.id === batch.sourceFileId)
  if (!file || file.status !== "UPLOADED" || !file.uploadRequestIds?.includes(batch.uploadRequestId)) throw new Error("A persisted completed upload is required for this batch.")
  const batches = readDatasetBatches(workspaceId, datasetId)
  const existing = batches.find((item) => item.uploadRequestId === batch.uploadRequestId)
  if (existing) return existing
  localStorage.setItem(batchStorageKey(workspaceId, datasetId), JSON.stringify([batch, ...batches]))
  window.dispatchEvent(new Event("datarise-batches-changed"))
  return batch
}
