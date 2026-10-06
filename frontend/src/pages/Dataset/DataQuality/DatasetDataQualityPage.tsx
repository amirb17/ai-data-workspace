import { Link, useParams } from "react-router-dom"
import { useDatasetSummary } from "../../../features/datasets/useDatasetSummary"
import { ApiFeedback } from "../../../components/ui/ApiFeedback"
import { countLabel, terminalDelivery } from "../../../features/ingestion/processingPresentation"
export function DatasetDataQualityPage() {
  const { workspaceId, datasetId } = useParams()
  const resource = useDatasetSummary()
  if (!resource.data) return <ApiFeedback error={resource.error} retry={resource.retry} loading="Loading data-quality summary…" />
  const processed = resource.data.deliveries.filter(d => terminalDelivery(d.context))
  const issues = resource.data.deliveries.filter(d => (d.context.rejected_rows ?? 0) > 0)
  const known = processed.length > 0 && processed.every(d => d.context.rejected_rows != null)
  const total = known ? processed.reduce((sum,d) => sum + (d.context.rejected_rows ?? 0),0) : null
  return <section className="space-y-4"><h2 className="text-xl font-semibold">Data Quality</h2><div className="space-y-3 rounded-xl border border-slate-200 bg-white p-5"><p>{issues.length ? `${issues.length} deliveries have rows that need attention.` : known ? "No data-quality issues detected in current processed deliveries." : "Process a delivery to see its data-quality outcome."}</p><p className="text-sm text-slate-600">Quarantined rows in completed deliveries: {countLabel(total)}</p>{issues.map(d => <p key={d.context.upload_request_id} className="break-words text-sm">{d.source_file_name}: {countLabel(d.context.rejected_rows)} quarantined rows</p>)}<p className="text-sm text-slate-500">Detailed Data Quality review is coming in the next stage. Row correction and replay are not available yet.</p><Link className="inline-flex min-h-11 items-center text-indigo-700" to={`/app/workspaces/${workspaceId}/datasets/${datasetId}/processing`}>View Processing Results</Link></div></section>
}
