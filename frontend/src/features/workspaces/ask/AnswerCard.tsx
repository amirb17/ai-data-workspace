import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { WorkspaceAnswer } from '../../../services/api/workspaceQuestions'

const labels = { ANSWERED: 'Answered', CLARIFICATION_REQUIRED: 'Needs clarification', REFRESH_REQUIRED: 'Refresh needed', UNSUPPORTED: 'Not supported yet', NO_DATA: 'No data', FAILED: 'Could not answer' }
function valueLabel(value: string | number | boolean | null) {
  return value === null ? 'Unavailable' : typeof value === 'number' ? value.toLocaleString(undefined, { maximumFractionDigits: 2 }) : String(value)
}
export function AnswerCard({ answer, busy, refresh, choose, followUp }: {
  answer: WorkspaceAnswer; busy: boolean; refresh: () => void; choose: (id: number) => void; followUp: (question: string) => void
}) {
  const current = answer.freshness === 'FRESH' && answer.status === 'ANSWERED'
  const chart = current && answer.result_kind === 'grouped' && answer.dimensions.length === 1 && answer.groups.length > 1
    && answer.groups.every(group => typeof group.value === 'number' && Number.isFinite(group.value))
  const chartData = chart ? answer.groups.map(group => ({ name: group.dimensions.map(d => d === null ? '(null)' : String(d)).join(' / '), value: group.value })) : []
  const timeSeries = /time display groups/i.test(answer.caveats.join(' '))
  return <article className="min-w-0 space-y-3 rounded-xl border bg-white p-4 sm:p-5">
    <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="break-words font-semibold">{answer.question}</h3><span className="text-sm text-slate-600">{answer.freshness === 'STALE' ? 'Historical · refresh needed' : labels[answer.status]}</span></div>
    <p className="break-words text-lg font-medium" aria-live="polite">{answer.freshness === 'STALE' ? 'This historical answer needs a refresh before it can show current values.' : answer.primary_answer}</p>
    {answer.reused && <p className="text-xs text-slate-500">Reused the current saved calculation.</p>}
    {answer.choices.length > 0 && <div className="flex flex-wrap gap-2" aria-label="Clarification choices">{answer.choices.map(choice => <button key={choice.choice_id} disabled={busy} onClick={() => choose(choice.choice_id)} className="min-h-11 max-w-full break-words rounded-lg border px-3 text-left text-sm disabled:opacity-50">{choice.label}</button>)}</div>}
    {chart && <div className="h-64 w-full min-w-0" role="img" aria-label={`${timeSeries ? 'Line' : 'Bar'} chart; exact values in the table below`}><ResponsiveContainer width="100%" height="100%">{timeSeries ? <LineChart data={chartData}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="name" /><YAxis /><Tooltip /><Line dataKey="value" stroke="#4f46e5" dot={false} /></LineChart> : <BarChart data={chartData}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="name" /><YAxis /><Tooltip /><Bar dataKey="value" fill="#4f46e5" /></BarChart>}</ResponsiveContainer></div>}
    {current && answer.groups.length > 0 && <div className="max-h-80 overflow-auto"><table className="w-full table-fixed text-left text-sm"><caption className="sr-only">Answer values</caption><thead><tr>{answer.dimensions.map((dimension, i) => <th className="break-words p-2" key={i} scope="col">{dimension}</th>)}<th className="p-2" scope="col">Value</th></tr></thead><tbody>{answer.groups.map((group, i) => <tr key={i}>{group.dimensions.map((dimension, j) => <td className="break-words p-2" key={j}>{dimension === null ? '(null)' : valueLabel(dimension)}</td>)}<td className="break-words p-2">{valueLabel(group.value)}</td></tr>)}</tbody></table></div>}
    {current && answer.comparison && <dl className="grid grid-cols-1 gap-2 text-sm sm:grid-cols-2">{answer.comparison.periods.map((period, i) => <div key={i}><dt>{period[0]} to {period[1]} (end excluded)</dt><dd>{valueLabel(answer.comparison?.values[i] ?? null)}</dd></div>)}<div><dt>Percentage change</dt><dd>{answer.comparison.percentage_change === null ? 'Unavailable (no baseline or zero baseline)' : `${valueLabel(answer.comparison.percentage_change)}%`}</dd></div></dl>}
    {answer.caveats.map((caveat, i) => <p key={i} className="break-words text-sm text-amber-800">{caveat}</p>)}
    <details className="text-sm"><summary className="min-h-11 cursor-pointer py-2">How this was answered</summary><div className="space-y-2 break-words"><p>{answer.interpretation}</p><p>{answer.explanation}</p><p>Calculated: {new Date(answer.computed_at).toLocaleString()}</p>{answer.provenance.map(source => <p key={source.dataset_id}>{source.dataset_name} · state {source.state_id} · Gold {source.gold_run_id}</p>)}{answer.relationship_versions.map(relationship => <p key={relationship.relationship_id}>Confirmed relationship {relationship.relationship_id}, version {relationship.version}</p>)}</div></details>
    <div className="flex flex-wrap gap-2">{(answer.freshness === 'STALE' || ['REFRESH_REQUIRED', 'FAILED'].includes(answer.status)) && <button disabled={busy} onClick={refresh} className="min-h-11 rounded-lg border px-3 text-sm disabled:opacity-50">{answer.status === 'FAILED' ? 'Retry answer' : 'Refresh answer'}</button>}{current && answer.follow_ups.map(question => <button key={question} disabled={busy} onClick={() => followUp(question)} className="min-h-11 max-w-full break-words rounded-lg border px-3 text-sm">{question}</button>)}</div>
  </article>
}
