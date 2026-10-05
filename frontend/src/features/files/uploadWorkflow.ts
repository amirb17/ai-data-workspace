import { completeUpload, initiateUpload, putCsv, type CompletedUpload, type UploadContext, type UploadSession } from "../../services/api/files"
import { getDatasetContract } from "../datasets/contracts/storage"
import { compareSchema } from "../datasets/contracts/schemaMatch"
import { DuplicateFileContentError, findFileByContentHash } from "./data/storage"
import { reconcileCompletedUpload } from "../ingestion/reconcileCompletedUpload"
import type { CsvInspectionResult } from "./types"
export type UploadState = "IDLE" | "INITIATING" | "UPLOADING" | "COMPLETING" | "SUCCESS" | "FAILED"
export type UploadFeedback = { state: UploadState; error?: string; isDuplicate?: boolean }
type Dependencies = { initiate: typeof initiateUpload; put: typeof putCsv; complete: typeof completeUpload; reconcile: typeof reconcileCompletedUpload }
const defaults: Dependencies = { initiate: initiateUpload, put: putCsv, complete: completeUpload, reconcile: reconcileCompletedUpload }

export function createUploadWorkflow(context: UploadContext, notify: (feedback: UploadFeedback) => void, deps: Dependencies = defaults) {
  const scope = { ...context }
  let session: UploadSession | undefined
  let uploaded = false
  let completed: CompletedUpload | undefined
  let selection: { file: File; inspection: CsvInspectionResult; acceptanceId: string } | undefined
  let busy = false
  let succeeded = false
  let controller = new AbortController()
  let disposed = false
  return {
    hasSession: () => !!session,
    activate() { if (disposed) { disposed = false; controller = new AbortController() } },
    dispose() { disposed = true; controller.abort(); session = undefined },
    reset() {
      if (busy) return
      controller.abort(); controller = new AbortController()
      session = undefined; uploaded = false; completed = undefined; selection = undefined; succeeded = false
      notify({ state: "IDLE" })
    },
    async accept(file: File, inspection: CsvInspectionResult, acceptanceId: string): Promise<boolean> {
      if (busy || succeeded || disposed) return false
      busy = true
      let stage = "Validation"
      const signal = controller.signal
      try {
        if (!selection) selection = { file, inspection, acceptanceId }
        if (selection.file !== file || selection.acceptanceId !== acceptanceId) throw new Error("Retry the original selected file or close this upload.")
        if (!session) {
          const duplicate = inspection.contentHash ? findFileByContentHash(scope.workspaceId, scope.datasetId, inspection.contentHash) : undefined
          if (duplicate) throw new DuplicateFileContentError(duplicate)
          const match = compareSchema(inspection.columns, getDatasetContract(scope.workspaceId, scope.datasetId))
          if (match.status !== "MATCH" && match.status !== "WARNING") throw new Error(`Upload blocked: ${match.status}. Review the dataset contract.`)
          stage = "Preparing upload"
          notify({ state: "INITIATING" })
          session = await deps.initiate(scope, file, signal)
          signal.throwIfAborted()
        }
        if (!uploaded) {
          stage = "Secure storage upload"
          notify({ state: "UPLOADING" })
          await deps.put(session.putUrl, file, signal)
          signal.throwIfAborted()
          uploaded = true
          session.putUrl = "" // No URL is needed for completion retries.
        }
        stage = "Finalizing upload"
        notify({ state: "COMPLETING" })
        if (!completed) completed = await deps.complete(scope, session.uploadRequestId, signal)
        signal.throwIfAborted()
        stage = "Upload completed on backend, but local metadata could not be saved. Retry to reconcile the same upload"
        deps.reconcile(scope, completed, inspection, acceptanceId)
        succeeded = true
        notify({ state: "SUCCESS", isDuplicate: completed.isDuplicate })
        return true
      } catch (error) {
        if (signal.aborted || disposed) return false
        notify({ state: "FAILED", error: `${stage}: ${error instanceof Error ? error.message : "Please retry."}` })
        return false
      } finally { busy = false }
    },
  }
}
