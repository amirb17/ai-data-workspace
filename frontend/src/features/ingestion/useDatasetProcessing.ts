import { useEffect, useRef, useState } from "react"
import { datasetProcessing, type DatasetProcessing } from "../../services/api/rules"
import { isRunning } from "./processingState"
import { ownsProcessing } from "./processingPresentation"

export function useDatasetProcessing(workspaceId: string, datasetId: string) {
  const [data, setData] = useState<DatasetProcessing>()
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)
  const [revision, refresh] = useState(0)
  const [watchedRequests, watch] = useState(0)
  const active = useRef(false)
  const scope = `${workspaceId}:${datasetId}`
  const currentScope = useRef(scope)
  useEffect(() => { currentScope.current = scope }, [scope])
  const scopedData = ownsProcessing(data, workspaceId, datasetId) ? data : undefined
  const running = scopedData?.deliveries.some(d => isRunning(d.context.status)) ?? false
  useEffect(() => {
    const controller = new AbortController()
    let disposed = false
    let loading = false
    const load = () => {
      if (loading) return
      loading = true
      void datasetProcessing(workspaceId, datasetId, false, controller.signal).then(value => {
        if (!disposed) { setData(previous => ({ ...value, operation: previous?.workspace_id === value.workspace_id && previous.dataset_id === value.dataset_id ? previous.operation : undefined })); setError("") }
      }).catch(caught => { if (!disposed) setError(caught instanceof Error ? caught.message : "Unable to read processing state.") }).finally(() => { loading = false })
    }
    load()
    const timer = busy || running || watchedRequests > 0 ? window.setInterval(load, 2500) : undefined
    return () => { disposed = true; controller.abort(); window.clearInterval(timer) }
  }, [workspaceId, datasetId, revision, busy, running, watchedRequests])
  const process = async () => {
    if (active.current) return
    active.current = true; setBusy(true); setError("")
    try {
      const value = await datasetProcessing(workspaceId, datasetId, true)
      if (currentScope.current === scope) setData(value)
    } catch (caught) { if (currentScope.current === scope) setError(caught instanceof Error ? caught.message : "Processing could not be completed.") }
    finally { active.current = false; setBusy(false); refresh(v => v + 1) }
  }
  return { data: scopedData, error, busy, requestActive: busy || watchedRequests > 0, process,
    onRequestActivity: (started: boolean) => watch(value => Math.max(0, value + (started ? 1 : -1))), reload: () => refresh(v => v + 1) }
}
