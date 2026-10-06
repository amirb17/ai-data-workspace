import { Link, useParams } from "react-router-dom"
import { useDatasetSummary } from "../../../features/datasets/useDatasetSummary"
import { ApiFeedback } from "../../../components/ui/ApiFeedback"
export function DatasetAnalyticsPage() {
  const { workspaceId, datasetId } = useParams()
  const resource = useDatasetSummary()
  if (!resource.data) return <ApiFeedback error={resource.error} retry={resource.retry} loading="Checking analytics readiness…" />
  const available = resource.data.deliveries.some(d => d.context.stages.gold === "SUCCESS" && (d.context.output_rows ?? 0) > 0)
  return <section className="space-y-4"><h2 className="text-xl font-semibold">Analytics</h2><div className="space-y-3 rounded-xl border border-slate-200 bg-white p-5"><p>{available ? "Trusted processing output is available. Dataset analytics exploration is coming in a later stage." : "Analytics will be available after successful processing produces valid output."}</p><Link className="inline-flex min-h-11 items-center text-indigo-700" to={`/app/workspaces/${workspaceId}/datasets/${datasetId}/processing`}>View Processing</Link></div></section>
}
