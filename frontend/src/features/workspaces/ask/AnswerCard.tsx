import type { WorkspaceAnswer } from '../../../services/api/workspaceQuestions'
import type { WorkspaceAnalytics } from '../../../services/api/workspaceAnalytics'
import { ResultVisualization } from '../analytics/ResultVisualization'
import { formatValue } from '../analytics/presentation'

const labels = { ANSWERED: 'Answered', CLARIFICATION_REQUIRED: 'Needs clarification', REFRESH_REQUIRED: 'Refresh needed', UNSUPPORTED: 'Not supported yet', NO_DATA: 'No data', FAILED: 'Could not answer' }
function valueLabel(value: string | number | boolean | null) {
  return typeof value === 'boolean' ? String(value) : formatValue(value)
}
export function AnswerCard({ answer, busy, refresh, choose, followUp, relationships = [] }: {
  answer: WorkspaceAnswer; busy: boolean; refresh: () => void; choose: (id: number) => void; followUp: (question: string) => void; relationships?: WorkspaceAnalytics['relationships']
}) {
  const current = answer.freshness === 'FRESH' && answer.status === 'ANSWERED'
  const timeGrain = answer.intent?.time_grain || answer.caveats.join(' ').match(/time display groups by (day|week|month|quarter|year)/i)?.[1].toUpperCase()
  return <article className="min-w-0 space-y-3 rounded-xl border bg-white p-4 sm:p-5">
    <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="break-words font-semibold">{answer.question}</h3><span className="text-sm text-slate-600">{answer.freshness === 'STALE' ? 'Historical · refresh needed' : labels[answer.status]}</span></div>
    <p className={`break-words font-medium ${current && answer.result_kind === 'scalar' ? 'text-3xl tracking-tight' : 'text-lg'}`} aria-live="polite">{answer.freshness === 'STALE' ? 'This historical answer needs a refresh before it can show current values.' : answer.primary_answer}</p>
    {answer.reused && <p className="text-xs text-slate-500">Reused the current saved calculation.</p>}
    {answer.choices.length > 0 && <div className="flex flex-wrap gap-2" aria-label="Clarification choices">{answer.choices.map(choice => <button key={choice.choice_id} disabled={busy} onClick={() => choose(choice.choice_id)} className="min-h-11 max-w-full break-words rounded-lg border px-3 text-left text-sm disabled:opacity-50">{choice.label}</button>)}</div>}
    {current && answer.result_kind === 'grouped' && <ResultVisualization title={answer.question} groups={answer.groups} dimensions={answer.dimensions} timeGrain={timeGrain} ranking={answer.intent?.question_type === 'RANKING'} />}
    {current && answer.comparison && <dl className="grid grid-cols-1 gap-2 text-sm sm:grid-cols-2">{answer.comparison.periods.map((period, i) => <div key={i}><dt>{period[0]} to {period[1]} (end excluded)</dt><dd>{valueLabel(answer.comparison?.values[i] ?? null)}</dd></div>)}<div><dt>Percentage change</dt><dd>{answer.comparison.percentage_change === null ? 'Unavailable (no baseline or zero baseline)' : `${valueLabel(answer.comparison.percentage_change)}%`}</dd></div></dl>}
    {answer.caveats.map((caveat, i) => <p key={i} className="break-words text-sm text-amber-800">{caveat}</p>)}
    <details className="text-sm"><summary className="min-h-11 cursor-pointer py-2 font-medium text-indigo-700">How this was calculated</summary><div className="space-y-2 break-words"><p>{answer.interpretation}</p><p>{answer.explanation}</p><p>Data: {current ? 'Current' : answer.freshness === 'STALE' ? 'Refresh required' : 'Unavailable'}</p><p>Calculated: {new Date(answer.computed_at).toLocaleString()}</p><p>Sources: {answer.provenance.map(source => source.dataset_name).join(', ') || 'No calculation sources'}</p>{answer.relationship_versions.map(r => {const relation = relationships?.find(item => item.relationship_id === r.relationship_id && item.version === r.version); return <p key={r.relationship_id}>Uses confirmed relationship: {relation ? `${relation.parent_name} → ${relation.child_name}` : 'Direct lookup'}</p>})}<details><summary className="min-h-11 cursor-pointer py-2 text-xs text-slate-500">Source versions</summary>{answer.provenance.map(source => <p key={source.dataset_id} className="text-xs text-slate-500">{source.dataset_name} · state {source.state_id} · analytics version {source.gold_run_id}</p>)}{answer.relationship_versions.map(r => <p key={r.relationship_id} className="text-xs text-slate-500">Confirmed relationship {r.relationship_id}, version {r.version}</p>)}</details></div></details>
    <div className="flex flex-wrap gap-2">{(answer.freshness === 'STALE' || ['REFRESH_REQUIRED', 'FAILED'].includes(answer.status)) && <button disabled={busy} onClick={refresh} className="min-h-11 rounded-lg border px-3 text-sm disabled:opacity-50">{answer.status === 'FAILED' ? 'Retry answer' : 'Refresh answer'}</button>}{current && answer.follow_ups.map(question => <button key={question} disabled={busy} onClick={() => followUp(question)} className="min-h-11 max-w-full break-words rounded-lg border px-3 text-sm">{question}</button>)}</div>
  </article>
}
