import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { getWorkspaceAnalytics, refreshWorkspaceAnalytics, retryWorkspaceMetric } from '../../../services/api/workspaceAnalytics'
import type { WorkspaceAnalytics } from '../../../services/api/workspaceAnalytics'
import { WorkspaceMetricCard } from '../../../features/workspaces/analytics/WorkspaceMetricCard'
import { WorkspaceAsk } from '../../../features/workspaces/ask/WorkspaceAsk'
import { readinessLabel, selectMetrics } from '../../../features/workspaces/analytics/presentation'

export function WorkspaceAnalyticsContent({data, busy, refresh, retry, showAsk = false}: {data: WorkspaceAnalytics; busy: boolean; refresh: () => void; retry: (id: number) => void; showAsk?: boolean}) {
  const workspaceId = String(data.workspace_id)
  const selected = selectMetrics(data.metrics)
  const state = busy || data.status === 'COMPUTING' ? 'REFRESHING' : data.readiness || (data.status === 'FRESH' ? 'READY' : data.metrics_fresh ? 'PARTIAL' : data.metrics_stale ? 'STALE' : data.metrics_blocked || data.metrics_failed ? 'NEEDS_ATTENTION' : 'NOT_READY')
  const displayed = new Set([...selected.kpis, ...selected.charts].map(m => m.candidate_id))
  const other = data.metrics.filter(m => m.status === 'FRESH' && !displayed.has(m.candidate_id) && !selected.attention.includes(m))
  const pending = data.metrics.filter(m => ['NOT_COMPUTED', 'COMPUTING'].includes(m.status) && !selected.attention.includes(m))
  const card = (metric: WorkspaceAnalytics['metrics'][number]) => <WorkspaceMetricCard key={metric.candidate_id} metric={metric} workspaceId={workspaceId} busy={busy} retry={retry} relationships={data.relationships} />
  return <div className="min-w-0 space-y-7">
    <section className="space-y-4" aria-labelledby="analytics-heading">
      <header className="flex flex-wrap items-start justify-between gap-3"><div className="min-w-0 space-y-2"><h2 id="analytics-heading" className="text-2xl font-semibold tracking-tight">Analytics</h2><p role="status" className="text-sm font-medium text-slate-700">{readinessLabel[state]}</p><p className="text-xs text-slate-500">{data.last_refreshed ? `Last calculated ${new Date(data.last_refreshed).toLocaleString()}` : 'No calculations yet'}</p></div>{data.can_refresh && <button disabled={busy} onClick={refresh} className="min-h-11 rounded-lg bg-indigo-600 px-4 text-sm font-medium text-white disabled:opacity-50">{state === 'REFRESHING' ? 'Refreshing…' : 'Refresh analytics'}</button>}</header>
      {data.metrics_fresh > 0 && <dl className="flex flex-wrap gap-x-8 gap-y-3">{[
        ...(data.summary ? [['Trusted datasets', data.summary.trusted_datasets]] : []), ['Available metrics', data.metrics_available], ['Current metrics', data.metrics_fresh], ['Needs attention', data.metrics_stale + data.metrics_failed + data.metrics_blocked], ...(data.summary ? [['Confirmed relationships', data.summary.confirmed_relationships]] : []),
      ].map(([label, number]) => <div key={label}><dt className="text-xs text-slate-500">{label}</dt><dd className="mt-1 text-lg font-semibold">{number}</dd></div>)}</dl>}
      {state === 'PARTIAL' && <p className="rounded-lg bg-indigo-50 p-3 text-sm text-indigo-900">{data.metrics_fresh} metrics current · {data.metrics_stale} need refresh · {data.metrics_failed} need retry · {data.metrics_blocked} need definition. Current successful results remain available.</p>}
      {data.metrics_fresh === 0 && <div className="rounded-xl border bg-white p-5"><h3 className="font-semibold">Analytics is being prepared</h3><p className="mt-1 text-sm text-slate-600">{state === 'NO_DATA' ? 'Add and process a dataset to start exploring this workspace.' : data.can_refresh ? 'Validated metrics are ready to calculate from current inputs. Refresh analytics to see your results.' : 'Current dataset analytics, profiles and safe metric definitions are required before workspace values can be shown. Check preparation first, then review any unresolved definitions below.'}</p>{!data.can_refresh && <Link className="mt-2 inline-flex min-h-11 items-center rounded-lg bg-indigo-600 px-4 text-sm text-white" to={`/app/workspaces/${workspaceId}/datasets`}>Prepare data</Link>}</div>}
      {selected.kpis.length > 0 && <div className="space-y-3"><h3 className="font-semibold">Key metrics</h3><div className="grid min-w-0 grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">{selected.kpis.map(card)}</div></div>}
      {selected.charts.length > 0 && <div className="space-y-3"><h3 className="font-semibold">Trends &amp; breakdowns</h3><div className="grid min-w-0 grid-cols-1 gap-4 lg:grid-cols-2">{selected.charts.map(card)}</div></div>}
      {selected.attention.length > 0 && <details className="rounded-xl border border-slate-200 bg-slate-50 p-4"><summary className="min-h-11 cursor-pointer font-semibold">Needs attention · {selected.attention.length}</summary><p className="mb-3 text-sm text-slate-600">Definitions and source freshness can be addressed individually. Current successful results remain available.</p><div className="grid min-w-0 grid-cols-1 gap-4 lg:grid-cols-2">{selected.attention.slice(0, 4).map(card)}</div>{selected.attention.length > 4 && <details className="mt-3"><summary className="min-h-11 cursor-pointer text-sm">View {selected.attention.length - 4} more metrics</summary><div className="grid grid-cols-1 gap-4 lg:grid-cols-2">{selected.attention.slice(4).map(card)}</div></details>}</details>}
      {pending.length > 0 && <details><summary className="min-h-11 cursor-pointer text-sm font-medium">Pending calculations · {pending.length}</summary><div className="grid grid-cols-1 gap-4 sm:grid-cols-2">{pending.map(card)}</div></details>}
      {other.length > 0 && <details><summary className="min-h-11 cursor-pointer text-sm font-medium">All other current metrics · {other.length}</summary><div className="grid grid-cols-1 gap-4 sm:grid-cols-2">{other.map(card)}</div></details>}
    </section>
    {showAsk && <WorkspaceAsk key={`ask-${workspaceId}`} workspaceId={workspaceId} relationships={data.relationships} />}
    <details className="rounded-xl border border-slate-200 bg-white p-4"><summary className="min-h-11 cursor-pointer font-semibold">Recent data updates &amp; sources</summary><div className="space-y-3 text-sm">{data.recent_updates?.length ? data.recent_updates.map(source => <div key={source.dataset_id} className="border-t border-slate-100 pt-3"><Link className="font-medium text-indigo-700 underline" to={`/app/workspaces/${workspaceId}/datasets/${source.dataset_id}`}>{source.dataset_name}</Link><p className="break-words">{source.file_name} · {new Date(source.published_at).toLocaleString()}</p><p className="text-slate-500">{source.inserted_rows !== null ? `${source.inserted_rows.toLocaleString()} records added` : ''}{source.updated_rows !== null ? ` · ${source.updated_rows.toLocaleString()} updated` : ''}</p></div>) : <p className="text-slate-500">No published data updates available.</p>}<div className="flex flex-wrap gap-4"><Link className="inline-flex min-h-11 items-center text-indigo-700 underline" to={`/app/workspaces/${workspaceId}/datasets`}>View Datasets</Link><Link className="inline-flex min-h-11 items-center text-indigo-700 underline" to={`/app/workspaces/${workspaceId}/data-quality`}>Data Quality</Link><Link className="inline-flex min-h-11 items-center text-indigo-700 underline" to={`/app/workspaces/${workspaceId}/data-model`}>Data Model</Link></div></div></details>
  </div>
}
function ScopedAnalytics({workspaceId}: {workspaceId: string}) {
  const [data, setData] = useState<WorkspaceAnalytics | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const action = useRef(false)
  const controller = useRef<AbortController | null>(null)
  const revision = useRef(0)
  const reload = useRef<(() => Promise<void>) | null>(null)
  useEffect(() => {
    const scope = new AbortController()
    controller.current = scope
    let reading = false
    async function load() {
      if (reading || action.current) return
      reading = true
      const startedRevision = revision.current
      try { const result = await getWorkspaceAnalytics(workspaceId, scope.signal); if (!scope.signal.aborted && startedRevision === revision.current) {setData(result); setError('')} }
      catch { if (!scope.signal.aborted && startedRevision === revision.current) {setData(null); setError('Could not load workspace analytics.')} }
      finally { reading = false }
    }
    reload.current = load
    void load()
    const timer = setInterval(() => void load(), 15000)
    return () => {scope.abort(); clearInterval(timer); reload.current = null}
  }, [workspaceId])
  async function run(id?: number) {
    if (action.current) return
    revision.current += 1
    action.current = true; setBusy(true); setError('')
    const signal = controller.current?.signal
    try {
      const result = id === undefined ? await refreshWorkspaceAnalytics(workspaceId, signal) : await retryWorkspaceMetric(workspaceId, id, signal)
      if (!signal?.aborted) setData(result)
    } catch { if (!signal?.aborted) {setData(null); setError('Some analytics could not be refreshed. Your trusted data is unchanged. Retry to check saved results.')} }
    finally {action.current = false; if (!signal?.aborted) setBusy(false)}
  }
  return <div className="space-y-7">{error && <div role="alert" className="space-y-2 rounded-lg border border-red-200 p-4 text-sm text-red-700"><p>{error}</p><button className="min-h-11 rounded-lg border px-3" onClick={() => void reload.current?.()} disabled={busy}>Retry</button></div>}{data ? <WorkspaceAnalyticsContent data={data} busy={busy} refresh={() => void run()} retry={id => void run(id)} /> : !error ? <div role="status" aria-label="Loading workspace analytics" className="space-y-4"><p className="text-sm text-slate-500">Loading workspace analytics…</p><div aria-hidden="true" className="grid grid-cols-1 gap-4 sm:grid-cols-3">{[1,2,3].map(id => <div key={id} className="h-32 animate-pulse rounded-xl bg-slate-100 motion-reduce:animate-none" />)}</div></div> : null}<WorkspaceAsk key={`ask-${workspaceId}`} workspaceId={workspaceId} relationships={data?.relationships} /></div>
}
export function WorkspaceAnalyticsPage() {
  const {workspaceId = ''} = useParams()
  return <ScopedAnalytics key={workspaceId} workspaceId={workspaceId} />
}
