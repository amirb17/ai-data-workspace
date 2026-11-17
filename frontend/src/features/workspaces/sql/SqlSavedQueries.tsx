import {useState} from 'react'
import type {SavedSql,SqlExecution} from '../../../services/api/sqlAnalytics'
import {SmallSelect} from '../../../components/ui/SearchSelect'

export function SqlSavedQueries({queries,history,kind,open,openHistory,duplicate,remove,rename}: {
  queries:SavedSql[]
  history:SqlExecution[]
  kind:'saved'|'history'
  open:(query:SavedSql)=>void
  openHistory:(execution:SqlExecution)=>void
  duplicate:(query:SavedSql)=>void
  remove:(query:SavedSql)=>void
  rename:(query:SavedSql)=>void
}) {
  const [search,setSearch]=useState(''),[status,setStatus]=useState('ALL')
  const matches=(text:string)=>text.toLowerCase().includes(search.trim().toLowerCase())
  const saved=queries.filter(q=>matches(q.name+' '+q.description+' '+q.sql_text))
  const named=new Map(queries.map(q=>[q.query_id,q.name]))
  const entries=history.filter(e=>(status==='ALL'||e.status===status)&&matches(
    [e.sql_text||'',e.saved_query_id?named.get(e.saved_query_id)||'':'',
      ...e.sources.map(source=>source.name)].join(' ')
  ))
  return <div className="space-y-4">
    <label className="block text-xs text-slate-600">{kind==='saved'?'Search saved queries':'Search query history'}
      <input value={search} onChange={e=>setSearch(e.target.value)} className="mt-1 min-h-11 w-full rounded-lg border px-3 text-sm" placeholder={kind==='history'?'Search SQL, query name or dataset…':'Search name, description or SQL…'}/>
    </label>
    {kind==='history'&&<SmallSelect label="History status" value={status} onChange={setStatus} options={['ALL','SUCCEEDED','BLOCKED','FAILED','TIMED_OUT'].map(value=>({value,label:value==='ALL'?'All executions':value.toLowerCase().replaceAll('_',' ')}))}/>}
    {kind==='saved'?saved.map(q=><article key={q.query_id} className="rounded-xl border p-4">
      <h3 className="break-words font-medium">{q.name}</h3>
      <p className="mt-1 text-xs text-slate-500">Modified {new Date(q.updated_at).toLocaleString()}</p>
      {q.description&&<p className="mt-2 break-words text-sm text-slate-600">{q.description}</p>}
      <div className="mt-3 flex flex-wrap gap-2">{[{label:'Open',action:()=>open(q)},{label:'Rename',action:()=>rename(q)},{label:'Duplicate',action:()=>duplicate(q)},{label:'Delete…',action:()=>remove(q)}].map(a=><button key={a.label} onClick={a.action} className={`min-h-11 rounded-lg border px-3 text-sm ${a.label==='Delete…'?'text-red-700':'text-indigo-700'}`}>{a.label}</button>)}</div>
    </article>):entries.map(e=><article key={e.execution_id} className="rounded-xl border border-slate-200 p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="break-words text-sm font-semibold text-slate-900">{e.saved_query_id&&named.get(e.saved_query_id)||`SQL execution #${e.execution_id}`}</h3>
          <p className="mt-1 text-xs text-slate-500">{new Date(e.executed_at).toLocaleString()}</p>
        </div>
        <span className={`rounded-full px-2 py-1 text-xs font-medium ${e.status==='SUCCEEDED'?'bg-emerald-50 text-emerald-700':e.status==='BLOCKED'?'bg-amber-50 text-amber-800':'bg-red-50 text-red-700'}`}>{e.status.toLowerCase().replaceAll('_',' ')}</span>
      </div>
      <pre className="mt-3 max-h-28 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-slate-50 p-3 font-mono text-xs text-slate-700">{(e.sql_text||'Query text unavailable').slice(0,500)}</pre>
      <p className="mt-2 text-xs text-slate-500">{e.row_count??0} rows · {(e.duration_ms/1000).toFixed(2)}s · {e.sources.map(s=>s.name).join(', ')||'No source listed'}</p>
      {e.freshness==='Data changed'&&<p className="mt-1 text-xs font-medium text-amber-800">Source data changed since this execution.</p>}
      <button type="button" disabled={!e.sql_text} className="mt-3 min-h-11 rounded-lg border border-indigo-200 px-3 text-sm font-medium text-indigo-700 hover:bg-indigo-50 disabled:opacity-50" onClick={()=>openHistory(e)}>Open SQL — no execution</button>
    </article>)}
    {!(kind==='saved'?saved.length:entries.length)&&<p className="rounded-xl bg-slate-50 p-4 text-sm text-slate-500">{search?'No matching queries.':kind==='saved'?'No saved queries yet. Save your first query from Builder or SQL Editor.':'No query executions yet. Run a query to see its history.'}</p>}
    {kind==='history'&&<p className="text-xs text-slate-500">Opening history restores SQL and execution details, without running the query. Result rows are not stored. Only the recent bounded history is retained.</p>}
  </div>
}
