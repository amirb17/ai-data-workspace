import { useCallback, useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { ApiFeedback } from '../../../components/ui/ApiFeedback'
import { useApiResource } from '../../../services/api/useApiResource'
import { getWorkspaceUnderstanding, getWorkspaceReadiness, generateWorkspaceUnderstanding, type WorkspaceUnderstanding as Result } from '../../../services/api/workspaceUnderstanding'
import { WorkspaceEvidence } from './WorkspaceEvidence'
export function WorkspaceUnderstandingPage() {
  const {workspaceId = ''} = useParams()
  const load = useCallback((signal: AbortSignal)=>getWorkspaceUnderstanding(workspaceId,signal),[workspaceId])
  const resource = useApiResource(`workspace-understanding:${workspaceId}`,load)
  return resource.data ? <Content key={workspaceId} workspaceId={workspaceId} data={resource.data} reload={resource.retry}/> : <ApiFeedback loading="Checking workspace understanding…" error={resource.error} retry={resource.retry}/>
}
function Content({workspaceId,data,reload}: {workspaceId: string; data: Result; reload: () => void}) {
  const [busy,setBusy] = useState(false), [error,setError] = useState('')
  const action = useRef<AbortController | null>(null)
  useEffect(()=>()=>action.current?.abort(),[])
  const identity = JSON.stringify([data.source_pins,data.coverage])
  useEffect(()=>{
    const controller = new AbortController()
    let reading = false
    const interval = setInterval(()=>{
      if(reading)return
      reading = true
      getWorkspaceReadiness(workspaceId,controller.signal).then(next=>{
        if(!controller.signal.aborted && (next.status !== data.status || next.failure_code !== data.failure_code || JSON.stringify([next.source_pins,next.coverage]) !== identity))reload()
      },()=>{if(!controller.signal.aborted)reload()}).finally(()=>{reading=false})
    },3000)
    return ()=>{clearInterval(interval);controller.abort()}
  },[workspaceId,data.status,data.failure_code,identity,reload])
  async function generate() {
    if(action.current)return
    const controller = new AbortController()
    action.current = controller;setBusy(true);setError('')
    try {await generateWorkspaceUnderstanding(workspaceId,controller.signal)}
    catch {if(!controller.signal.aborted)setError('Analysis could not start. Reload and check dataset readiness. V1 supports 50 eligible datasets, 1000 columns and 100 KB of evidence.')}
    finally {action.current=null;if(!controller.signal.aborted){setBusy(false);reload()}}
  }
  return <WorkspaceEvidence data={data} busy={busy} error={error} generate={generate}/>
}
