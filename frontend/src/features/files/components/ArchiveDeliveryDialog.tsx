import { useRef, useState } from "react"
import { Button } from "../../../components/ui/Button"
import { useDialogFocus } from "../../../components/ui/useDialogFocus"
import { archiveDelivery } from "../../../services/api/deliveries"
import { archiveAction } from "../deliveryArchive"
import type { ProcessingContext } from "../../../services/api/rules"
export function ArchiveDeliveryDialog({workspaceId,datasetId,datasetName,fileName,context,onClose,onArchived}: {
  workspaceId:string; datasetId:string; datasetName:string; fileName:string; context:ProcessingContext; onClose:()=>void; onArchived:()=>void
}) {
  const [busy,setBusy] = useState(false), [error,setError] = useState("")
  const submitted = useRef(false)
  const ref = useDialogFocus(true,onClose,busy)
  const action = archiveAction(context)
  async function confirm() {
    if (submitted.current || action.blocked) return
    submitted.current = true; setBusy(true); setError("")
    try { await archiveDelivery(workspaceId,datasetId,context.upload_request_id); onArchived() }
    catch (e) { setError(e instanceof Error ? e.message : "Could not archive this delivery. Retry."); submitted.current = false; setBusy(false) }
  }
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/50 p-4">
    <div ref={ref} role="dialog" aria-modal="true" aria-labelledby="archive-title" aria-describedby="archive-consequence" className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-xl bg-white p-5 shadow-xl">
      <h2 id="archive-title" className="break-words text-lg font-semibold [overflow-wrap:anywhere]">{action.label}: {fileName}?</h2>
      <p id="archive-consequence" className="mt-3 break-words text-sm text-slate-600">{action.blocked ? "This delivery is currently processing and cannot be archived yet." : action.processed ? `This delivery has already been processed. It will be removed from active ${datasetName} views, but its historical processing, quality, and lineage information will be retained.` : `This delivery has not completed processing. It will be removed from the active ${datasetName} dataset. Any profiling and rule history will be retained.`} Canonical source bytes will be retained.</p>
      {error && <p role="alert" className="mt-3 text-sm text-red-700">{error}</p>}
      <div className="mt-5 flex flex-col gap-3 sm:flex-row sm:justify-end"><Button variant="secondary" disabled={busy} onClick={onClose}>Cancel</Button><Button variant="danger" disabled={busy || action.blocked} onClick={confirm}>{busy ? "Saving…" : action.label}</Button></div>
    </div>
  </div>
}
