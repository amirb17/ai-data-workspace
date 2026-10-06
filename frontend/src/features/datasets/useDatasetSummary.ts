import { useCallback } from "react"
import { useParams } from "react-router-dom"
import { datasetProcessing } from "../../services/api/rules"
import { useApiResource } from "../../services/api/useApiResource"

export function useDatasetSummary() {
  const { workspaceId = "", datasetId = "" } = useParams()
  const load = useCallback((signal: AbortSignal) => datasetProcessing(workspaceId, datasetId, false, signal), [workspaceId, datasetId])
  return useApiResource(`delivery-summary:${workspaceId}:${datasetId}`, load)
}
