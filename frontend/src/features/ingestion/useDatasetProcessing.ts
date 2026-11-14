import { useEffect, useRef, useState } from "react"
import { datasetProcessing, type DatasetProcessing } from "../../services/api/rules"
import { readStageJobs, activeJob, type StageJob } from "../../services/api/processingJobs"
import { isRunning } from "./processingState"
import { ownsProcessing } from "./processingPresentation"

export function useDatasetProcessing(workspaceId: string, datasetId: string) {
  const [data, setData] = useState<DatasetProcessing>()
  const [jobState, setJobs] = useState<{scope:string;items:StageJob[]}>()
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)
  const [revision, refresh] = useState(0)
  const [watchedRequests, watch] = useState(0)
  const active = useRef(false)
  const scope = `${workspaceId}:${datasetId}`
  const jobs = jobState?.scope === scope ? jobState.items : []
  const currentScope = useRef(scope)
  useEffect(() => { currentScope.current = scope }, [scope])
  const scopedData = ownsProcessing(data, workspaceId, datasetId) ? data : undefined
  const running = (scopedData?.deliveries.some(d => isRunning(d.context.status)) ?? false) || jobs.some(activeJob)
  useEffect(() => {
    const controller = new AbortController()
    let disposed = false
    let loading = false
    const load = () => {
      if (loading) return
      loading = true
      void Promise.allSettled([datasetProcessing(workspaceId, datasetId, false, controller.signal), readStageJobs(workspaceId,datasetId,controller.signal)]).then(([deliveryResult,jobResult]) => {
        if (disposed) return
        if (deliveryResult.status === 'fulfilled') setData(deliveryResult.value)
        if (jobResult.status === 'fulfilled') setJobs({scope: `${workspaceId}:${datasetId}`,items:jobResult.value})
        const failure = deliveryResult.status === 'rejected' ? deliveryResult.reason : jobResult.status === 'rejected' ? jobResult.reason : undefined
        setError(failure ? failure instanceof Error ? failure.message : 'Unable to read current processing state.' : '')
      }).finally(() => { loading = false })
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
    } catch (caught) { if (currentScope.current === scope) setError(caught instanceof Error ? caught.message : "Processing could not be submitted.") }
    finally { active.current = false; setBusy(false); refresh(v => v + 1) }
  }
  return { data: scopedData, jobs, error, busy, requestActive: busy || watchedRequests > 0 || jobs.some(activeJob), process,
    onRequestActivity: (started: boolean) => watch(value => Math.max(0, value + (started ? 1 : -1))), reload: () => refresh(v => v + 1) }
}
