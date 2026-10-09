import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ApiFeedback } from '../../../components/ui/ApiFeedback'
import { useApiResource } from '../../../services/api/useApiResource'
import { getRelationships, getRelationshipReadiness, discoverRelationships, reviewRelationship, type RelationshipsView, type Candidate } from '../../../services/api/relationships'
import { CandidateCard, RelationshipEvidenceCard } from './RelationshipEvidenceCard'
export function RelationshipsPage() {
  const {workspaceId=''} = useParams()
  const load = useCallback((signal: AbortSignal)=>getRelationships(workspaceId,signal),[workspaceId])
  const resource = useApiResource(`relationships:${workspaceId}`,load)
  return resource.data ? <Content key={workspaceId} workspaceId={workspaceId} data={resource.data} reload={resource.retry}/> : <ApiFeedback loading="Checking relationship evidence…" error={resource.error} retry={resource.retry}/>
}
export function RelationshipList({data,busy,generate,review}: {data: RelationshipsView; busy: boolean; generate: () => void; review: (candidate: Candidate, action: 'confirm' | 'reject') => void}) {
  return <section className="min-w-0 space-y-5">
    <h2 className="text-xl font-semibold">Data Model · Relationships</h2>
    <p className="text-sm text-gray-600">Deterministic candidates · Review required. Confirmation records your decision; it does not execute joins or analytics.</p>
    <div className="rounded-xl border bg-indigo-50 p-4">
      <p>{data.coverage.datasets_analyzed} of {data.coverage.datasets_total} datasets eligible{data.coverage.partial ? ' · Partial coverage' : ''}</p>
      <p className="mt-2 text-sm">{data.readiness_message}</p>
      <p className="mt-2 text-sm">AI dataset context: {data.semantic_context.dataset_semantics_available} available · Workspace context: {data.semantic_context.workspace_semantics}{data.semantic_context.workspace_domain ? ` (${data.semantic_context.workspace_domain})` : ''}. No new AI request.</p>
      <button className="mt-3 min-h-11 rounded-lg bg-indigo-600 px-4 py-2 text-white disabled:opacity-50" onClick={generate} disabled={busy || !data.can_discover}>{busy || data.status==='DISCOVERING' ? 'Verifying…' : data.status==='READY' ? 'Evidence is current' : data.status==='FAILED' ? 'Retry Discovery' : data.status==='STALE' ? 'Refresh Relationship Evidence' : 'Discover Relationships'}</button>
    </div>
    {data.status==='STALE' && <p className="text-amber-800">Sources changed. Earlier candidates are hidden; reviewed configuration remains below with separate structural and verification freshness.</p>}
    {data.status==='FAILED' && <p role="alert">Discovery could not complete ({data.failure_code}). Trusted datasets are unchanged. Check the V1 limits and retry.</p>}
    {data.coverage.excluded.map(d=><p key={d.dataset_id} className="break-words text-sm">{d.dataset_name}: {d.reason.replaceAll('_',' ')} · <Link className="text-indigo-700 underline" to={`/app/workspaces/${data.workspace_id}/datasets/${d.dataset_id}/profile`}>Review Dataset</Link></p>)}
    {data.status==='READY' && data.candidates.length===0 && <p>No compatible deterministic pairs were found. V1 discovers single-column identifiers; composite parent keys require future support.</p>}
    <div className="grid min-w-0 gap-4 lg:grid-cols-2">{data.candidates.map(c=><CandidateCard key={c.candidate_id} candidate={c} busy={busy} review={review}/>)}</div>
    {data.reviewed_relationships.length>0 && <div className="space-y-4"><h3 className="text-lg font-semibold">Reviewed configuration</h3>{data.reviewed_relationships.map(r=><article key={r.evidence.candidate_key} className="min-w-0 rounded-xl border bg-white p-5">
      <p className="mb-3 font-semibold">{r.status} · version {r.relationship_version}</p>
      <p className="mb-3 text-sm">Structure: {r.structural_status.replaceAll('_',' ')} · Verification: {r.verification_status} · Reviewed {new Date(r.reviewed_at).toLocaleString()}</p>
      <RelationshipEvidenceCard evidence={r.evidence}/>
      {r.verification_status==='STALE' && <p className="mt-3 text-amber-800">Historical coverage shown for audit. Refresh and explicitly review current evidence before treating this verification as current.</p>}
    </article>)}</div>}
  </section>
}
function Content({workspaceId,data,reload}: {workspaceId: string; data: RelationshipsView; reload: () => void}) {
  const [busy,setBusy] = useState(false), [error,setError] = useState('')
  const action = useRef<AbortController | null>(null)
  useEffect(()=>()=>action.current?.abort(),[])
  const identity = JSON.stringify([data.status,data.run_id,data.failure_code,data.coverage,data.semantic_context,data.source_pins,data.review_revision])
  useEffect(()=>{
    const controller = new AbortController(); let reading = false
    const timer = setInterval(()=>{
      if(reading)return; reading=true
      getRelationshipReadiness(workspaceId,controller.signal).then(next=>{
        if(!controller.signal.aborted && JSON.stringify([next.status,next.run_id,next.failure_code,next.coverage,next.semantic_context,next.source_pins,next.review_revision])!==identity)reload()
      },()=>{if(!controller.signal.aborted)reload()}).finally(()=>{reading=false})
    },3000)
    return ()=>{clearInterval(timer);controller.abort()}
  },[workspaceId,identity,reload])
  async function perform(candidate?: Candidate, decision?: 'confirm' | 'reject') {
    if(action.current)return
    const controller = new AbortController(); action.current=controller; setBusy(true);setError('')
    try {
      if(candidate && decision)await reviewRelationship(workspaceId,candidate,decision,controller.signal)
      else await discoverRelationships(workspaceId,controller.signal)
    } catch {if(!controller.signal.aborted)setError('Action could not complete. Reload current evidence and review state before retrying.')}
    finally {action.current=null;if(!controller.signal.aborted){setBusy(false);reload()}}
  }
  return <>{error && <p role="alert" className="mb-4 text-red-700">{error}</p>}<RelationshipList data={data} busy={busy} generate={()=>void perform()} review={(c,a)=>void perform(c,a)}/></>
}
