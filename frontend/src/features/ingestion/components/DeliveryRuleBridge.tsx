import { useRef, useState } from "react"
import { Link } from "react-router-dom"
import { startBronze } from "../../../services/api/rules"
import { useProcessingContext } from "../useProcessingContext"
import { IngestionBatchCard } from "./IngestionBatchCard"
import type { IngestionBatch } from "../types"
export function DeliveryRuleBridge({ batch, workspaceId, datasetId }: { batch: IngestionBatch; workspaceId: string; datasetId: string }) {
  const { context, error, reload } = useProcessingContext(workspaceId, datasetId, batch.uploadRequestId!)
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState("")
  const active = useRef(false)
  const prepare = async () => {
    if (active.current) return
    active.current = true; setBusy(true); setActionError("")
    try { await startBronze(workspaceId, datasetId, batch.uploadRequestId!); reload() }
    catch (caught) { setActionError(caught instanceof Error ? caught.message : "Bronze failed."); reload() }
    finally { active.current = false; setBusy(false) }
  }
  const status = busy ? "Preparing Bronze request" : context?.status
  return <section className="space-y-3">
    <IngestionBatchCard batch={batch} processingStatus={status ?? "Loading backend state"} />
    {(error || actionError) && <p role="alert" className="break-words text-sm text-red-700">{actionError || error}</p>}
    <div className="flex flex-wrap gap-3 text-sm">
      {context && ["READY_TO_PROCESS", "BRONZE_FAILED"].includes(context.status) && <button className="min-h-10 rounded-lg bg-indigo-600 px-4 text-white disabled:opacity-50" disabled={busy} onClick={() => void prepare()}>{busy ? "Preparing Bronze…" : context.status === "BRONZE_FAILED" ? "Retry Bronze" : "Prepare Rules (Bronze)"}</button>}
      {context?.dataset_version_file_id && <Link className="inline-flex min-h-10 items-center rounded-lg border border-indigo-200 px-4 text-indigo-700" to={`/app/workspaces/${workspaceId}/datasets/${datasetId}/rules?upload=${batch.uploadRequestId}`}>{context.rule_state === "DRAFT" ? "Review Rules" : "View Finalized Rules"}</Link>}
      <button className="min-h-10 rounded-lg border border-slate-200 px-4" disabled={busy} onClick={reload}>Reload State</button>
    </div>
    {context?.status === "AWAITING_RULES" && <p className="text-sm text-amber-700">Rules required. Review and explicitly approve the backend questions.</p>}
    {context?.silver_can_proceed && <p className="text-sm text-emerald-700">Rules ready (version {context.rule_version}). Silver prerequisites are satisfied; execution is available in a later phase.</p>}
    {context?.rules_reused && <p className="text-sm text-emerald-700">Bronze ✓ · Rules ✓ · Approved rule version {context.rule_version} reused for this delivery.</p>}
  </section>
}
