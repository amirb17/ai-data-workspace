import { Link } from 'react-router-dom'
import type { MetricResult, WorkspaceAnalytics } from '../../../services/api/workspaceAnalytics'
import { formatValue, metricStatus } from './presentation'
import { ResultVisualization } from './ResultVisualization'

export function WorkspaceMetricCard({metric, workspaceId, busy, retry, relationships = []}: {metric: MetricResult; workspaceId: string; busy: boolean; retry: (id: number) => void; relationships?: WorkspaceAnalytics['relationships']}) {
  const current = metric.status === 'FRESH' && metric.decision !== 'REVIEW_REQUIRED' ? metric.output : null
  const d = metric.definition
  const name = (id: number) => metric.source_names?.find(s => s.dataset_id === id)?.dataset_name || 'Source dataset'
  return <article className="min-w-0 space-y-3 rounded-xl border border-slate-200 bg-white p-5 [overflow-wrap:anywhere]">
    <div className="flex flex-wrap items-start justify-between gap-2"><h3 className="font-semibold text-slate-900">{d.name}</h3><span className={`rounded-full px-2 py-1 text-xs font-medium ${current ? 'bg-emerald-50 text-emerald-800' : 'bg-slate-100 text-slate-600'}`}>{metricStatus[metric.status]}</span></div>
    {current?.kind === 'scalar' && <p className="text-3xl font-semibold tracking-tight">{current.input_rows === 0 && current.value === null ? 'No matching data' : formatValue(current.value, d.unit)}</p>}
    {current?.kind === 'scalar' && current.value === null && <p className="text-sm text-slate-500">{current.input_rows === 0 ? 'No matching data for this metric.' : 'This calculation has no available numeric value.'}</p>}
    {current?.kind === 'grouped' && <ResultVisualization title={d.name} groups={current.groups} dimensions={[...(d.dimensions || []).map(r => r.column), ...(d.time_field && d.time_bucket ? [d.time_field.column] : [])]} unit={d.unit} timeGrain={current.display_hint === 'line' ? d.time_bucket || undefined : undefined} totalGroups={current.total_groups} truncated={current.truncated} />}
    {metric.computed_at && <p className="text-xs text-slate-500">Updated {new Date(metric.computed_at).toLocaleString()}</p>}
    {metric.status === 'STALE' && <p className="text-sm text-slate-600">Source analytics need refresh. Previous values are withheld until the required data is current.</p>}
    {metric.status === 'FAILED' && <p className="text-sm text-slate-600">Your data is unchanged. Retry this calculation.</p>}
    {metric.status === 'BLOCKED' && <p className="text-sm text-slate-600">{metric.reason === 'UNAVAILABLE' ? 'A required dataset is unavailable in this workspace.' : metric.reason === 'RELATIONSHIP_REQUIRED' ? 'A relationship needs current confirmation in Data Model.' : metric.review_reasons.join(' ') || 'This definition needs clarification before it can be calculated.'}</p>}
    {metric.decision === 'REVIEW_RECOMMENDED' && <p className="text-xs text-indigo-700">Recommended metric · Review is optional</p>}
    <details className="text-sm"><summary className="min-h-11 cursor-pointer py-2 font-medium text-indigo-700">View details</summary><div className="space-y-3 border-t border-slate-100 pt-3">
      <p>{d.description}</p><p>Data: {metricStatus[metric.status]}</p><p>Definition: {metric.decision === 'AUTO_ACCEPT' ? 'Ready automatically' : metric.decision === 'REVIEW_RECOMMENDED' ? 'Review is optional' : 'Needs definition'}</p>
      <p>Based on {metric.required_datasets.map((id, i) => <span key={id}>{i > 0 ? ', ' : ''}<Link className="text-indigo-700 underline" to={`/app/workspaces/${workspaceId}/datasets/${id}`}>{name(id)}</Link></span>)}</p>
      <p>Dimensions: {(d.dimensions || []).map(r => `${name(r.dataset_id)} · ${r.column}`).join(', ') || 'None'}</p>
      <div><p className="font-medium">Calculation</p>{d.formula?.nodes.map(n => <p key={n.id} className="break-words font-mono text-xs">{n.id}: {n.op}({n.column ? `${name(n.column.dataset_id)}.${n.column.column}` : n.args.join(', ')})</p>)}<p className="text-xs text-slate-500">{d.time_bucket ? `Time grouping: ${d.time_bucket.toLowerCase()}. ` : ''}Unit: {d.unit === 'PERCENTAGE' ? 'Percentage' : d.unit === 'RATIO' ? 'Ratio' : 'Number'}</p></div>
      {(metric.relationship_versions || []).map(r => {const relation = relationships?.find(item => item.relationship_id === r.relationship_id && item.version === r.version); return <p key={r.relationship_id}>Uses confirmed relationship: {relation ? `${relation.parent_name} → ${relation.child_name}` : 'Direct lookup'}<span className="block text-xs text-slate-500">Relationship {r.relationship_id} · version {r.version}</span></p>})}
      {(metric.source_versions || []).map(s => <p key={s.dataset_id} className="text-xs text-slate-500">{name(s.dataset_id)} · state {s.state_id} · analytics version {s.gold_run_id}</p>)}
    </div></details>
    <div className="flex flex-wrap gap-3">
      {metric.can_retry && <button className="min-h-11 rounded-lg border px-3 text-sm disabled:opacity-50" disabled={busy} onClick={() => retry(metric.candidate_id)}>Retry</button>}
      {(metric.status === 'BLOCKED' || metric.decision === 'REVIEW_RECOMMENDED') && <Link className="inline-flex min-h-11 items-center text-sm text-indigo-700 underline" to={`/app/workspaces/${workspaceId}/metrics`}>{metric.status === 'BLOCKED' ? 'Review Metric' : 'Review definition'}</Link>}
      {metric.status === 'STALE' && <Link className="inline-flex min-h-11 items-center text-sm text-indigo-700 underline" to={`/app/workspaces/${workspaceId}/datasets`}>View sources</Link>}
      {metric.reason === 'RELATIONSHIP_REQUIRED' && <Link className="inline-flex min-h-11 items-center text-sm text-indigo-700 underline" to={`/app/workspaces/${workspaceId}/data-model`}>Review Data Model</Link>}
    </div>
  </article>
}
