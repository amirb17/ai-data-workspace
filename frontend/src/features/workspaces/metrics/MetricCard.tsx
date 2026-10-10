import type { MetricCandidate } from '../../../services/api/metrics'
export function MetricCard({candidate:c,busy,review}: {candidate: MetricCandidate; busy: boolean; review: (c: MetricCandidate,a: 'approve' | 'reject')=>void}) {
  const d=c.definition
  return <article className="min-w-0 space-y-3 rounded-xl border bg-white p-5 [overflow-wrap:anywhere]">
    <h3 className="text-lg font-semibold">{d.name}</h3><p>{d.description}</p>
    <p className="text-sm font-medium">{c.decision.replaceAll('_',' ')} · {c.review_status.replaceAll('_',' ')}</p>
    <p className="text-sm">Structural validation: {c.validation_status.replaceAll('_',' ')} · AI semantic confidence: {d.confidence>=.8?'High':d.confidence>=.5?'Medium':'Low'} (uncalibrated)</p>
    <p className="text-sm">Based on dataset {d.base_dataset_id} · {d.base_entity} · Grain: {d.grain.toLowerCase()}{d.grain_keys.length ? ` (${d.grain_keys.join(' + ')})` : ''} · {d.unit}</p>
    <dl className="space-y-1 text-sm">{d.formula.nodes.map(n=><div key={n.id}><dt className="inline font-medium">{n.id}: </dt><dd className="inline">{n.op}({n.column ? `dataset ${n.column.dataset_id}.${n.column.column}` : n.args.join(', ')}){n.filters.map(f=>` · dataset ${f.column.dataset_id}.${f.column.column} ${f.operator} unresolved category [${f.category_ref}]`).join('')}</dd></div>)}</dl>
    <p className="text-sm">Result node: {d.formula.root} · Zero denominator: unavailable · Scale: {d.formula.scale}</p>
    <p className="text-sm">Dimensions: {d.dimensions.map(r=>`dataset ${r.dataset_id}.${r.column}`).join(', ') || 'None'} · Relationships: {d.relationship_ids.join(', ') || 'None'}</p>
    <p className="text-sm">Time grouping: {d.time_field ? `dataset ${d.time_field.dataset_id}.${d.time_field.column} (${d.time_bucket})` : 'None'} · Default filters: {d.default_filters.map(f=>`dataset ${f.column.dataset_id}.${f.column.column} ${f.operator} unresolved category [${f.category_ref}]`).join(', ') || 'None'}</p>
    <p className="text-sm">Required columns: {c.required_columns.map(r=>`dataset ${r.dataset_id}.${r.column}`).join(', ') || 'None (base records)'} · Discovery run: {c.run_id}</p>
    <details className="text-sm"><summary className="min-h-11 cursor-pointer py-2">Pinned source versions</summary><pre className="whitespace-pre-wrap break-words">{JSON.stringify(c.dependencies,null,2)}</pre></details>
    <p className="text-sm">Definition: {c.definition_freshness.replaceAll('_',' ')} · Dependencies: {c.dependency_freshness} · Analytics data: {c.data_freshness.replaceAll('_',' ')}</p>
    <p className="text-sm text-slate-600">{d.rationale}</p>
    <ul className="space-y-1 text-sm text-amber-800">{[...c.errors,...c.review_reasons].map((r,i)=><li key={`${i}:${r}`}>{r}</li>)}</ul>
    {c.decision==='AUTO_ACCEPT' && c.review_status==='AUTO_ACCEPTED' && <p className="text-sm">Accepted by deterministic policy. No approval dialog is needed.</p>}
    {c.decision==='REVIEW_RECOMMENDED' && <p className="text-sm">Review is optional for this recommendation. Approval does not execute it.</p>}
    {c.decision==='REVIEW_REQUIRED' && <p className="text-sm">Resolve the stated assumptions before approval; V1 cannot edit business definitions here.</p>}
    <p className="text-sm text-slate-500">Definition only. Preview and execution are unavailable in this phase; no value has been calculated.</p>
    <div className="flex flex-wrap gap-3">{c.can_approve && <button className="min-h-11 rounded-lg bg-indigo-600 px-4 text-white disabled:opacity-50" disabled={busy} onClick={()=>review(c,'approve')}>Approve definition (optional)</button>}{c.can_reject && <button className="min-h-11 rounded-lg border px-4 disabled:opacity-50" disabled={busy} onClick={()=>review(c,'reject')}>Reject</button>}</div>
  </article>
}
