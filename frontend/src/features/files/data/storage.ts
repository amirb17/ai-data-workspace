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
    (file.acceptanceId === undefined || typeof file.acceptanceId === "string")
}

export function acceptDatasetFile(
  workspaceId: string,
  datasetId: string,
  inspection: CsvInspectionResult,
  acceptanceId: string,
): DatasetFile[] {
  const files = readDatasetFiles(workspaceId, datasetId)
  if (files.some((file) => file.acceptanceId === acceptanceId)) return files
  const workspace = Number(workspaceId)
  const dataset = Number(datasetId)
  if (!Number.isSafeInteger(workspace) || workspace <= 0 || !Number.isSafeInteger(dataset) || dataset <= 0) {
    throw new Error("A valid workspace and dataset are required to accept this file.")
  }
  const file: DatasetFile = {
    id: files.reduce((id, item) => Math.max(id, item.id + 1), Date.now()),
    acceptanceId,
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
  return next
}
