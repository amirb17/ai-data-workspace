import {useCallback,useState} from 'react'
import {Link,useParams} from 'react-router-dom'
import {getWarehouseCatalog} from '../../../services/api/warehouseCatalog'
import {useApiResource} from '../../../services/api/useApiResource'
import {usePublicReadOnly} from '../../../services/publicLaunchContext'
import {useWarehouseQuery} from './useWarehouseQuery'
import {WarehouseQueryResults} from './WarehouseQueryResults'
const button='min-h-11 rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium disabled:opacity-50'
export function WarehouseSqlPage(){const {workspaceId=''}=useParams();return <WarehouseSqlWorkspace workspace={workspaceId}/>}
export function WarehouseSqlWorkspace({workspace}:{workspace:string}){return <WarehouseSqlContent key={workspace} workspace={workspace}/>}
function WarehouseSqlContent({workspace}:{workspace:string}){
 const load=useCallback((signal:AbortSignal)=>getWarehouseCatalog(workspace,signal),[workspace])
 const catalog=useApiResource('warehouse-sql:'+workspace,load),q=useWarehouseQuery(workspace),readOnly=usePublicReadOnly()
 const [resume,setResume]=useState('')
 const model=catalog.data?.models.find(m=>m.model_id===q.model),disabled=!!q.busy||q.blocked||readOnly
 const template=()=>{if(model)q.change(model.model_id,`SELECT ${model.columns.slice(0,32).map(c=>'"'+c.name.replaceAll('"','""')+'"').join(', ')} FROM model_${model.model_id} LIMIT 100`)}
 return <section className="min-w-0 space-y-5 text-slate-900">
  <header><h1 className="text-2xl font-semibold">Warehouse SQL</h1><p className="mt-2 text-sm text-slate-600">Query approved warehouse models. This is separate from <Link className="underline text-indigo-700" to={`/app/workspaces/${workspace}/sql`}>dataset / Gold SQL</Link>.</p></header>
  <aside role="note" className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-950">Live BigQuery execution remains disabled. A controlled backend must be explicitly available for this workflow; this screen does not enable it. Approved definitions do not prove serving readiness.{readOnly&&' Public read-only mode blocks query operations.'}</aside>
  {!q.blocked&&catalog.error&&<div role="alert">{catalog.error}<button className={button} onClick={catalog.retry}>Retry models</button></div>}
  {!q.blocked&&!catalog.data&&!catalog.error&&<p role="status">Loading approved warehouse models...</p>}
  {!q.blocked&&catalog.data&&<div className="grid min-w-0 gap-5 lg:grid-cols-[minmax(15rem,1fr)_minmax(0,3fr)]">
   <aside className="min-w-0 rounded-xl border bg-white p-4"><h2 className="font-semibold">Approved FACT / DIMENSION models</h2>
    {!catalog.data.models.length&&<p className="mt-3 text-sm">No approved warehouse models are available.</p>}
    <label className="mt-3 block text-sm">Warehouse model<select aria-label="Warehouse model" className="mt-2 min-h-11 w-full rounded border p-2" value={q.model||''} disabled={disabled||q.locked} onChange={e=>q.change(Number(e.target.value),'')}><option value="">Select a model</option>{catalog.data.models.map(m=><option key={m.model_id} value={m.model_id}>{m.name} ({m.model_type}, v{m.version_number})</option>)}</select></label>
    {model&&<><p className="my-3 break-all font-mono text-sm">model_{model.model_id}</p><ul className="space-y-3 text-sm">{model.columns.map(c=><li className="break-words" key={c.name}><strong>{c.name}</strong><span className="block text-slate-600">{c.type} / {c.role}</span></li>)}</ul></>}
   </aside>
   <div className="min-w-0 space-y-4"><section className="rounded-xl border bg-white p-4"><h2 className="font-semibold">Bounded single-model query</h2><p id="warehouse-grammar" className="my-3 text-sm text-slate-600">SELECT explicit columns FROM model_id LIMIT 1-500. No joins, filters, aggregates, wildcards or physical table names. Server budgets may be stricter.</p>
    <button className={button} disabled={disabled||q.locked||!model} onClick={template}>Use selected model columns</button>
    <label className="mt-3 block text-sm" htmlFor="warehouse-sql-editor">Warehouse SQL editor</label><textarea id="warehouse-sql-editor" aria-describedby="warehouse-grammar" spellCheck={false} maxLength={4096} className="mt-2 min-h-52 w-full resize-y rounded-lg border p-3 font-mono text-sm" value={q.sql} disabled={disabled||q.locked} onChange={e=>q.change(q.model,e.target.value)}/>
    <div className="mt-3 flex flex-wrap gap-3"><button className={button} disabled={disabled||q.locked||!model||!q.sql.trim()} onClick={()=>void q.validate()}>Validate SQL</button><button className={button} disabled={disabled||!!q.handle||q.validation?.budget_decision!=='ALLOWED'} onClick={()=>void q.admit()}>{q.locked&&!q.handle?'Retry original admission':'Admit query'}</button><button className={button} disabled={disabled||(q.locked&&!q.terminal)} onClick={q.reset}>New query</button></div>
    {q.locked&&!q.handle&&<p className="mt-3 text-sm">Keep this original request for retries. Do not create a replacement while its admission is unresolved.</p>}
    {q.validation&&<div className="mt-3 rounded border p-3 text-sm" role="status"><strong>Validation: {q.validation.budget_decision}</strong><p>Estimated bytes: {q.validation.estimated_bytes.toLocaleString()} / Row limit: {q.validation.row_limit}</p><p>{q.validation.reason}</p><p>Validation does not execute or admit a query.</p></div>}
   </section>
   {!q.handle&&!q.locked&&<form className="rounded-xl border bg-white p-4" onSubmit={e=>{e.preventDefault();void q.resume(resume.trim())}}><label className="block text-sm" htmlFor="warehouse-resume">Resume original query handle</label><input id="warehouse-resume" className="my-2 min-h-11 w-full rounded border p-2 font-mono" maxLength={32} value={resume} disabled={disabled} onChange={e=>setResume(e.target.value)}/><button className={button} disabled={disabled||!resume.trim()}>Check original query</button></form>}
   {q.handle&&<section className="rounded-xl border bg-white p-4" aria-label="Original warehouse query"><h2 className="font-semibold">Original query</h2><p className="my-2 break-all font-mono text-sm">{q.handle}</p><p role="status">Status: {q.status?.status||'Admitted; status not yet checked'}</p>
    {q.status?.status==='UNCERTAIN'&&<p className="my-2 text-sm text-amber-800">Execution is uncertain. Refresh this original attempt; do not submit a replacement. Missing evidence never proves non-execution.</p>}
    {q.status?.status==='EXPIRED'&&<p className="my-2 text-sm">The query or its result access window expired. Expiration does not authorize execution or deletion.</p>}
    <div className="mt-3 flex flex-wrap gap-3"><button className={button} disabled={disabled||q.sent||q.terminal} onClick={()=>void q.update('execute')}>Execute original query</button><button className={button} disabled={disabled} onClick={()=>void q.update('refresh')}>Refresh original status</button><button className={button} disabled={disabled||q.terminal||!q.status||!['RUNNING','UNCERTAIN'].includes(q.status.status)} onClick={()=>void q.update('cancel')}>Request cancellation</button><button className={button} disabled={disabled||!q.status?.results_available||q.status.status!=='SUCCEEDED'} onClick={()=>void q.loadResults()}>Load verified results</button></div>
   </section>}
   </div>
  </div>}
  {q.busy&&<p role="status">{q.busy}...</p>}{q.error&&<p role="alert" className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-900">{q.error}</p>}
  {q.results&&<WarehouseQueryResults page={q.results}/>}
 </section>
}
