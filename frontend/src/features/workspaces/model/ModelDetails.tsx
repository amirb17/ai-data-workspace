import { useCallback, useState } from 'react'
import { Link } from 'react-router-dom'
import { X } from 'lucide-react'
import { ModelColumns } from './ModelColumns'
import { useDialogFocus } from '../../../components/ui/useDialogFocus'
import { useApiResource } from '../../../services/api/useApiResource'
import { datasetProcessing } from '../../../services/api/rules'
import { terminalDelivery } from '../../ingestion/processingPresentation'
import { ApiFeedback } from '../../../components/ui/ApiFeedback'
import { currentMeaning, currentProfile, description, relationshipText, type ModelDataset, type ModelRelationship } from './modelView'

export function RelationshipDetails({relation}: {relation:ModelRelationship}) {
  const e=relation.evidence, coverage=e.signals.overlap.child_to_parent_coverage
  return <div className="space-y-4 break-words"><h3 className="text-lg font-semibold">{e.parent.dataset_name} → {e.child.dataset_name}</h3><p>{e.parent.dataset_name}.{e.parent.columns.join(', ')} matches {e.child.dataset_name}.{e.child.columns.join(', ')}</p><p>{relationshipText(e)}</p><p className={relation.suggested?'text-amber-800':'text-slate-700'}>{relation.suggested?'Suggested · Needs review':'Confirmed · Current'}</p>{!relation.suggested && coverage!==null && <p>Non-null child distinct-key coverage: {(coverage*100).toLocaleString(undefined,{maximumFractionDigits:2})}%</p>}{relation.reviewedAt&&<p className="text-sm text-slate-500">Reviewed {new Date(relation.reviewedAt).toLocaleString()}</p>}{relation.suggested&&<p className="text-sm text-slate-600">This suggestion is not part of the confirmed model. Review the evidence before deciding.</p>}</div>
}
function Quality({workspace,id}: {workspace:string;id:number}) {
  const load=useCallback((signal:AbortSignal)=>datasetProcessing(workspace,String(id),false,signal),[workspace,id])
  const resource=useApiResource(`model-quality:${workspace}:${id}`,load)
  if(!resource.data)return <ApiFeedback loading="Loading quality…" error={resource.error} retry={resource.retry}/>
  const processed=resource.data.deliveries.filter(d=>terminalDelivery(d.context))
  const known=processed.length>0 && processed.every(d=>d.context.valid_rows!==null && d.context.rejected_rows!==null)
  return <div className="space-y-4"><h3 className="font-semibold">Data quality</h3>{known?<dl className="grid grid-cols-2 gap-4"><div><dt className="text-sm text-slate-500">Silver accepted rows</dt><dd>{processed.reduce((s,d)=>s+d.context.valid_rows!,0).toLocaleString()}</dd></div><div><dt className="text-sm text-slate-500">Silver rejected rows</dt><dd>{processed.reduce((s,d)=>s+d.context.rejected_rows!,0).toLocaleString()}</dd></div></dl>:<p>Process a delivery to see its quality outcome.</p>}<p className="text-sm text-slate-500">Counts cover completed deliveries; updates and repeated deliveries can include the same business records. These are not a quality percentage or current dataset totals.</p><Link className="inline-flex min-h-11 items-center text-indigo-700" to={`/app/workspaces/${workspace}/datasets/${id}/data-quality`}>View Data Quality</Link></div>
}
export function ModelDetails({workspace,value,relation,relationships,close}: {workspace:string;value?:ModelDataset;relation?:ModelRelationship;relationships:ModelRelationship[];close:()=>void}) {
  const ref=useDialogFocus(true,close),[tab,setTab]=useState('Overview')
  const summary=value && currentProfile(value)?value.profile.summary:null
  const related=value?relationships.filter(r=>r.evidence.parent.dataset_id===value.dataset.id||r.evidence.child.dataset_id===value.dataset.id):[]
  const base=`/app/workspaces/${workspace}`
  return <div className="fixed inset-0 z-50 flex justify-end"><button tabIndex={-1} aria-label="Close details" className="absolute inset-0 bg-slate-950/30" onClick={close}/><div ref={ref} role="dialog" aria-modal="true" aria-label={value?`${value.dataset.name} details`:'Relationship details'} className="relative flex h-full w-full max-w-xl flex-col overflow-y-auto bg-white p-5 shadow-xl sm:p-6">
    <header className="mb-5 flex items-start justify-between gap-3"><h2 className="break-words text-xl font-semibold">{value?.dataset.name??'Relationship details'}</h2><button aria-label="Close details" className="min-h-11 shrink-0 p-2" onClick={close}><X/></button></header>
    {relation&&<><RelationshipDetails relation={relation}/>{relation.suggested&&<Link className="mt-5 inline-flex min-h-11 items-center text-indigo-700" to={`${base}/relationships`}>Review suggested relationship</Link>}</>}
    {value&&<><p className="break-words text-sm text-slate-600">{description(value)}</p>{!value.dataset.description&&currentMeaning(value)&&<p className="mt-2 text-xs text-slate-500">Suggested meaning · Review before relying on it</p>}<nav aria-label="Dataset detail sections" className="my-5 flex flex-wrap gap-1 border-b">{['Overview','Columns','Relationships','Quality'].map(name=><button key={name} aria-current={tab===name?'page':undefined} className={`min-h-11 px-3 text-sm ${tab===name?'border-b-2 border-indigo-600 text-indigo-700':'text-slate-600'}`} onClick={()=>setTab(name)}>{name}</button>)}</nav>
    {tab==='Overview'&&<dl className="grid grid-cols-2 gap-5 text-sm">{[['Rows',summary?.row_count.toLocaleString()??'Unavailable'],['Columns',summary?.column_count??'Unavailable'],['Business key',summary?.business_key.join(', ')||'Not configured'],['Time field',summary?.event_time_column||'Not configured'],['Last updated',Number.isNaN(Date.parse(value.dataset.updatedAt))?'Unavailable':new Date(value.dataset.updatedAt).toLocaleString()],['Current status',value.dataset.status==='ARCHIVED'?'Archived':summary?'Ready':'Needs preparation'],['Last profiled',summary&&value.profile.profile_timestamp?new Date(value.profile.profile_timestamp).toLocaleString():'Unavailable']].map(([label,text])=><div key={label} className="min-w-0 break-words"><dt className="text-slate-500">{label}</dt><dd className="mt-1 font-medium">{text}</dd></div>)}</dl>}
    {tab==='Columns'&&<ModelColumns value={value} relationships={related}/>}
    {tab==='Relationships'&&<div className="space-y-5"><h3 className="font-semibold">Relationships ({related.length})</h3>{related.length?related.map(r=><article key={r.id} className="border-b pb-4"><RelationshipDetails relation={r}/></article>):<p>No current relationships for this dataset.</p>}</div>}
    {tab==='Quality'&&<Quality workspace={workspace} id={value.dataset.id}/>}
    <footer className="mt-8 border-t pt-4"><p className="text-sm font-semibold">Next step</p>{value.dataset.status==='ARCHIVED'?<Link className="inline-flex min-h-11 items-center text-indigo-700" to={`${base}/datasets/${value.dataset.id}`}>View dataset</Link>:summary?<div className="flex flex-wrap gap-4"><Link className="inline-flex min-h-11 items-center text-indigo-700" to={`${base}/analytics`}>Explore analytics</Link><Link className="inline-flex min-h-11 items-center text-indigo-700" to={`${base}/analytics#ask-workspace-title`}>Ask a question</Link>{related.some(r=>r.suggested)&&<Link className="inline-flex min-h-11 items-center text-indigo-700" to={`${base}/relationships`}>Review suggested relationships</Link>}</div>:<Link className="inline-flex min-h-11 items-center text-indigo-700" to={`${base}/datasets/${value.dataset.id}/profile`}>Prepare data</Link>}</footer></>}
  </div></div>
}
