import { useRef, useState } from "react"
import { Link } from "react-router-dom"
import { startBronze, continueProcessing, type ProcessingContext } from "../../../services/api/rules"
import { executionAction } from "../processingState"
import { IngestionBatchCard, type BatchPresentation } from "./IngestionBatchCard"

export function DeliveryRuleBridge({ batch, workspaceId, datasetId, context, reload, datasetBusy, onRequestActivity }: { batch: BatchPresentation; workspaceId: string; datasetId: string; context: ProcessingContext; reload: () => void; datasetBusy: boolean; onRequestActivity?: (started: boolean) => void }) {
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState("")
  const active = useRef(false)
  const retry = async (bronze = false) => {
    if (active.current || datasetBusy) return
    active.current = true; setBusy(true); setActionError("")
    onRequestActivity?.(true)
    try { await (bronze ? startBronze : continueProcessing)(workspaceId, datasetId, context.upload_request_id); reload() }
    catch (caught) { setActionError(caught instanceof Error ? caught.message : "Processing could not be completed."); reload() }
    finally { active.current = false; setBusy(false); onRequestActivity?.(false) }
  }
  const action = executionAction(context)
  const qualityHref = `/app/workspaces/${workspaceId}/datasets/${datasetId}/data-quality`
  const failed = context.status.endsWith("_FAILED")
  return <IngestionBatchCard batch={batch} context={context} qualityHref={qualityHref}>
    {context.status === "AWAITING_RULES" && <p className="rounded-lg bg-amber-50 p-3 text-sm text-amber-950">Bronze profiling is complete. Processing is waiting for your decisions before validation can continue.</p>}
    {failed && <div className="space-y-1 rounded-lg bg-red-50 p-3 text-sm text-red-800">
      <p>{context.error_summary ?? "Processing could not be completed. Review this delivery and retry."}</p>
      <p>{context.status === "GOLD_FAILED" ? "Silver output is available. Retrying Gold will reuse successful Silver processing." : "Your uploaded source file is safe. You do not need to upload it again."}</p>
    </div>}
    {context.stages.gold === "SKIPPED" && <p className="rounded-lg bg-amber-50 p-3 text-sm text-amber-950">No valid rows were available for analytics output.</p>}
    {(context.rejected_rows ?? 0) > 0 && <p className="text-sm text-amber-900">{context.rejected_rows?.toLocaleString()} {context.rejected_rows === 1 ? "row needs" : "rows need"} attention in quarantine.</p>}
    {actionError && <p role="alert" className="break-words text-sm text-red-700">{actionError}</p>}
    <div className="flex flex-wrap gap-2 text-sm">
      {context.status === "AWAITING_RULES" && context.dataset_version_file_id && <Link className="inline-flex min-h-11 items-center rounded-lg bg-indigo-600 px-4 font-medium text-white" to={`/app/workspaces/${workspaceId}/datasets/${datasetId}/rules?upload=${context.upload_request_id}`}>Review Rules</Link>}
      {context.status === "BRONZE_FAILED" && <button className="min-h-11 rounded-lg bg-indigo-600 px-4 font-medium text-white disabled:opacity-50" disabled={busy || datasetBusy} onClick={() => void retry(true)}>{busy ? "Retrying…" : "Retry Bronze"}</button>}
      {action && <button className="min-h-11 rounded-lg bg-indigo-600 px-4 font-medium text-white disabled:opacity-50" disabled={busy || datasetBusy} onClick={() => void retry()}>{busy ? "Processing request active…" : action}</button>}
      {(context.rejected_rows ?? 0) > 0 && <Link className="inline-flex min-h-11 items-center rounded-lg border border-amber-200 px-4 font-medium text-amber-900" to={qualityHref}>View Data Quality</Link>}
    </div>
  </IngestionBatchCard>
}
