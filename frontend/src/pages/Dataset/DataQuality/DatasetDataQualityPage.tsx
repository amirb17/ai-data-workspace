import { Link, useParams } from "react-router-dom"
import { useDatasetSummary } from "../../../features/datasets/useDatasetSummary"
import { ApiFeedback } from "../../../components/ui/ApiFeedback"
import { countLabel, terminalDelivery } from "../../../features/ingestion/processingPresentation"
export function DatasetDataQualityPage() {
  const { workspaceId, datasetId } = useParams()
  const resource = useDatasetSummary()
  if (!resource.data) return <ApiFeedback error={resource.error} retry={resource.retry} loading="Loading data-quality summary…" />
  const processed = resource.data.deliveries.filter(d => terminalDelivery(d.context))
  const issues = resource.data.deliveries.filter(d => (d.context.rejected_rows ?? 0) > 0 || (d.context.incremental_rejected_rows ?? 0) > 0)
  const known = processed.length > 0 && processed.every(d => d.context.rejected_rows != null && (!d.context.load_strategy || d.context.incremental_rejected_rows != null))
  const total = known ? processed.reduce((sum,d) => sum + (d.context.rejected_rows ?? 0),0) : null
  const incremental = processed.filter(d => d.context.load_strategy)
  const updateRejected = incremental.length && incremental.every(d => d.context.incremental_rejected_rows != null) ? incremental.reduce((sum,d) => sum + d.context.incremental_rejected_rows!,0) : null
  return <section className="space-y-4"><h2 className="text-2xl font-semibold">Quality</h2><div className="space-y-4 rounded-xl border border-slate-200 bg-white p-5"><p>{issues.length ? `${issues.length} deliveries have rows that need attention.` : known ? "No data-quality issues detected in current processed deliveries." : "Process a delivery to see its data-quality outcome."}</p><dl className="flex flex-wrap gap-6"><div><dt className="text-sm text-slate-500">Silver quarantined rows</dt><dd className="text-xl font-semibold">{countLabel(total)}</dd></div>{incremental.length > 0 && <div><dt className="text-sm text-slate-500">Rejected at dataset update</dt><dd className="text-xl font-semibold">{countLabel(updateRejected)}</dd></div>}</dl><p className="text-xs text-slate-500">Counts describe completed deliveries, not a quality score or the current dataset row count.</p>{issues.map(d => <article key={d.context.upload_request_id} className="space-y-1 border-t pt-3 text-sm"><h3 className="break-words font-medium">{d.source_file_name}</h3><p>{countLabel(d.context.rejected_rows)} quarantined rows during Silver validation{d.context.incremental_rejected_rows != null ? ` · ${countLabel(d.context.incremental_rejected_rows)} rejected at dataset update` : ""}</p>{d.context.issue_summary?.map((issue,index) => <p key={index} className="text-slate-600">{issue.rule_type.replaceAll("_", " ").toLowerCase()}: {issue.violation_count} violations</p>)}</article>)}<p className="text-sm text-slate-500">Row correction and replay are not available yet.</p><Link className="inline-flex min-h-11 items-center text-indigo-700" to={`/app/workspaces/${workspaceId}/datasets/${datasetId}/processing`}>View processing results</Link></div></section>
}
