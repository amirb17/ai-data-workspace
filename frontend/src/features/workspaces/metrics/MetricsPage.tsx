import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ApiFeedback } from '../../../components/ui/ApiFeedback'
import { useApiResource } from '../../../services/api/useApiResource'
import { getMetrics, getMetricReadiness, discoverMetrics, reviewMetric, type MetricView, type MetricCandidate } from '../../../services/api/metrics'
import { MetricCard } from './MetricCard'
export function MetricsPage() {
  const {workspaceId=''}=useParams()
  const load=useCallback((signal: AbortSignal)=>getMetrics(workspaceId,signal),[workspaceId])
  const resource=useApiResource(`metrics:${workspaceId}`,load)
  return resource.data ? <Content key={workspaceId} workspaceId={workspaceId} data={resource.data} reload={resource.retry}/> : <ApiFeedback loading="Checking metric definitions…" error={resource.error} retry={resource.retry}/>
}
export function MetricList({data,busy,generate,review}: {data: MetricView; busy: boolean; generate: ()=>void; review: (c: MetricCandidate,a: 'approve' | 'reject')=>void}) {
  return <section className="min-w-0 space-y-5"><h2 className="text-xl font-semibold">Suggested Metrics</h2>
    <p className="text-sm">Current semantic evidence and confirmed relationships inform proposals. Deterministic validation controls acceptance; AI confidence alone never grants trust.</p>
    <div className="space-y-2 rounded-xl border bg-indigo-50 p-4"><p>{data.coverage.datasets_analyzed} of {data.coverage.datasets_total} datasets eligible · {data.status.replaceAll('_',' ')}</p><p>{data.readiness_message}</p>
      <p className="text-sm">Discovery sends dataset/column names and redacted semantic metadata to the configured AI provider. No raw rows or categorical values are sent.</p>
      <button className="min-h-11 rounded-lg bg-indigo-600 px-4 py-2 text-white disabled:opacity-50" disabled={busy || !data.can_discover} onClick={generate}>{busy || data.status==='GENERATING' ? 'Discovering…' : data.status==='READY' ? 'Definitions are current' : data.status==='FAILED' ? 'Retry Discovery' : 'Discover Metrics'}</button>
    </div>
    {data.failure_code && <p role="alert">Discovery could not complete ({data.failure_code}). Existing analytics and datasets are unchanged.</p>}
    {data.status==='STALE' && <p className="text-amber-800">Dependencies changed. Historical definitions remain visible with separate freshness; they are not current recommendations.</p>}
    {data.coverage.excluded.map(d=><p key={d.dataset_id} className="break-words text-sm">{d.dataset_name}: {d.reason.replaceAll('_',' ')} · <Link className="text-indigo-700 underline" to={`/app/workspaces/${data.workspace_id}/datasets/${d.dataset_id}/understanding`}>Review Dataset Understanding</Link></p>)}
    {data.status==='READY' && !data.candidates.length && <p>No safe metric definitions were proposed for the current evidence.</p>}
    {data.discovery_context && <details className="text-sm"><summary className="min-h-11 cursor-pointer py-2">Discovery version and model</summary><pre className="whitespace-pre-wrap break-words">{JSON.stringify(data.discovery_context,null,2)}</pre></details>}
    <div className="grid min-w-0 gap-4 lg:grid-cols-2">{data.candidates.map(c=><MetricCard key={c.candidate_id} candidate={c} busy={busy} review={review}/>)}</div>
  </section>
}
function Content({workspaceId,data,reload}: {workspaceId: string; data: MetricView; reload: ()=>void}) {
  const [busy,setBusy]=useState(false),[error,setError]=useState('')
  const action=useRef<AbortController | null>(null)
  useEffect(()=>()=>action.current?.abort(),[])
  const identity=JSON.stringify([data.status,data.run_id,data.failure_code,data.source_pins,data.workspace_suggestion_id,data.review_revision,data.freshness_revision,data.coverage])
  useEffect(()=>{
    const controller=new AbortController();let reading=false
    const timer=setInterval(()=>{if(reading)return;reading=true
      getMetricReadiness(workspaceId,controller.signal).then(next=>{
        if(!controller.signal.aborted && JSON.stringify([next.status,next.run_id,next.failure_code,next.source_pins,next.workspace_suggestion_id,next.review_revision,next.freshness_revision,next.coverage])!==identity)reload()
      },()=>{if(!controller.signal.aborted)reload()}).finally(()=>{reading=false})
    },3000)
    return ()=>{clearInterval(timer);controller.abort()}
  },[workspaceId,identity,reload])
  async function perform(c?: MetricCandidate,a?: 'approve' | 'reject') {
    if(action.current)return
    const controller=new AbortController();action.current=controller;setBusy(true);setError('')
    try {if(c && a)await reviewMetric(workspaceId,c,a,controller.signal);else await discoverMetrics(workspaceId,controller.signal)}
    catch {if(!controller.signal.aborted)setError('Action could not complete. Reload readiness and review state before retrying.')}
    finally {action.current=null;if(!controller.signal.aborted){setBusy(false);reload()}}
  }
  return <>{error && <p role="alert" className="mb-4 text-red-700">{error}</p>}<MetricList data={data} busy={busy} generate={()=>void perform()} review={(c,a)=>void perform(c,a)}/></>
}
