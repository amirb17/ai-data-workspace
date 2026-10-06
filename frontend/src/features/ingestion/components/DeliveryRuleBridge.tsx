import { useRef, useState } from "react"
import { Link } from "react-router-dom"
import { startBronze, continueProcessing, type ProcessingContext } from "../../../services/api/rules"
import { ProcessingDetails } from "./ProcessingDetails"
import { executionAction } from "../processingState"
import { IngestionBatchCard, type BatchPresentation } from "./IngestionBatchCard"
export function DeliveryRuleBridge({ batch, workspaceId, datasetId, context, reload, datasetBusy }: { batch: BatchPresentation; workspaceId: string; datasetId: string; context: ProcessingContext; reload: () => void; datasetBusy: boolean }) {
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState("")
  const active = useRef(false)
  const prepare = async (continueStages = false) => {
    if (active.current) return
    active.current = true; setBusy(true); setActionError("")
    try { await (continueStages ? continueProcessing : startBronze)(workspaceId, datasetId, batch.uploadRequestId!); reload() }
    catch (caught) { setActionError(caught instanceof Error ? caught.message : "Processing could not be completed."); reload() }
    finally { active.current = false; setBusy(false) }
  }
  const status = context?.status
  const action = context ? executionAction(context) : null
  return <section className="space-y-3">
    <IngestionBatchCard batch={batch} context={context} processingStatus={status ?? "Loading backend state"} />
    {actionError && <p role="alert" className="break-words text-sm text-red-700">{actionError}</p>}
    <div className="flex flex-wrap gap-3 text-sm">
      {context.status === "BRONZE_FAILED" && <button className="min-h-10 rounded-lg bg-indigo-600 px-4 text-white disabled:opacity-50" disabled={busy || datasetBusy} onClick={() => void prepare()}>{busy ? "Preparing Bronze…" : "Retry Bronze"}</button>}
      {action && <button className="min-h-10 rounded-lg bg-indigo-600 px-4 text-white disabled:opacity-50" disabled={busy || datasetBusy} onClick={() => void prepare(true)}>{busy ? "Processing request active…" : action}</button>}
      {context?.dataset_version_file_id && <Link className="inline-flex min-h-10 items-center rounded-lg border border-indigo-200 px-4 text-indigo-700" to={`/app/workspaces/${workspaceId}/datasets/${datasetId}/rules?upload=${batch.uploadRequestId}`}>{context.rule_state === "DRAFT" ? "Review Rules" : "View Finalized Rules"}</Link>}
      <button className="min-h-10 rounded-lg border border-slate-200 px-4" disabled={busy} onClick={reload}>Reload State</button>
      {context.quarantine_available && <Link className="inline-flex min-h-10 items-center rounded-lg border border-amber-200 px-4 text-amber-800" to={`/app/workspaces/${workspaceId}/datasets/${datasetId}/data-quality`}>View Data Quality</Link>}
    </div>
    {context?.status === "AWAITING_RULES" && <p className="text-sm text-amber-700">Rules required. Review and explicitly approve the backend questions.</p>}
    {context && <ProcessingDetails context={context} />}
  </section>
}
