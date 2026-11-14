import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { listDatasets } from '../../../services/api/datasets'
import { getDatasetAnalytics, type DatasetAnalytics } from '../../../services/api/analytics'

type DatasetRow = { id:number; name:string; analytics:DatasetAnalytics | null; error:string }
type State = {rows:DatasetRow[]; loading:boolean; error:string}
const freshnessLabels:Record<string,string> = {FRESH:'Current',STALE:'Needs refresh',REFRESHING:'Refreshing',FAILED:'Needs attention',NOT_READY:'Not prepared'}

export function PublicWorkspaceAnalytics({workspaceId}:{workspaceId:string}) {
  const [revision,setRevision]=useState(0)
  const [state,setState]=useState<State>({rows:[],loading:true,error:''})
  useEffect(()=>{
    const controller=new AbortController()
    setState({rows:[],loading:true,error:''})
    async function load() {
      try {
        const datasets=(await listDatasets(workspaceId,controller.signal)).filter(d=>d.status==='ACTIVE')
        // Owner-scoped reads. Do not imply a failed dataset is zero or current.
        const rows=await Promise.all(datasets.map(async d:Promise<DatasetRow>=>{
          try { const analytics=await getDatasetAnalytics(workspaceId,String(d.id),controller.signal)
            return {id:d.id,name:d.name,analytics,error:''}
          } catch (caught) {return {id:d.id,name:d.name,analytics:null,error:caught instanceof Error?caught.message:'Dataset analytics unavailable'} }
        }))
        if(!controller.signal.aborted)setState({rows,loading:false,error:''})
      } catch (caught) {
        if(!controller.signal.aborted)setState({rows:[],loading:false,error:caught instanceof Error?caught.message:'Workspace datasets unavailable'})
      }
    }
    void load()
    return ()=>controller.abort()
  },[workspaceId,revision])

  const ready=state.rows.filter(r=>r.analytics?.analytics_ready===true&&r.analytics.freshness==='FRESH')
  const incomplete=state.rows.filter(r=>!r.analytics || r.analytics.freshness!=='FRESH')
  const trusted=state.rows.every(r=>r.analytics?.trusted_rows!=null)
    ? state.rows.reduce((total,r)=>total+(r.analytics?.trusted_rows??0),0) : null
  return <section className="space-y-5" aria-labelledby="public-workspace-analytics">
    <header className="flex flex-wrap items-center justify-between gap-3"><div><h2 id="public-workspace-analytics" className="text-2xl font-semibold">Analytics</h2>
      <p className="mt-1 text-sm text-slate-600">Current dataset analytics from verified published data.</p></div>
      <button className="min-h-11 rounded-lg border border-slate-200 bg-white px-4 text-sm" onClick={()=>setRevision(v=>v+1)}>Refresh status</button></header>
    {state.loading && <p role="status">Loading dataset analytics…</p>}
    {state.error && <p role="alert" className="rounded-lg border border-red-200 p-4 text-sm text-red-700">{state.error}</p>}
    {!state.loading&&!state.error&&<>
      <dl className="grid gap-4 sm:grid-cols-3">
        <div className="rounded-xl border bg-white p-4"><dt className="text-sm text-slate-500">Datasets</dt><dd className="mt-1 text-2xl font-semibold">{state.rows.length}</dd></div>
        <div className="rounded-xl border bg-white p-4"><dt className="text-sm text-slate-500">Current analytics</dt><dd className="mt-1 text-2xl font-semibold">{ready.length}</dd></div>
        <div className="rounded-xl border bg-white p-4"><dt className="text-sm text-slate-500">Trusted records</dt><dd className="mt-1 text-2xl font-semibold">{trusted===null?'Unavailable':trusted.toLocaleString()}</dd></div>
      </dl>
      {incomplete.length>0&&<p role="status" className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">{incomplete.length} dataset{incomplete.length===1?'':'s'} need analytics preparation or attention. Outdated values are not presented as current.</p>}
      {!state.rows.length&&<p className="rounded-xl border bg-white p-5 text-sm">No active datasets yet. Add a dataset, process a delivery, and publish trusted records to view Analytics.</p>}
      <div className="grid gap-4 lg:grid-cols-2">{state.rows.map(row=><article key={row.id} className="space-y-3 rounded-xl border border-slate-200 bg-white p-5">
        <div className="flex flex-wrap items-center justify-between gap-3"><h3 className="font-semibold">{row.name}</h3><span className="text-sm text-slate-600">{row.analytics?freshnessLabels[row.analytics.freshness]??row.analytics.freshness:'Unavailable'}</span></div>
        {row.error?<p role="alert" className="text-sm text-red-700">{row.error}</p>:row.analytics&&<>
          <p className="text-sm text-slate-600">Trusted records: {row.analytics.trusted_rows?.toLocaleString()??'Unavailable'}</p>
          {row.analytics.analytics_ready&&row.analytics.freshness==='FRESH'&&
            <dl className="grid gap-3 sm:grid-cols-2">{row.analytics.kpis?.map(kpi=><div key={kpi.kpi_name}><dt className="text-xs text-slate-500">{kpi.label}</dt><dd className="text-lg font-semibold">{kpi.value?.toLocaleString()??'Unavailable'}</dd></div>)}</dl>}
          {row.analytics.analytics_built_at&&<p className="text-xs text-slate-500">Last built: {new Date(row.analytics.analytics_built_at).toLocaleString()}</p>}
        </>}
        <Link className="inline-flex min-h-11 items-center text-sm font-medium text-indigo-700 underline" to={`/app/workspaces/${workspaceId}/datasets/${row.id}/analytics`}>View dataset analytics</Link>
      </article>)}</div>
      <p className="text-sm text-slate-600">Workspace-wide metric definitions and Ask AI are not enabled in this release. Dataset analytics remain available.</p>
    </>}
  </section>
}
