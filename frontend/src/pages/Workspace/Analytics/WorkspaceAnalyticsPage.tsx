import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { getWorkspaceAnalytics, refreshWorkspaceAnalytics, retryWorkspaceMetric } from '../../../services/api/workspaceAnalytics'
import type { WorkspaceAnalytics } from '../../../services/api/workspaceAnalytics'
import { WorkspaceMetricCard } from '../../../features/workspaces/analytics/WorkspaceMetricCard'

export function WorkspaceAnalyticsContent({data, busy, refresh, retry}: {data: WorkspaceAnalytics; busy: boolean; refresh: () => void; retry: (id: number) => void}) {
  const workspaceId = String(data.workspace_id)
  return <section className="min-w-0 space-y-5">
    <header className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="text-xl font-semibold">Workspace Analytics</h2><p className="text-sm text-slate-600">Validated metrics from current trusted dataset analytics.</p></div><button disabled={busy || !data.can_refresh} onClick={refresh} className="min-h-11 rounded-lg bg-indigo-600 px-4 text-sm font-medium text-white disabled:opacity-50">{busy || data.status === 'COMPUTING' ? 'Refreshing…' : 'Refresh Analytics'}</button></header>
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3"><div className="rounded-xl border bg-white p-4">Current metrics<p className="text-2xl font-semibold">{data.metrics_fresh}</p></div><div className="rounded-xl border bg-white p-4">Needs attention<p className="text-2xl font-semibold">{data.metrics_stale + data.metrics_failed + data.metrics_blocked}</p></div><div className="rounded-xl border bg-white p-4">Last refreshed<p className="break-words text-sm">{data.last_refreshed ? new Date(data.last_refreshed).toLocaleString() : 'Not refreshed yet'}</p></div></div>
    {data.status === 'NEEDS_ATTENTION' && <p className="text-sm text-amber-800">Some metrics need attention. Current successful results remain available.</p>}
    {!data.metrics.length && <p className="rounded-xl border bg-white p-5">No validated metrics yet. <Link className="text-indigo-700 underline" to={`/app/workspaces/${workspaceId}/metrics`}>Discover Metrics</Link></p>}
    <div className="grid min-w-0 grid-cols-1 gap-4 lg:grid-cols-2">{data.metrics.map(metric => <WorkspaceMetricCard key={metric.candidate_id} metric={metric} workspaceId={workspaceId} busy={busy} retry={retry} />)}</div>
  </section>
}
function ScopedAnalytics({workspaceId}: {workspaceId: string}) {
  const [data, setData] = useState<WorkspaceAnalytics | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const action = useRef(false)
  const controller = useRef<AbortController | null>(null)
  const revision = useRef(0)
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
    void load()
    const timer = setInterval(() => void load(), 5000)
    return () => {scope.abort(); clearInterval(timer)}
  }, [workspaceId])
  async function run(id?: number) {
    if (action.current) return
    revision.current += 1
    action.current = true; setBusy(true); setError('')
    const signal = controller.current?.signal
    try {
      const result = id === undefined ? await refreshWorkspaceAnalytics(workspaceId, signal) : await retryWorkspaceMetric(workspaceId, id, signal)
      if (!signal?.aborted) setData(result)
    } catch { if (!signal?.aborted) {setData(null); setError('Could not refresh analytics. Reload to check persisted results.')} }
    finally {action.current = false; if (!signal?.aborted) setBusy(false)}
  }
  return <>{error && <p role="alert" className="rounded-lg border border-red-200 p-4 text-red-700">{error}</p>}{data ? <WorkspaceAnalyticsContent data={data} busy={busy} refresh={() => void run()} retry={id => void run(id)} /> : !error && <p>Loading workspace analytics…</p>}</>
}
export function WorkspaceAnalyticsPage() {
  const {workspaceId = ''} = useParams()
  return <ScopedAnalytics key={workspaceId} workspaceId={workspaceId} />
}
