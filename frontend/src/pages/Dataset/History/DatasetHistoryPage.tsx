import { Link, useParams } from "react-router-dom"
import { useDatasetSummary } from "../../../features/datasets/useDatasetSummary"
import { ApiFeedback } from "../../../components/ui/ApiFeedback"
import { deliveryStatus } from "../../../features/ingestion/processingPresentation"
export function DatasetHistoryPage() {
  const {workspaceId,datasetId} = useParams()
  const resource = useDatasetSummary()
  if (!resource.data) return <ApiFeedback error={resource.error} retry={resource.retry} loading="Loading delivery history…" />
  return <section className="space-y-4"><h2 className="text-xl font-semibold">History</h2><p className="text-sm text-slate-600">Current delivery outcomes. A complete attempt audit is not available here yet.</p>{resource.data.deliveries.length ? resource.data.deliveries.slice().reverse().map(d => <article key={d.context.upload_request_id} className="space-y-2 rounded-xl border border-slate-200 bg-white p-4"><h3 className="break-words font-medium">{d.source_file_name}</h3><p>{deliveryStatus(d.context).label}</p><time className="text-sm text-slate-500" dateTime={d.created_at}>{new Date(d.created_at).toLocaleString()}</time></article>) : <p>No processing history yet. Upload and process your first delivery.</p>}<Link className="inline-flex min-h-11 items-center text-indigo-700" to={`/app/workspaces/${workspaceId}/datasets/${datasetId}/processing`}>View Processing</Link></section>
}
