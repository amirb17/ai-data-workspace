import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiFeedback } from '../../../components/ui/ApiFeedback'
import { useApiResource } from '../../../services/api/useApiResource'
import { generateUnderstanding, getUnderstanding, getUnderstandingReadiness, type Understanding } from '../../../services/api/understanding'
import { UnderstandingEvidence } from './UnderstandingEvidence'
export function DatasetUnderstanding({workspaceId,datasetId}: {workspaceId: string; datasetId: string}) {
  const load = useCallback((signal: AbortSignal)=>getUnderstanding(workspaceId,datasetId,signal),[workspaceId,datasetId])
  const resource = useApiResource(`understanding:${workspaceId}:${datasetId}`,load)
  return resource.data ? <Content key={`${workspaceId}:${datasetId}`} data={resource.data} workspaceId={workspaceId} datasetId={datasetId} reload={resource.retry}/> : <ApiFeedback loading="Checking AI understanding…" error={resource.error} retry={resource.retry}/>
}
function Content({data,workspaceId,datasetId,reload}: {data: Understanding; workspaceId: string; datasetId: string; reload: () => void}) {
  const [busy,setBusy] = useState(false), [error,setError] = useState('')
  const active = useRef(false), mounted = useRef(true)
  useEffect(()=>{mounted.current=true;return ()=>{mounted.current=false}},[])
  useEffect(()=>{
    const controller = new AbortController()
    let reading = false
    const interval = setInterval(()=>{
      if (reading) return
      reading = true
      getUnderstandingReadiness(workspaceId,datasetId,controller.signal).then(next=>{
      if (!controller.signal.aborted && (next.status!==data.status || next.profile_freshness!==data.profile_freshness || next.source_profile_id!==data.source_profile_id || next.source_state_id!==data.source_state_id)) reload()
    },()=>{if(!controller.signal.aborted)reload()}).finally(()=>{reading=false})},3000)
    return ()=>{clearInterval(interval);controller.abort()}
  },[workspaceId,datasetId,data.status,data.profile_freshness,data.source_profile_id,data.source_state_id,reload])
  async function generate() {
    if(active.current)return
    active.current=true;setBusy(true);setError('')
    try {await generateUnderstanding(workspaceId,datasetId)}
    catch {if(mounted.current)setError('Analysis could not start. Check the current profile and reload before retrying. V1 supports up to 200 columns.')}
    finally {active.current=false;if(mounted.current){setBusy(false);reload()}}
  }
  return <UnderstandingEvidence data={data} busy={busy} error={error} generate={generate}/>
}
