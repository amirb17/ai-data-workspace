import { useCallback, useEffect, useState } from "react"
import { getProcessingContext, type ProcessingContext } from "../../services/api/rules"
export function useProcessingContext(workspaceId: string, datasetId: string, uploadId: number) {
  const [state, setState] = useState<{ context?: ProcessingContext; error?: string }>({})
  const [revision, setRevision] = useState(0)
  const reload = useCallback(() => setRevision((value) => value + 1), [])
  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    const load = () => getProcessingContext(workspaceId, datasetId, uploadId, controller.signal).then((context) => {
      if (controller.signal.aborted) return
      setState({ context })
      if (context.status === "BRONZE_PROCESSING") timer = setTimeout(load, 2000)
    }).catch((error) => {
      if (!controller.signal.aborted) setState({ error: error instanceof Error ? error.message : "Unable to load processing state." })
    })
    void load()
    window.addEventListener("focus", reload)
    return () => { controller.abort(); clearTimeout(timer); window.removeEventListener("focus", reload) }
  }, [workspaceId, datasetId, uploadId, revision, reload])
  return { ...state, reload }
}
