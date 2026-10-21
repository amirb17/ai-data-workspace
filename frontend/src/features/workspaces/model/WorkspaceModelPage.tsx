import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ApiFeedback } from '../../../components/ui/ApiFeedback'
import { getWorkspaceModel } from '../../../services/api/workspaceModel'
import { getRelationships } from '../../../services/api/relationships'
import { useApiResource } from '../../../services/api/useApiResource'
import { ModelGraph } from './ModelGraph'
import { ModelDetails } from './ModelDetails'
import { currentMeaning, currentProfile, description, modelRelationships, relationshipsNeedRefresh, relationshipText, type ModelDataset, type ModelRelationship } from './modelView'

export function WorkspaceModelPage() {
  const {workspaceId=''}=useParams()
  const load=useCallback(async(signal:AbortSignal)=>{
    const [values,relationships]=await Promise.all([getWorkspaceModel(workspaceId,signal),getRelationships(workspaceId,signal)])
    return {values,relationships}
  },[workspaceId])
  const resource=useApiResource(`workspace-model:${workspaceId}`,load)
  const {data,retry}=resource
  useEffect(()=>{
    if(!data)return
    const controller=new AbortController();let reading=false
    const check=async()=>{
      if(reading)return;reading=true
      try {const next=await load(controller.signal);if(!controller.signal.aborted&&JSON.stringify(next)!==JSON.stringify(data))retry()}
      catch {if(!controller.signal.aborted)retry()}
      finally {reading=false}
    }
    const timer=window.setInterval(()=>void check(),60000)
    window.addEventListener('focus',check)
    return ()=>{controller.abort();window.clearInterval(timer);window.removeEventListener('focus',check)}
  },[data,load,retry])
  const visibleRelationships=data?modelRelationships(data.relationships,data.values):[]
  return data?<ModelContent key={workspaceId} workspace={workspaceId} datasets={data.values} relationships={visibleRelationships} relationshipsReady={data.relationships.status==='READY'} stale={relationshipsNeedRefresh(data.relationships,visibleRelationships)} reload={retry}/>:<ApiFeedback loading="Loading your data model…" error={resource.error} retry={retry}/>
}
export function ModelContent({workspace,datasets,relationships,stale,reload,relationshipsReady=true}: {workspace:string;datasets:ModelDataset[];relationships:ModelRelationship[];stale:boolean;reload:()=>void;relationshipsReady?:boolean}) {
  const [selected,setSelected]=useState<number|null>(null),[relation,setRelation]=useState<ModelRelationship>(),[search,setSearch]=useState(''),[suggestions,setSuggestions]=useState(false),[list,setList]=useState(false),[detailsOpen,setDetailsOpen]=useState(false)
  const confirmed=relationships.filter(r=>!r.suggested), proposed=relationships.filter(r=>r.suggested),shown=suggestions?proposed:confirmed
  const ready=datasets.filter(currentMeaning).length, needsRefresh=stale||datasets.some(v=>v.profile.freshness==='STALE'||v.meaning.status==='STALE')
  const base=`/app/workspaces/${workspace}`
  const select=(id:number)=>{setSelected(id<0?null:id);setRelation(undefined);setDetailsOpen(id>=0)}
  const matching=datasets.filter(d=>d.dataset.name.toLowerCase().includes(search.toLowerCase()))
  return <section className="min-w-0 space-y-5">
    <header><h1 className="text-2xl font-semibold text-slate-900">How your data connects</h1><p className="mt-2 text-sm text-slate-600">See what each dataset represents and how your datasets relate.</p><p className="mt-3 text-sm text-slate-600">{datasets.length} datasets · {confirmed.length} confirmed relationships · {needsRefresh?'Some model information needs refresh':ready===datasets.length&&ready>0&&relationshipsReady?'All model information current':`${ready} of ${datasets.length} dataset descriptions prepared`}</p></header>
    {(!ready||needsRefresh||ready<datasets.length)&&<aside className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm"><p>{needsRefresh?'Some model information needs refresh.':!ready?"Data model isn't ready yet.":'Some dataset descriptions are not available yet.'} Prepare your datasets so DataRise can describe and connect them.</p><Link className="inline-flex min-h-11 items-center font-medium text-indigo-700" to={`${base}/datasets`}>Prepare data</Link>{needsRefresh&&<Link className="ml-4 inline-flex min-h-11 items-center text-indigo-700" to={`${base}/understanding`}>Refresh model</Link>}</aside>}
    <div className="flex flex-wrap items-end gap-3"><label className="min-w-0 flex-1 text-sm">Search datasets<input placeholder="Search datasets…" className="mt-1 block min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3" value={search} onChange={e=>setSearch(e.target.value)}/></label><button aria-pressed={!suggestions} className="min-h-11 rounded-lg border bg-white px-3 text-sm" onClick={()=>setSuggestions(false)}>Confirmed ({confirmed.length})</button><button aria-pressed={suggestions} className="min-h-11 rounded-lg border bg-white px-3 text-sm" onClick={()=>setSuggestions(true)}>Suggested relationships ({proposed.length})</button><button aria-pressed={list} className="hidden min-h-11 rounded-lg border bg-white px-3 text-sm md:block" onClick={()=>setList(v=>!v)}>{list?'View graph':'Relationship list'}</button><button className="min-h-11 rounded-lg border bg-white px-3 text-sm" onClick={reload}>Check for updates</button></div>
    {suggestions&&<p className="text-sm text-amber-800">Dashed connections are suggestions and need review. They are excluded from the confirmed model. <Link className="underline" to={`${base}/relationships`}>Review relationship suggestions</Link></p>}
    {!shown.length&&<p className="rounded-xl border border-slate-200 bg-white p-4 text-sm">{suggestions?'No current relationship suggestions.':'No confirmed current relationships yet.'} <Link className="text-indigo-700 underline" to={`${base}/relationships`}>Review relationship evidence</Link></p>}
    {!datasets.length&&<p className="rounded-xl border bg-white p-5">Add datasets to start building your model. <Link className="text-indigo-700 underline" to={`${base}/datasets`}>Add data</Link></p>}
    {datasets.length>0&&!list&&<div className="hidden overflow-hidden rounded-xl border border-slate-200 bg-white md:block"><ModelGraph datasets={datasets} relationships={shown} layoutRelationships={relationships} selected={selected} select={select} selectRelationship={r=>{setRelation(r);setSelected(null);setDetailsOpen(true)}} search={search}/></div>}
    <div className={`${list?'':'md:hidden'} grid gap-3 sm:grid-cols-2`}>{matching.map(value=><button key={value.dataset.id} className="min-w-0 rounded-xl border border-slate-200 bg-white p-4 text-left hover:border-indigo-400" onClick={()=>select(value.dataset.id)}><div className="flex justify-between gap-2"><strong className="break-words">{value.dataset.name}</strong><span className={`text-xs ${currentProfile(value)?'text-green-700':'text-amber-800'}`}>{value.dataset.status==='ARCHIVED'?'Archived':currentProfile(value)?'Ready':'Needs preparation'}</span></div><p className="mt-2 text-xs text-slate-500">{currentProfile(value)&&value.profile.summary?`${value.profile.summary.row_count.toLocaleString()} rows · Key: ${value.profile.summary.business_key.join(', ')||'Not configured'}`:'Row count unavailable'}</p><p className="mt-2 text-sm text-slate-600">{description(value)}</p><p className="mt-2 text-xs text-slate-500">Connected to: {shown.filter(r=>r.evidence.parent.dataset_id===value.dataset.id||r.evidence.child.dataset_id===value.dataset.id).map(r=>r.evidence.parent.dataset_id===value.dataset.id?r.evidence.child.dataset_name:r.evidence.parent.dataset_name).join(', ')||'No current connections'}</p></button>)}</div>
    {!matching.length&&search&&<p role="status">No datasets match your search.</p>}
    <details open={list} className="rounded-xl border border-slate-200 bg-white p-4"><summary className="min-h-11 cursor-pointer py-2 font-medium">Accessible dataset and relationship list</summary><div className="mt-3 flex flex-wrap gap-2">{matching.map(v=><button className="min-h-11 rounded-lg border px-3 text-sm" key={v.dataset.id} onClick={()=>select(v.dataset.id)}>Inspect {v.dataset.name}</button>)}</div><ul className="mt-3 space-y-2">{shown.map(r=><li key={r.id}><button className="min-h-11 w-full rounded-lg p-3 text-left hover:bg-slate-50" onClick={()=>{setRelation(r);setSelected(null);setDetailsOpen(true)}}><span className="font-medium">{r.evidence.parent.dataset_name} → {r.evidence.child.dataset_name}</span><span className="block text-sm text-slate-600">{relationshipText(r.evidence)} · {r.evidence.parent.columns.join(', ')} → {r.evidence.child.columns.join(', ')} · {r.suggested?'Needs review':'Confirmed'}</span></button></li>)}</ul></details>
    <p className="text-xs text-slate-500">Connections represent relationships between datasets. Suggested dataset and column meanings require review.</p>
    {detailsOpen&&(selected!==null||relation)&&<ModelDetails key={selected??relation?.id} workspace={workspace} value={datasets.find(d=>d.dataset.id===selected)} relation={relation} relationships={relationships} close={()=>{setDetailsOpen(false);setRelation(undefined)}}/>}
  </section>
}
