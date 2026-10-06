import { useCallback } from "react"
import { datasetProcessing } from "../../../services/api/rules"
import { useApiResource } from "../../../services/api/useApiResource"
import { overviewAttention, ownsProcessing } from "../../ingestion/processingPresentation"
export function DatasetReadiness({ workspaceId, datasetId }: { workspaceId: string; datasetId: number }) {
  const load = useCallback((signal: AbortSignal) => datasetProcessing(workspaceId, String(datasetId), false, signal), [workspaceId, datasetId])
  const resource = useApiResource(`dataset-readiness:${workspaceId}:${datasetId}`, load)
  if (!ownsProcessing(resource.data, workspaceId, String(datasetId))) return <p className="text-xs text-slate-500">{resource.error ? "Delivery summary unavailable. Open the dataset to retry." : "Loading delivery summary…"}</p>
  const data = resource.data!
  return <p className="text-sm text-slate-600">{data.summary.total} deliveries · {data.summary.pending} pending · {data.summary.awaiting_rules} need rules review · {overviewAttention(data)} need attention</p>
}
