import { Link } from 'react-router-dom'
import type { WorkspaceUnderstanding } from '../../../services/api/workspaceUnderstanding'
import type { Classification } from '../../../services/api/understanding'
const confidence = (value: number) => value >= .8 ? 'High confidence' : value >= .5 ? 'Moderate confidence' : 'Low confidence'
function ClassificationCard({title,value}: {title: string; value: Classification}) {
  return <section className="min-w-0 rounded-xl border border-slate-200 p-4"><h3 className="font-medium">{title}</h3><p className="mt-2 text-lg">{value.primary.label || 'Uncertain — review required'}</p><p className="text-sm text-slate-500">{confidence(value.primary.confidence)}</p><p className="mt-2 text-sm">{value.primary.rationale}</p>{value.alternatives.length > 0 && <div className="mt-3 space-y-2 text-sm"><h4 className="font-medium">Alternative interpretations</h4>{value.alternatives.map((c,i)=><p key={i}>{c.label || 'Unknown'} · {confidence(c.confidence)} — {c.rationale}</p>)}</div>}</section>
}
export function WorkspaceEvidence({data,busy,error,generate}: {data: WorkspaceUnderstanding; busy: boolean; error: string; generate: () => void}) {
  const suggestion = data.status === 'READY' ? data.suggestion : null
  const reasoning = suggestion?.reasoning
  const names = new Map(data.source_pins.map(p=>[p.dataset_id,p.dataset_name]))
  const contributors = (ids: number[]) => ids.map(id=>names.get(id) || `Dataset ${id}`).join(', ')
  return <div className="min-w-0 space-y-5 [overflow-wrap:anywhere]">
    <header><h2 className="text-xl font-semibold">Workspace Understanding</h2><p className="mt-2 text-sm text-slate-600">AI suggested · Review required. Discover likely entities and processes from current dataset evidence. Suggestions do not approve semantics.</p></header>
    <section className="rounded-xl border border-indigo-200 bg-indigo-50 p-4"><p className="font-medium">{data.coverage.datasets_analyzed} of {data.coverage.datasets_total} datasets {suggestion ? 'analyzed' : 'eligible'}{data.coverage.partial ? ' · Partial coverage' : ''}</p><p className="mt-1 text-sm">{data.readiness_message}</p><p className="mt-2 text-sm">Analysis sends workspace and dataset names, column names, configured keys/time and redacted evidence to the configured AI provider. No raw records are sent.</p>
      {data.status === 'STALE' && <p className="mt-2 text-sm">Workspace membership, dataset evidence or model configuration changed. Earlier suggestions are hidden. Refresh understanding using current eligible datasets.</p>}
      {data.status === 'FAILED' && <p role="alert" className="mt-2 text-sm">Analysis failed ({data.failure_code || 'GENERATION_FAILED'}). Dataset data and profiles are unchanged. You can retry.</p>}
      {data.status === 'NOT_GENERATED' && <p className="mt-2 text-sm">Workspace understanding has not been generated.</p>}
      <button disabled={busy || data.status === 'GENERATING' || !data.can_generate} onClick={generate} className="mt-3 min-h-11 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">{busy || data.status === 'GENERATING' ? 'Analyzing…' : data.status === 'FAILED' ? 'Retry Understanding' : data.status === 'STALE' ? 'Refresh Understanding' : data.status === 'READY' ? 'Understanding is current' : 'Generate Workspace Understanding'}</button>
      {error && <p role="alert" className="mt-2 text-sm text-red-700">{error}</p>}
    </section>
    {data.coverage.excluded.length > 0 && <section className="rounded-xl border border-slate-200 p-4"><h3 className="font-medium">Excluded datasets</h3><ul className="mt-3 space-y-3">{data.coverage.excluded.map(d=><li key={d.dataset_id} className="flex flex-wrap items-center justify-between gap-2 text-sm"><span>{d.dataset_name} · {d.reason.replaceAll('_',' ').toLowerCase()}</span><Link className="inline-flex min-h-11 items-center text-indigo-700" to={`/app/workspaces/${data.workspace_id}/datasets/${d.dataset_id}/understanding`}>Review Dataset</Link></li>)}</ul></section>}
    {reasoning && <>
      <p className="text-sm text-slate-500">Suggestion version {suggestion!.suggestion_version} · {confidence(reasoning.overall_confidence)} · {new Date(suggestion!.completed_at).toLocaleString()}</p>
      {reasoning.overall_confidence < .5 && <p className="rounded-lg bg-amber-50 p-4 text-sm">The evidence is ambiguous or may span multiple business domains. Review alternatives and unresolved questions before relying on these suggestions.</p>}
      <div className="grid min-w-0 gap-4 md:grid-cols-2"><ClassificationCard title="Likely domain" value={reasoning.domain}/><ClassificationCard title="Likely subdomain" value={reasoning.subdomain}/></div>
      <section className="space-y-3"><h3 className="font-medium">Likely business processes</h3>{reasoning.business_processes.length === 0 && <p className="text-sm">No supported process identified.</p>}{reasoning.business_processes.map((p,i)=><article key={i} className="rounded-xl border border-slate-200 p-4"><h4 className="font-medium">{p.name}</h4><p className="text-sm text-slate-500">{confidence(p.confidence)} · {contributors(p.contributing_datasets)}</p><p className="mt-2 text-sm">{p.rationale}</p></article>)}</section>
      <section className="space-y-3"><h3 className="font-medium">Likely entity inventory</h3>{reasoning.entities.length === 0 && <p className="text-sm">No supported entity identified.</p>}{reasoning.entities.map((e,i)=><article key={i} className="rounded-xl border border-slate-200 p-4"><h4 className="font-medium">{e.canonical_name}</h4><p className="text-sm text-slate-500">{confidence(e.confidence)} · {contributors(e.contributing_datasets)}</p><p className="mt-2 text-sm">{e.rationale}</p></article>)}</section>
      <section className="space-y-3"><h3 className="font-medium">Suggested dataset roles</h3>{reasoning.dataset_roles.map(r=><article key={r.dataset_id} className="rounded-xl border border-slate-200 p-4"><h4 className="font-medium">{names.get(r.dataset_id)}</h4><p className="text-sm text-slate-500">{r.role.replaceAll('_',' ')} · {confidence(r.confidence)}</p><p className="mt-2 text-sm">{r.rationale}</p></article>)}</section>
      <section className="rounded-xl border border-amber-200 bg-amber-50 p-4"><h3 className="font-medium">Uncertainties and review questions</h3><ul className="mt-2 list-disc space-y-2 pl-5 text-sm">{[...reasoning.warnings,...reasoning.unresolved_questions].map((q,i)=><li key={i}>{q}</li>)}</ul></section>
    </>}
  </div>
}
