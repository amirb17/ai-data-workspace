import {useCallback,useMemo,useState} from 'react'
import {Link,useParams} from 'react-router-dom'
import {getWarehouseCatalog} from '../../../services/api/warehouseCatalog'
import type {WarehouseModel} from '../../../services/api/warehouseCatalog'
import {useApiResource} from '../../../services/api/useApiResource'
import {usePublicReadOnly} from '../../../services/publicLaunchContext'
import {useWarehouseQuery} from './useWarehouseQuery'
import {WarehouseQueryResults} from './WarehouseQueryResults'

const button='min-h-11 rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-800 transition-colors hover:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-600 disabled:cursor-not-allowed disabled:opacity-50'
const primary='min-h-11 rounded-lg bg-indigo-700 px-5 py-2 text-sm font-semibold text-white transition-colors hover:bg-indigo-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-600 disabled:cursor-not-allowed disabled:opacity-40'
const fixedTypes=new Set(['STRING','INTEGER','INT64','NUMERIC','DECIMAL','BOOLEAN','BOOL','DATE','TIMESTAMP'])
const quote=(name:string)=>'"'+name.replaceAll('"','""')+'"'
const bytes=(count:number)=>count>=1024*1024*1024?(count/(1024**3)).toFixed(2)+' GiB':count>=1024*1024?(count/(1024**2)).toFixed(2)+' MiB':count>=1024?(count/1024).toFixed(1)+' KiB':count+' B'
function example(model:WarehouseModel, names?:string[]) {
 const fields=(names?.length?names:model.columns.slice(0,32).map(c=>c.name))
 return 'SELECT '+fields.map(quote).join(', ')+' FROM model_'+model.model_id+' LIMIT 100'
}
function stageName(number:number){return ['Choose model','Write SQL','Validate & budget','Admit & execute','Inspect results'][number]}

export function WarehouseSqlPage(){const {workspaceId=''}=useParams();return <WarehouseSqlWorkspace workspace={workspaceId}/>}
export function WarehouseSqlWorkspace({workspace}:{workspace:string}){return <WarehouseSqlContent key={workspace} workspace={workspace}/>}

function WarehouseSqlContent({workspace}:{workspace:string}){
 const load=useCallback((signal:AbortSignal)=>getWarehouseCatalog(workspace,signal),[workspace])
 const catalog=useApiResource('warehouse-sql:'+workspace,load)
 const q=useWarehouseQuery(workspace)
 const readOnly=usePublicReadOnly()
 const [resume,setResume]=useState('')
 const [search,setSearch]=useState('')
 const [showGuide,setShowGuide]=useState(false)
 const model=catalog.data?.models.find(m=>m.model_id===q.model)
 const visible=useMemo(()=>catalog.data?.models.filter(m=>
  (m.name+' '+m.model_type+' model_'+m.model_id).toLowerCase().includes(search.toLowerCase().trim()))||[],[catalog.data,search])
 const selectedUnsupported=model?.columns.filter(c=>!fixedTypes.has(c.type.toUpperCase()))||[]
 const unsupportedSelected=selectedUnsupported.length>0&&model?.columns.length===selectedUnsupported.length
 const disabled=!!q.busy||q.blocked||readOnly
 const canEdit=!disabled&&!q.locked
 const stage=q.results?4:q.handle?3:q.validation?2:q.sql.trim()?1:0
 const makeQuery=(names?:string[])=>{if(model)q.change(model.model_id,example(model,names))}
 const errorHint=q.error&&model&&selectedUnsupported.length>0
 const step=(number:number)=><div key={number} className="flex min-w-0 items-center gap-2">
  <span className={'flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-xs font-semibold '+(stage>=number?'border-indigo-700 bg-indigo-700 text-white':'border-slate-300 bg-white text-slate-500')}>{stage>number?'✓':number+1}</span>
  <span className={'hidden text-xs font-medium lg:inline '+(stage>=number?'text-slate-900':'text-slate-500')}>{stageName(number)}</span>
 </div>
 return <section className="min-w-0 space-y-5 text-slate-900">
  <header className="flex flex-wrap items-start justify-between gap-3">
   <div><div className="mb-2 text-xs font-semibold uppercase tracking-wider text-indigo-700">Analytics / Warehouse</div>
    <h1 className="text-2xl font-bold tracking-tight">Warehouse SQL workspace</h1>
    <p className="mt-2 max-w-3xl text-sm text-slate-600">Explore approved analytical models, validate a bounded query, review the scan estimate, then execute and inspect verified results. For dataset-level SQL, use <Link className="font-medium text-indigo-700 underline" to={`/app/workspaces/${workspace}/sql`}>dataset / Gold SQL</Link>.</p>
   </div>
   <button className={button} onClick={()=>setShowGuide(v=>!v)} aria-expanded={showGuide}>{showGuide?'Hide workflow guide':'How this works'}</button>
  </header>
  {showGuide&&<section className="rounded-xl border border-indigo-200 bg-indigo-50 p-4 text-sm text-slate-800" aria-label="Warehouse workflow guide">
   <h2 className="font-semibold">How teams query a real warehouse</h2>
   <p className="mt-2">Data engineers publish tested fact and dimension models. Analysts discover a trusted model, inspect its grain and columns, validate SQL and estimated bytes, run a governed query, then use the result in a dashboard or business decision. Read-only query access is separate from pipeline administration.</p>
   <p className="mt-2">This workspace currently supports <strong>one approved logical model</strong>, explicitly selected columns and a literal LIMIT. It does not yet support free-form joins, WHERE, GROUP BY, aggregations or direct physical BigQuery table names. Those capabilities must be supported by the backend compiler before the editor can offer them.</p>
  </section>}
  <section className="rounded-xl border border-slate-200 bg-white px-4 py-3" aria-label="Query progress">
   <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="text-sm font-semibold">Query lifecycle</h2><span className="text-xs text-slate-500">BigQuery · asia-south1 · governed read-only</span></div>
   <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2">{[0,1,2,3,4].map((n)=><div key={n} className="flex items-center gap-3">{step(n)}{n!==4&&<span className="h-px w-4 bg-slate-200" aria-hidden="true"/>}</div>)}</div>
  </section>
  {readOnly&&<p className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">Public read-only mode blocks query operations.</p>}
  {q.blocked&&<p role="alert" className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-900">Session or access changed. Sign in again before continuing.</p>}
  {!q.blocked&&catalog.error&&<div role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm">{catalog.error} <button className={button} onClick={catalog.retry}>Retry models</button></div>}
  {!q.blocked&&!catalog.data&&!catalog.error&&<p role="status" className="rounded-xl border bg-white p-6 text-sm text-slate-600">Loading approved warehouse models...</p>}
  {!q.blocked&&catalog.data&&<div className="grid min-w-0 items-start gap-5 lg:grid-cols-[minmax(17rem,1fr)_minmax(0,2.4fr)]">
   <aside className="min-w-0 space-y-4 rounded-xl border border-slate-200 bg-white p-4" aria-label="Warehouse model explorer">
    <div className="flex items-center justify-between gap-3"><h2 className="font-semibold">Model explorer</h2><span className="text-xs text-slate-500">{catalog.data.models.length} approved</span></div>
    <p className="text-xs text-slate-500">Choose a business-ready fact or dimension model. This lists logical models, not arbitrary BigQuery tables.</p>
    <label className="block text-xs font-semibold text-slate-600" htmlFor="warehouse-model-search">Find a model</label>
    <input id="warehouse-model-search" type="search" placeholder="Search models..." value={search} onChange={e=>setSearch(e.target.value)} disabled={disabled} className="min-h-10 w-full rounded-lg border border-slate-300 px-3 text-sm"/>
    <label className="block text-sm font-medium" htmlFor="warehouse-model-select">Warehouse model</label>
    <select id="warehouse-model-select" aria-label="Warehouse model" className="min-h-11 w-full rounded-lg border border-slate-300 p-2 text-sm" value={q.model||''} disabled={disabled||q.locked} onChange={e=>q.change(Number(e.target.value),'')}>
     <option value="">Select a model</option>
     {visible.map(m=><option key={m.model_id} value={m.model_id}>{m.name} ({m.model_type}, v{m.version_number})</option>)}
     {model&&!visible.some(m=>m.model_id===model.model_id)&&<option value={model.model_id}>{model.name} ({model.model_type}, v{model.version_number})</option>}
    </select>
    {!catalog.data.models.length&&<p className="rounded-lg bg-slate-50 p-3 text-sm">No approved models yet. Publish and approve a fact or dimension model from the Model workspace first.</p>}
    {model&&<div className="space-y-3 border-t pt-4">
     <div className="flex items-center justify-between gap-2"><div><p className="font-semibold">{model.name}</p><p className="font-mono text-xs text-slate-500">model_{model.model_id} · version {model.version_number}</p></div><span className="rounded-md bg-indigo-50 px-2 py-1 text-xs font-semibold text-indigo-700">{model.model_type}</span></div>
     <dl className="grid grid-cols-2 gap-2 text-xs"><div className="rounded-lg bg-slate-50 p-2"><dt className="text-slate-500">Model contract</dt><dd className="mt-1 font-semibold">Approved</dd></div><div className="rounded-lg bg-slate-50 p-2"><dt className="text-slate-500">Serving metadata</dt><dd className="mt-1 font-semibold">{model.resource_registered?'Not independently verified':'Not registered'}</dd></div></dl>
     {model.grain&&<p className="text-xs text-slate-600"><strong>Grain:</strong> {model.grain}</p>}
     {model.sources.length>0&&<p className="text-xs text-slate-600"><strong>Sources:</strong> {model.sources.map(s=>s.dataset_name).join(', ')}</p>}
     <div className="flex items-center justify-between"><h3 className="text-sm font-semibold">Columns ({model.columns.length})</h3><span className="text-xs text-slate-500">Select to query</span></div>
     <ul className="max-h-80 space-y-1 overflow-y-auto">{model.columns.map(c=><li key={c.name}><button type="button" disabled={!canEdit} onClick={()=>makeQuery([c.name])} title={`Create a query for ${c.name}`} className="flex w-full items-start justify-between gap-2 rounded-lg p-2 text-left hover:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-600 disabled:cursor-not-allowed">
      <span className="min-w-0"><strong className="block break-all font-mono text-xs">{c.name}</strong><span className="text-xs text-slate-500">{c.role}</span></span>
      <span className={'shrink-0 rounded px-1.5 py-0.5 font-mono text-[11px] '+(fixedTypes.has(c.type.toUpperCase())?'bg-slate-100 text-slate-600':'bg-amber-50 text-amber-800')}>{c.type}</span>
     </button></li>)}</ul>
    </div>}
   </aside>
   <div className="min-w-0 space-y-4">
    <section className="rounded-xl border border-slate-200 bg-white p-4 sm:p-5" aria-label="Warehouse SQL editor section">
     <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="font-semibold">SQL editor</h2><p className="mt-1 text-xs text-slate-500">Logical, read-only query · one model · maximum 500 requested rows</p></div>
      <button className={button} disabled={!canEdit||!model} onClick={()=>makeQuery()}>Use selected model columns</button></div>
     <div className="mt-4 flex flex-wrap gap-2 text-xs"><span className="rounded-md bg-slate-100 px-2 py-1 font-mono">SELECT columns</span><span className="rounded-md bg-slate-100 px-2 py-1 font-mono">FROM model_ID</span><span className="rounded-md bg-slate-100 px-2 py-1 font-mono">LIMIT 1–500</span><span className="rounded-md bg-amber-50 px-2 py-1 text-amber-800">No joins / aggregates yet</span></div>
     <label className="mt-4 block text-sm font-medium" htmlFor="warehouse-sql-editor">Warehouse SQL editor</label>
     <textarea id="warehouse-sql-editor" spellCheck={false} maxLength={4096} className="mt-2 min-h-52 w-full resize-y rounded-lg border border-slate-300 bg-slate-50 p-4 font-mono text-sm leading-6 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-100" placeholder={'SELECT "id" FROM model_1 LIMIT 100'} value={q.sql} disabled={disabled||q.locked} onChange={e=>q.change(q.model,e.target.value)}/>
     {model&&selectedUnsupported.length>0&&<div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-950" role="note">
      <strong>Result type compatibility needs attention.</strong> {unsupportedSelected?'All columns':'Some columns'} in this model use types the current named-results contract does not support ({[...new Set(selectedUnsupported.map(c=>c.type))].join(', ')}). The SQL may be syntactically valid while live validation still rejects the projected result schema. Choose a compatible model/columns; supporting text results requires a reviewed backend change.
     </div>}
     <div className="mt-4 flex flex-wrap items-center gap-3">
      <button className={primary} disabled={disabled||q.locked||!model||!q.sql.trim()} onClick={()=>void q.validate()}>Validate SQL</button>
      <button className={button} disabled={disabled||!!q.handle||q.validation?.budget_decision!=='ALLOWED'} onClick={()=>void q.admit()}>{q.locked&&!q.handle?'Retry original admission':'Admit query'}</button>
      <button className={button} disabled={disabled||(q.locked&&!q.terminal)} onClick={q.reset}>New query</button>
     </div>
     {q.locked&&!q.handle&&<p className="mt-3 text-sm text-amber-800">Keep this original request for retries. Do not create a replacement while admission is unresolved.</p>}
     {q.validation&&<div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm" role="status">
      <div className="flex flex-wrap items-center justify-between gap-2"><strong>Validation: {q.validation.budget_decision}</strong><span className="font-mono text-xs">{bytes(q.validation.estimated_bytes)} estimated</span></div>
      <p className="mt-2 text-xs text-slate-600">Estimated bytes: {q.validation.estimated_bytes.toLocaleString()}</p>
      <div className="mt-3 grid grid-cols-2 gap-3 text-xs"><div><span className="text-slate-500">Requested rows</span><p className="mt-1 font-semibold">{q.validation.row_limit}</p></div><div><span className="text-slate-500">Governance</span><p className="mt-1 font-semibold">{q.validation.reason}</p></div></div>
      <p className="mt-3 text-xs text-slate-600">Validation estimates scan bytes; it does not execute the query. Actual billable bytes may differ.</p>
     </div>}
    </section>
    {q.handle&&<section className="rounded-xl border border-slate-200 bg-white p-4" aria-label="Original warehouse query">
     <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="font-semibold">Query execution</h2><span className="rounded-md bg-slate-100 px-2 py-1 text-xs font-semibold">{q.status?.status||'ADMITTED'}</span></div>
     <p className="mt-2 text-xs text-slate-500">Original attempt handle</p><p className="my-1 break-all font-mono text-xs">{q.handle}</p>
     <p role="status" className="mt-2 text-sm">Status: {q.status?.status||'Admitted; status not yet checked'}</p>
     {q.status?.status==='UNCERTAIN'&&<p className="mt-2 text-sm text-amber-800">Execution is uncertain. Refresh this original attempt; do not submit a replacement. Missing evidence never proves non-execution.</p>}
     {q.status?.status==='EXPIRED'&&<p className="mt-2 text-sm">The query or result access window expired. Expiration does not authorize another submission.</p>}
     <div className="mt-4 flex flex-wrap gap-3">
      <button className={primary} disabled={disabled||q.sent||q.terminal} onClick={()=>void q.update('execute')}>Execute original query</button>
      <button className={button} disabled={disabled} onClick={()=>void q.update('refresh')}>Refresh original status</button>
      <button className={button} disabled={disabled||q.terminal||!q.status||!['RUNNING','UNCERTAIN'].includes(q.status.status)} onClick={()=>void q.update('cancel')}>Request cancellation</button>
      <button className={button} disabled={disabled||!q.status?.results_available||q.status.status!=='SUCCEEDED'} onClick={()=>void q.loadResults()}>Load verified results</button>
     </div>
    </section>}
    {!q.handle&&!q.locked&&<form className="rounded-xl border border-slate-200 bg-white p-4" onSubmit={e=>{e.preventDefault();void q.resume(resume.trim())}}>
     <h2 className="text-sm font-semibold">Resume an existing query</h2><p className="mt-1 text-xs text-slate-500">Use the original attempt handle to check status without admitting another query.</p>
     <label className="mt-3 block text-sm" htmlFor="warehouse-resume">Resume original query handle</label>
     <input id="warehouse-resume" className="my-2 min-h-11 w-full rounded-lg border border-slate-300 p-2 font-mono text-sm" maxLength={32} value={resume} disabled={disabled} onChange={e=>setResume(e.target.value)}/>
     <button className={button} disabled={disabled||!resume.trim()}>Check original query</button>
    </form>}
    {q.busy&&<p role="status" className="text-sm text-indigo-700">{q.busy}...</p>}
    {q.error&&<div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-900">
     <p className="font-medium">{q.error}</p>
     {errorHint&&<p className="mt-2 text-xs">This selected model includes a result type unsupported by the current named-result contract. Try a compatible numeric/date column; if all columns are text, this model needs backend STRING support.</p>}
    </div>}
   </div>
  </div>}
  {q.results&&<WarehouseQueryResults page={q.results}/>}
 </section>
}
