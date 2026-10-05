import { filesByDatasetMock } from "./files.mock"
import type { CsvInspectionResult, DatasetFile } from "../types"

export function fileStorageKey(workspaceId: string, datasetId: string) {
  return `datarise-workspace-${workspaceId}-dataset-${datasetId}-files`
}

export function readDatasetFiles(workspaceId: string, datasetId: string): DatasetFile[] {
  const seed = filesByDatasetMock[datasetId]?.filter((file) =>
    String(file.workspaceId) === workspaceId && String(file.datasetId) === datasetId,
  ) ?? []
  const stored = localStorage.getItem(fileStorageKey(workspaceId, datasetId))
  if (stored === null) return seed
  const value: unknown = JSON.parse(stored)
  if (!Array.isArray(value) || !value.every(isDatasetFile)) {
    throw new Error("The saved file list is invalid. Review local file storage before accepting another file.")
  }
  return value.filter((file) => String(file.workspaceId) === workspaceId && String(file.datasetId) === datasetId)
}

export class DuplicateFileContentError extends Error {
  readonly existingFile: DatasetFile
  constructor(existingFile: DatasetFile) {
    super("This file has already been added to this dataset.")
    this.name = "DuplicateFileContentError"
    this.existingFile = existingFile
  }
}

export function findFileByContentHash(workspaceId: string, datasetId: string, contentHash: string): DatasetFile | undefined {
  return readDatasetFiles(workspaceId, datasetId).find((file) => file.contentHash === contentHash)
}

function isDatasetFile(value: unknown): value is DatasetFile {
  if (!value || typeof value !== "object") return false
  const file = value as DatasetFile
  return Number.isSafeInteger(file.id) && Number.isSafeInteger(file.workspaceId) &&
    Number.isSafeInteger(file.datasetId) && typeof file.fileName === "string" &&
    ["CSV", "EXCEL"].includes(file.fileType) && Number.isFinite(file.sizeBytes) &&
    file.sizeBytes >= 0 && typeof file.uploadedAt === "string" &&
    ["UPLOADING", "INSPECTING", "NEEDS_MAPPING", "READY_TO_PROCESS", "PROCESSING", "PROCESSED", "NEEDS_ATTENTION", "FAILED"].includes(file.status) &&
    (file.rowCount === undefined || (Number.isInteger(file.rowCount) && file.rowCount >= 0)) &&
    (file.columnCount === undefined || (Number.isInteger(file.columnCount) && file.columnCount >= 0)) &&
    (file.contentHash === undefined || (typeof file.contentHash === "string" && /^[a-f0-9]{64}$/.test(file.contentHash))) &&
    (file.acceptanceId === undefined || typeof file.acceptanceId === "string")
}

export function acceptDatasetFile(
  workspaceId: string,
  datasetId: string,
  inspection: CsvInspectionResult,
  acceptanceId: string,
): DatasetFile[] {
  const files = readDatasetFiles(workspaceId, datasetId)
  if (inspection.contentHash !== undefined && !/^[a-f0-9]{64}$/.test(inspection.contentHash)) throw new Error("Invalid file content hash.")
  const accepted = files.find((file) => file.acceptanceId === acceptanceId)
  if (accepted) {
    if (accepted.contentHash !== inspection.contentHash) throw new Error("This acceptance identity belongs to different file content.")
    return files
  }
  const duplicate = inspection.contentHash ? findFileByContentHash(workspaceId, datasetId, inspection.contentHash) : undefined
  if (duplicate) throw new DuplicateFileContentError(duplicate)
  const workspace = Number(workspaceId)
  const dataset = Number(datasetId)
  if (!Number.isSafeInteger(workspace) || workspace <= 0 || !Number.isSafeInteger(dataset) || dataset <= 0) {
    throw new Error("A valid workspace and dataset are required to accept this file.")
  }
  const file: DatasetFile = {
    id: files.reduce((id, item) => Math.max(id, item.id + 1), Date.now()),
    acceptanceId,
    contentHash: inspection.contentHash,
    workspaceId: workspace,
    datasetId: dataset,
    fileName: inspection.fileName,
    fileType: inspection.fileType,
    sizeBytes: inspection.sizeBytes,
    rowCount: inspection.rowCount,
    columnCount: inspection.columnCount,
    uploadedAt: new Date().toISOString(),
    status: "READY_TO_PROCESS",
  }
  const next = [file, ...files]
  localStorage.setItem(fileStorageKey(workspaceId, datasetId), JSON.stringify(next))
  window.dispatchEvent(new Event("datarise-files-changed"))
  return next
}
