import { Link } from 'react-router-dom'
import type { MetricResult } from '../../../services/api/workspaceAnalytics'

const wording = {NOT_COMPUTED: 'Not calculated yet', STALE: 'Refresh required', COMPUTING: 'Calculating…', FRESH: 'Current', FAILED: 'Could not be calculated', BLOCKED: 'Needs definition'}
function value(v: number | string | null) {
  return v === null ? 'Unavailable' : typeof v === 'number' ? v.toLocaleString(undefined, {maximumFractionDigits: 6}) : v
}
export function WorkspaceMetricCard({metric, workspaceId, busy, retry}: {metric: MetricResult; workspaceId: string; busy: boolean; retry: (id: number) => void}) {
  const current = metric.status === 'FRESH' ? metric.output : null
  return <article className="min-w-0 space-y-3 rounded-xl border border-slate-200 bg-white p-5 [overflow-wrap:anywhere]">
    <div><h3 className="text-lg font-semibold">{metric.definition.name}</h3><p className="text-sm text-slate-600">{metric.definition.description}</p></div>
    <p className={current ? 'font-medium text-emerald-700' : 'font-medium text-slate-600'}>{wording[metric.status]}</p>
    {metric.decision === 'REVIEW_RECOMMENDED' && <p className="text-sm text-indigo-700">Recommended metric · Review is optional</p>}
    {metric.decision === 'AUTO_ACCEPT' && <p className="text-sm text-slate-500">Ready automatically</p>}
    {current?.kind === 'scalar' && <p className="text-3xl font-semibold">{value(current.value)}{current.value !== null && metric.definition.unit === 'PERCENTAGE' ? '%' : ''}</p>}
    {current?.kind === 'grouped' && <div className="space-y-2" role="table" aria-label={metric.definition.name}>
      {current.groups.map((group, index) => <div key={index} role="row" className="flex min-w-0 flex-wrap justify-between gap-2 border-b border-slate-100 pb-2"><span role="cell" className="min-w-0 break-words">{group.dimensions.map(d => d === null ? '(null)' : String(d)).join(' · ')}</span><span role="cell" className="font-medium">{value(group.value)}</span></div>)}
      {current.groups.length === 0 && <p>No groups in current data.</p>}
      {current.truncated && <p className="text-sm text-slate-500">Showing the first {current.groups.length} of {current.total_groups} groups in ascending dimension order.</p>}
    </div>}
    {metric.status === 'STALE' && <p className="text-sm text-slate-600">Required data changed or dataset analytics need refreshing. Refresh the affected dataset analytics, then refresh workspace analytics.</p>}
    {metric.status === 'FAILED' && <p className="text-sm text-slate-600">Your data is unchanged. Retry this calculation.</p>}
    {metric.status === 'BLOCKED' && <p className="text-sm text-slate-600">{metric.reason === 'UNAVAILABLE' ? 'A required dataset is unavailable in this workspace.' : metric.reason === 'RELATIONSHIP_REQUIRED' ? 'A required relationship needs current confirmation in Data Model.' : metric.review_reasons.join(' ') || 'Review the definition and refresh current understanding before calculating.'}</p>}
    <p className="text-sm text-slate-500">Based on {metric.required_datasets.map((id, i) => <span key={id}>{i > 0 ? ', ' : ''}<Link className="text-indigo-700 underline" to={`/app/workspaces/${workspaceId}/datasets/${id}`}>{metric.source_names?.find(s => s.dataset_id === id)?.dataset_name || `Dataset ${id}`}</Link></span>)}</p>
    <div className="flex flex-wrap gap-3">
      {metric.can_retry && <button className="min-h-11 rounded-lg border px-3 text-sm disabled:opacity-50" disabled={busy} onClick={() => retry(metric.candidate_id)}>Retry</button>}
      {(metric.status === 'BLOCKED' || metric.decision === 'REVIEW_RECOMMENDED') && <Link className="inline-flex min-h-11 items-center text-sm text-indigo-700 underline" to={`/app/workspaces/${workspaceId}/metrics`}>{metric.status === 'BLOCKED' ? 'Review Metric' : 'Review definition'}</Link>}
      {metric.reason === 'RELATIONSHIP_REQUIRED' && <Link className="inline-flex min-h-11 items-center text-sm text-indigo-700 underline" to={`/app/workspaces/${workspaceId}/data-model`}>Review Data Model</Link>}
    </div>
  </article>
}
