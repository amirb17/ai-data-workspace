import { useCallback } from "react"
import { datasetProcessing } from "../../../services/api/rules"
import { getAnalyticsReadiness } from "../../../services/api/analytics"
import { StatusBadge } from "../../../components/ui/StatusBadge"
import { useApiResource } from "../../../services/api/useApiResource"
import { overviewAttention, ownsProcessing } from "../../ingestion/processingPresentation"
export function DatasetReadiness({ workspaceId, datasetId }: { workspaceId: string; datasetId: number }) {
  const load = useCallback(async (signal: AbortSignal) => {
    const [processing, analytics] = await Promise.all([datasetProcessing(workspaceId, String(datasetId), false, signal), getAnalyticsReadiness(workspaceId, String(datasetId), signal)])
    return { processing, analytics }
  }, [workspaceId, datasetId])
  const resource = useApiResource(`dataset-readiness:${workspaceId}:${datasetId}`, load)
  if (!ownsProcessing(resource.data?.processing, workspaceId, String(datasetId))) return <p className="text-xs text-slate-500">{resource.error ? "Delivery summary unavailable. Open the dataset to retry." : "Loading delivery summary…"}</p>
  const { processing: data, analytics } = resource.data!
  return <div className="space-y-2"><p className="text-sm text-slate-600">{analytics.trusted_rows === null ? "Trusted row count unavailable" : `${analytics.trusted_rows.toLocaleString()} trusted rows`} · {data.summary.total} deliveries</p><div className="flex flex-wrap items-center gap-2"><StatusBadge status={analytics.freshness} /><span className="text-xs text-slate-500">{data.summary.pending} pending · {data.summary.awaiting_rules} need rules review · {overviewAttention(data)} need attention</span></div></div>
}
