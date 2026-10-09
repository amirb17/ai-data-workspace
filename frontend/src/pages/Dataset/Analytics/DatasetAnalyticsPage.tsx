import { Link, useParams } from "react-router-dom"
import { AnalyticsReadiness } from "../../../features/datasets/AnalyticsReadiness"
export function DatasetAnalyticsPage() {
  const { workspaceId = '', datasetId = '' } = useParams()
  return <section className="space-y-4"><h2 className="text-xl font-semibold">Dataset Analytics</h2><AnalyticsReadiness workspaceId={workspaceId} datasetId={datasetId} dashboard /><Link className="inline-flex min-h-11 items-center text-indigo-700" to={`/app/workspaces/${workspaceId}/datasets/${datasetId}/processing`}>View delivery processing</Link></section>
}
