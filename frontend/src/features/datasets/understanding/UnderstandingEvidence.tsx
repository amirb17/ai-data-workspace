import { Link } from 'react-router-dom'
import type { Classification, Understanding } from '../../../services/api/understanding'
const confidenceBand = (value: number) => value >= .8 ? 'High' : value >= .5 ? 'Medium' : 'Low'
function ClassificationCard({title, value}: {title: string; value: Classification}) {
  const primary = value.primary
  return <section className="min-w-0 space-y-2 rounded-xl border bg-white p-4 [overflow-wrap:anywhere]">
    <h3 className="font-semibold">{title}</h3>
    <p>{primary.confidence < .5 || !primary.label ? "We couldn't confidently determine this business meaning." : primary.label}</p>
    <p className="text-sm text-slate-600">{confidenceBand(primary.confidence)} confidence · AI assessment</p>
    <p className="text-sm">{primary.rationale}</p>
    {!!value.alternatives.length && <><h4 className="text-sm font-semibold">Possible alternatives</h4><ul className="space-y-1 text-sm">{value.alternatives.map((item,index)=><li key={index}>{item.label || 'Unknown'} · {confidenceBand(item.confidence)} confidence — {item.rationale}</li>)}</ul></>}
  </section>
}
export function UnderstandingEvidence({data, busy=false, error='', generate}: {data: Understanding; busy?: boolean; error?: string; generate: () => void}) {
  const ready = data.status === 'READY' && data.profile_freshness === 'READY'
  const suggestion = ready ? data.suggestion : null
  const reasoning = suggestion?.reasoning
  return <div className="min-w-0 space-y-5 [overflow-wrap:anywhere]">
    <section className="space-y-3 rounded-xl border bg-white p-4 sm:p-5">
      <h2 className="text-lg font-semibold">AI Understanding</h2>
      <p className="text-sm text-slate-600">Suggested by AI · Review required. These interpretations do not approve keys, rules, sensitivity classifications, or business meaning.</p>
      <p className="text-sm text-slate-600">Analysis sends dataset and column names with redacted aggregate evidence to the configured AI provider. No raw record values are sent.</p>
      {data.profile_freshness !== 'READY' ? <><p role="status">Refresh the Data Profile before generating AI understanding.</p><Link className="inline-flex min-h-11 items-center text-indigo-700 underline" to="../profile">Go to Data Profile</Link></> : <>
        <p role="status">{busy || data.status === 'GENERATING' ? 'Analyzing dataset…' : data.status === 'READY' ? 'AI suggestions ready for review' : data.status === 'FAILED' ? 'AI analysis could not complete. Trusted data and the Data Profile are unchanged.' : data.status === 'STALE' ? 'Previous suggestions are stale. Analyze the current profile.' : 'AI Understanding has not been generated.'}</p>
        {!ready && <button type="button" className="min-h-11 rounded-lg bg-indigo-600 px-4 text-white disabled:opacity-50" disabled={busy || !data.can_generate} onClick={generate}>{busy || data.status === 'GENERATING' ? 'Analyzing…' : data.status === 'FAILED' ? 'Retry Analysis' : 'Analyze Dataset'}</button>}
      </>}
      {error && <p role="alert" className="text-sm text-amber-800">{error}</p>}
    </section>
    {suggestion && reasoning && <>
      <p className="text-sm text-slate-600">Suggestion {suggestion.suggestion_version} · Profile {suggestion.source_profile_id} · Dataset state {suggestion.source_state_version} · {suggestion.provider} / {suggestion.resolved_model || suggestion.model} · {new Date(suggestion.completed_at).toLocaleString()}</p>
      <p className="text-sm">Overall confidence: {confidenceBand(reasoning.overall_confidence)}. Confidence is an uncalibrated AI assessment.</p>
      {suggestion.compact_mode && <p className="text-sm text-amber-800">Wide dataset: context was reduced deterministically. Every column name and type was included.</p>}
      <div className="grid gap-3 md:grid-cols-3"><ClassificationCard title="Suggested domain" value={reasoning.domain}/><ClassificationCard title="Suggested subdomain" value={reasoning.subdomain}/><ClassificationCard title="Likely dataset type" value={reasoning.entity}/></div>
      {!!reasoning.warnings.length && <section><h3 className="font-semibold">Review warnings</h3><ul className="list-inside list-disc text-sm">{reasoning.warnings.map((warning,index)=><li key={index}>{warning}</li>)}</ul></section>}
      {!!reasoning.unresolved_questions.length && <section><h3 className="font-semibold">Unresolved questions</h3><ul className="list-inside list-disc text-sm">{reasoning.unresolved_questions.map((question,index)=><li key={index}>{question}</li>)}</ul></section>}
      <div className="grid gap-4 xl:grid-cols-2">{reasoning.columns.map(column=>{
        const evidence = data.evidence.find(item=>item.original_name===column.column_name)
        return <article key={column.column_name} className="min-w-0 space-y-3 rounded-xl border bg-white p-4">
          <h3 className="font-semibold">{column.column_name}</h3>
          {evidence && <div className="space-y-1 rounded-lg bg-slate-50 p-3 text-sm"><h4 className="font-semibold">Deterministic evidence</h4><p>{evidence.canonical_type} · {evidence.distinct_ratio == null ? 'Uniqueness unavailable' : `${Math.round(evidence.distinct_ratio*100)}% unique`} · {evidence.null_ratio == null ? 'Missingness unavailable' : `${Math.round(evidence.null_ratio*100)}% missing`}</p>{evidence.authoritative_business_key && <p>Configured business key · Position {evidence.business_key_position}</p>}{evidence.authoritative_event_time && <p>Configured event time</p>}{!!evidence.sensitivity_hints.length && <p className="text-amber-800">Potential sensitive field · AI interpretation requires review</p>}</div>}
          <div className="space-y-1 text-sm"><h4 className="font-semibold text-indigo-700">AI suggestion · Review required</h4><p>{column.suggested_role.replaceAll('_',' ')} · {confidenceBand(column.confidence)} confidence</p><p>{column.business_meaning}</p><p className="text-slate-600">{column.rationale}</p>{column.warnings.map((warning,index)=><p key={index} className="text-amber-800">{warning}</p>)}</div>
        </article>
      })}</div>
    </>}
  </div>
}
