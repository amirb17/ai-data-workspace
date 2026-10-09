import { useCallback, useEffect, useRef, useState } from 'react'
import { useApiResource } from '../../services/api/useApiResource'
import { getDatasetAnalytics, getAnalyticsReadiness, refreshDatasetAnalytics, type DatasetAnalytics } from '../../services/api/analytics'
import { ApiFeedback } from '../../components/ui/ApiFeedback'

const labels = { FRESH: 'Fresh', STALE: 'Refresh required', REFRESHING: 'Refreshing', FAILED: 'Needs attention', NOT_READY: 'Not ready' }
const time = (value: string | null) => value ? new Date(value).toLocaleString() : '—'
export function AnalyticsStatus({data, busy = false, error = '', refresh}: {data: DatasetAnalytics; busy?: boolean; error?: string; refresh: () => void}) {
  return <section className="min-w-0 space-y-3 rounded-xl border border-slate-200 bg-white p-4 sm:p-5" aria-label="Dataset analytics readiness">
    <h3 className="font-semibold">Analytics</h3><p role="status" className={data.freshness === 'FAILED' ? 'text-amber-800' : 'text-slate-700'}>{labels[data.freshness]}</p>
    <p className="text-sm text-slate-600">{data.analytics_ready ? 'Built from current trusted dataset state.' : data.freshness === 'REFRESHING' ? 'Refreshing analytics from trusted data…' : data.current_state_id == null ? 'Complete a dataset update before refreshing analytics.' : data.freshness === 'FAILED' ? 'Analytics refresh failed. Trusted data is safe; retry without reprocessing deliveries.' : 'The trusted dataset requires an analytics build. Previous results are not presented as current.'}</p>
    <dl className="grid gap-3 text-sm sm:grid-cols-2"><div><dt className="text-slate-500">Trusted dataset records</dt><dd>{data.trusted_rows?.toLocaleString() ?? '—'}</dd></div><div><dt className="text-slate-500">Current analytics records</dt><dd>{data.analytics_ready ? data.analytics_rows?.toLocaleString() ?? '—' : 'Refresh required'}</dd></div><div><dt className="text-slate-500">Dataset state updated</dt><dd>{time(data.state_updated_at)}</dd></div><div><dt className="text-slate-500">Analytics last built</dt><dd>{time(data.analytics_built_at)}</dd></div></dl>
    {data.load_strategy === 'SNAPSHOT' && <p className="text-sm text-slate-600">Current analytics includes active records only. Inactive records remain in dataset history.</p>}
    {data.latest_source_file_name && <p className="break-words text-sm">Latest dataset update: {data.latest_source_file_name}</p>}
    {error && <p role="alert" className="text-sm text-amber-800">{error}</p>}
    {!data.analytics_ready && data.current_state_id != null && <button type="button" className="min-h-11 rounded-lg bg-indigo-600 px-4 text-white disabled:opacity-50" disabled={busy || data.freshness === 'REFRESHING'} onClick={refresh}>{busy || data.freshness === 'REFRESHING' ? 'Refreshing analytics…' : data.freshness === 'FAILED' ? 'Retry Analytics Refresh' : 'Refresh Analytics'}</button>}
  </section>
}
export function AnalyticsReadiness({workspaceId,datasetId,dashboard = false}: {workspaceId: string; datasetId: string; dashboard?: boolean}) {
  const load = useCallback((signal: AbortSignal) => (dashboard ? getDatasetAnalytics : getAnalyticsReadiness)(workspaceId,datasetId,signal), [workspaceId,datasetId,dashboard])
  const resource = useApiResource(`analytics:${workspaceId}:${datasetId}:${dashboard}`,load)
  return resource.data ? <AnalyticsContent key={`${workspaceId}:${datasetId}`} data={resource.data} workspaceId={workspaceId} datasetId={datasetId} reload={resource.retry} dashboard={dashboard} /> : <ApiFeedback loading="Checking analytics freshness…" error={resource.error} retry={resource.retry} />
}
function AnalyticsContent({data,workspaceId,datasetId,reload,dashboard}: {data: DatasetAnalytics; workspaceId: string; datasetId: string; reload: () => void; dashboard: boolean}) {
  const [busy,setBusy] = useState(false), [error,setError] = useState('')
  const accepting = useRef(false), mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  useEffect(() => {
    const controller = new AbortController()
    const interval = setInterval(() => {
      getAnalyticsReadiness(workspaceId,datasetId,controller.signal).then(next => {
        if (!controller.signal.aborted && (next.current_state_id !== data.current_state_id || next.gold_run_id !== data.gold_run_id || next.freshness !== data.freshness)) reload()
      }, () => { if (!controller.signal.aborted) reload() })
    },3000)
    return () => { clearInterval(interval); controller.abort() }
  }, [data.current_state_id,data.gold_run_id,data.freshness,workspaceId,datasetId,reload])
  async function refresh() {
    if (accepting.current) return
    accepting.current = true; setBusy(true); setError('')
    try { await refreshDatasetAnalytics(workspaceId,datasetId) }
    catch { if (mounted.current) setError('Analytics could not be refreshed. Reload the status and retry; trusted data remains safe.') }
    finally { accepting.current = false; if (mounted.current) { setBusy(false); reload() } }
  }
  return <div className="space-y-4"><AnalyticsStatus data={data} busy={busy} error={error} refresh={refresh} />
    {dashboard && data.analytics_ready && <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">{data.kpis?.map(kpi => <div key={kpi.kpi_name} className="min-w-0 rounded-xl border border-slate-200 bg-white p-4"><dt className="break-words text-sm text-slate-500">{kpi.label}</dt><dd className="mt-2 break-words text-xl font-semibold">{kpi.value?.toLocaleString() ?? '—'}</dd></div>)}</dl>}
    {dashboard && !data.analytics_ready && <p className="text-sm text-slate-600">Ask Your Data requires fresh analytics. Refresh this dataset before asking questions.</p>}
  </div>
}
