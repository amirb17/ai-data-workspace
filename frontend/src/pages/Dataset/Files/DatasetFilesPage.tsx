import { useState } from "react"
import { useOutletContext, useParams, useSearchParams } from "react-router-dom"
import { Button } from "../../../components/ui/Button"
import { ApiFeedback } from "../../../components/ui/ApiFeedback"
import { StatusBadge } from "../../../components/ui/StatusBadge"
import { UploadFileDialog } from "../../../features/files/components/UploadFileDialog"
import { useDatasetSummary } from "../../../features/datasets/useDatasetSummary"
import { readDatasetFiles } from "../../../features/files/data/storage"
import { countLabel, terminalDelivery } from "../../../features/ingestion/processingPresentation"
import type { DatasetListItem } from "../../../features/datasets/types"
import type { DatasetProcessing } from "../../../services/api/rules"
import { ArchiveDeliveryDialog } from "../../../features/files/components/ArchiveDeliveryDialog"
import { DeliveryMoreMenu } from "../../../features/files/components/DeliveryMoreMenu"
export function DatasetFilesPage() {
  const { workspaceId = "", datasetId = "" } = useParams()
  const dataset = useOutletContext<DatasetListItem>()
  const [params, setParams] = useSearchParams()
  const [uploadOpen, setUploadOpen] = useState(params.get("setup") === "1")
  const resource = useDatasetSummary()
  const [archiving,setArchiving] = useState<DatasetProcessing["deliveries"][number] | null>(null)
  const [expanded,setExpanded] = useState<number | null>(null)
  let metadata: ReturnType<typeof readDatasetFiles> = [], cacheError = ""
  try { metadata = readDatasetFiles(workspaceId,datasetId) } catch { cacheError = "Saved inspection details are unavailable in this browser. Backend deliveries are still shown." }
  return <div className="space-y-5">
    <header className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="text-xl font-semibold">Files</h2><p className="mt-1 text-sm text-slate-600">Deliveries are incoming source data for {dataset.name}. Each delivery retains its own file and processing outcome.</p></div><Button onClick={() => setUploadOpen(true)}>Add Delivery</Button></header>
    {cacheError && <p role="status" className="text-sm text-slate-600">{cacheError}</p>}
    {!resource.data ? <ApiFeedback error={resource.error} retry={resource.retry} loading="Loading deliveries…" /> : !resource.data.deliveries.length ? <section className="space-y-2 rounded-xl border border-slate-300 border-dashed bg-white p-6 text-center"><h3 className="font-semibold">No deliveries yet</h3><p className="text-sm text-slate-600">Use Add Delivery to inspect a CSV and check its compatibility before uploading.</p></section> : <section className="space-y-4" aria-label="Source deliveries">{resource.data.deliveries.slice().reverse().map(delivery => {
      const c = delivery.context
      const inspection = metadata.find(f => f.id === c.file_id && f.uploadRequestIds?.includes(c.upload_request_id))
      return <article key={c.upload_request_id} className="space-y-3 rounded-xl border border-slate-200 bg-white p-4 sm:p-5">
        <div className="flex flex-wrap items-start justify-between gap-3"><h3 className="min-w-0 break-words font-semibold [overflow-wrap:anywhere]">{delivery.source_file_name}</h3><div className="flex flex-wrap items-start gap-2"><StatusBadge status={c.status === "SUCCESS" && (c.rejected_rows ?? 0) > 0 ? "SUCCESS_WITH_WARNINGS" : c.status} /><DeliveryMoreMenu context={c} fileName={delivery.source_file_name} processingHref={`/app/workspaces/${workspaceId}/datasets/${datasetId}/processing`} onArchive={() => setArchiving(delivery)} onViewDetails={() => setExpanded(c.upload_request_id)} /></div></div>
        <p className="text-xs text-slate-500">Uploaded <time dateTime={delivery.created_at}>{new Date(delivery.created_at).toLocaleString()}</time></p>
        <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4"><div><dt className="text-slate-500">Rows{c.input_rows == null && inspection?.rowCount != null ? " (inspection)" : ""}</dt><dd>{countLabel(c.input_rows ?? inspection?.rowCount)}</dd></div><div><dt className="text-slate-500">Columns{c.column_count == null && inspection?.columnCount != null ? " (inspection)" : ""}</dt><dd>{countLabel(c.column_count ?? inspection?.columnCount)}</dd></div><div><dt className="text-slate-500">File size</dt><dd>{countLabel(delivery.size_bytes)}{delivery.size_bytes != null ? " bytes" : ""}</dd></div><div><dt className="text-slate-500">Processing</dt><dd>{terminalDelivery(c) ? "Completed" : c.status === "READY_TO_PROCESS" ? "Not started" : "See processing status"}</dd></div></dl>
        <details open={expanded === c.upload_request_id} id={`delivery-${c.upload_request_id}-details`}><summary className="min-h-11 cursor-pointer py-3 text-sm font-medium text-indigo-700">View delivery details</summary><div className="space-y-3 border-t pt-3 text-sm">
          <p>Valid: {countLabel(c.valid_rows)} · Quarantined: {countLabel(c.rejected_rows)} · Published: {countLabel(c.output_rows)}</p>
          {c.rule_state === "FINALIZED" && <p>Approved Rule Version {c.rule_version}{c.rules_reused ? " reused" : ""}</p>}
          {c.load_strategy === "SNAPSHOT" && <div className="space-y-1"><p>Snapshot coverage: {c.snapshot_context?.coverage === "COMPLETE" ? "Complete" : "Partial / unknown"} · {c.snapshot_context?.delivery_kind ?? "Declaration required"}</p>{c.snapshot_context?.effective_at && <p>Effective snapshot time: {new Date(c.snapshot_context.effective_at).toLocaleString()}</p>}<p>Outcome: {c.snapshot_outcome ?? "Not applied"} · Deactivated: {countLabel(c.deactivated_rows)} · Reactivated: {countLabel(c.reactivated_rows)}</p></div>}
          {inspection && <p>Browser inspection: {inspection.sizeBytes.toLocaleString()} bytes · {countLabel(inspection.rowCount)} rows · {countLabel(inspection.columnCount)} columns</p>}
          <dl><dt className="text-slate-500">Upload reference</dt><dd>{c.upload_request_id}</dd></dl>
          <p className="text-slate-600">Removing or archiving a delivery retains its source file and historical lineage.</p>
        </div></details>
      </article>
    })}</section>}
    <button className="min-h-11 text-sm text-indigo-700" onClick={resource.retry}>Refresh delivery list</button>
    {archiving && <ArchiveDeliveryDialog workspaceId={workspaceId} datasetId={datasetId} datasetName={dataset.name} fileName={archiving.source_file_name} context={archiving.context} onClose={() => setArchiving(null)} onArchived={() => { setArchiving(null); resource.retry() }} />}
    {uploadOpen && <UploadFileDialog open workspaceId={workspaceId} datasetId={datasetId} datasetName={dataset.name} onAccepted={resource.retry} onClose={() => { setUploadOpen(false); setParams({}); resource.retry() }} />}
  </div>
}
