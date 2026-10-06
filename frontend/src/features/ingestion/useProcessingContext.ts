import { useCallback, useEffect, useState } from "react"
import { getProcessingContext, type ProcessingContext } from "../../services/api/rules"
import { isRunning } from "./processingState"
export function useProcessingContext(workspaceId: string, datasetId: string, uploadId: number, requestActive = false) {
  const key = `${workspaceId}/${datasetId}/${uploadId}`
  const [state, setState] = useState<{ key?: string; context?: ProcessingContext; error?: string }>({})
  const [revision, setRevision] = useState(0)
  const reload = useCallback(() => setRevision((value) => value + 1), [])
  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    const load = () => getProcessingContext(workspaceId, datasetId, uploadId, controller.signal).then((context) => {
      if (controller.signal.aborted) return
      setState({ key, context })
      if (requestActive || isRunning(context.status)) timer = setTimeout(load, 2000)
    }).catch((error) => {
      if (!controller.signal.aborted) setState({ key, error: error instanceof Error ? error.message : "Unable to load processing state." })
    })
    void load()
    window.addEventListener("focus", reload)
    return () => { controller.abort(); clearTimeout(timer); window.removeEventListener("focus", reload) }
  }, [workspaceId, datasetId, uploadId, revision, reload, requestActive, key])
  return { ...(state.key === key ? state : {}), reload }
}
