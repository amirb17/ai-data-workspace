import { getDatasetContract } from "../datasets/contracts/storage"
import { compareSchema } from "../datasets/contracts/schemaMatch"
import { acceptDatasetFile, DuplicateFileContentError, findFileByContentHash } from "../files/data/storage"
import type { CsvInspectionResult } from "../files/types"
import { ensureBatchForAcceptedFile } from "./data/storage"

// Keep acceptance ordering and blocked-schema checks outside the UI.
export function acceptInspectedCsv(workspaceId: string, datasetId: string, inspection: CsvInspectionResult, acceptanceId: string) {
  if (!acceptanceId || inspection.fileType !== "CSV") throw new Error("A completed CSV inspection is required.")
  if (!Number.isSafeInteger(inspection.rowCount) || inspection.rowCount <= 0 ||
      !Number.isSafeInteger(inspection.columnCount) || inspection.columnCount <= 0 ||
      inspection.columnCount !== inspection.columns.length) throw new Error("The CSV inspection is incomplete.")
  if (!inspection.contentHash || !/^[a-f0-9]{64}$/.test(inspection.contentHash)) throw new Error("File content verification is required before acceptance.")
  const duplicate = findFileByContentHash(workspaceId, datasetId, inspection.contentHash)
  if (duplicate && duplicate.acceptanceId !== acceptanceId) throw new DuplicateFileContentError(duplicate)
  const contract = getDatasetContract(workspaceId, datasetId)
  const match = compareSchema(inspection.columns, contract)
  if (match.status !== "MATCH" && match.status !== "WARNING") {
    throw new Error(`This file cannot be accepted: ${match.status}. Review its dataset contract.`)
  }
  const files = acceptDatasetFile(workspaceId, datasetId, inspection, acceptanceId)
  const file = files.find((file) => file.acceptanceId === acceptanceId)
  if (!file) throw new Error("The accepted source file could not be found.")
  try {
    return ensureBatchForAcceptedFile(workspaceId, datasetId, file.id)
  } catch (caught) {
    const reason = caught instanceof Error ? caught.message : "Browser storage is unavailable."
    throw new Error(`The source file was saved, but its ingestion batch could not be saved. Retry Accept File to finish without duplicating the file. ${reason}`, { cause: caught })
  }
}
