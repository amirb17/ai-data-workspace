import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { formatValue, plotRows } from './presentation'
import type { ResultGroup } from './presentation'

export function ResultVisualization({title, groups, dimensions, unit, timeGrain, ranking = false, totalGroups, truncated}: {
  title: string; groups: ResultGroup[]; dimensions: string[]; unit?: string; timeGrain?: string; ranking?: boolean; totalGroups?: number; truncated?: boolean
}) {
  const chart = dimensions.length === 1 && groups.length >= (timeGrain ? 2 : 1) && (!timeGrain || groups.length <= 100) && groups.every(g => typeof g.value === 'number' && Number.isFinite(g.value))
  const rows = chart ? plotRows(groups, timeGrain) : []
  const minimum = Math.min(0, ...rows.map(r => r.value || 0))
  const maximum = Math.max(0, ...rows.map(r => r.value || 0))
  return <div className="min-w-0 space-y-3">
    {rows.length > 0 && <><p className="text-xs text-slate-500">{timeGrain ? `${timeGrain.toLowerCase()} buckets in chronological order. Gaps indicate missing buckets.` : `${Math.min(12, groups.length)} categories shown; exact values in the table below.`}</p><div className="h-64 min-w-0 w-full overflow-hidden" role="img" aria-label={`${timeGrain ? 'Line' : 'Bar'} chart for ${title}; exact values in the table below`}><ResponsiveContainer width="100%" height="100%">{timeGrain ? <LineChart data={rows} accessibilityLayer><CartesianGrid strokeDasharray="3 3" vertical={false} /><XAxis dataKey="name" tick={{fontSize: 11}} minTickGap={28} /><YAxis domain={[minimum, maximum > minimum ? maximum : 'auto']} width={48} /><Tooltip /><Line dataKey="value" name="Value" stroke="#4f46e5" strokeWidth={2} dot={{r: 3}} connectNulls={false} isAnimationActive={false} /></LineChart> : <BarChart data={rows} layout={ranking ? 'vertical' : 'horizontal'} accessibilityLayer><CartesianGrid strokeDasharray="3 3" vertical={false} /><XAxis dataKey={ranking ? undefined : 'name'} type={ranking ? 'number' : 'category'} domain={ranking ? [minimum, maximum > minimum ? maximum : 'auto'] : undefined} tick={{fontSize: 11}} /><YAxis dataKey={ranking ? 'name' : undefined} type={ranking ? 'category' : 'number'} domain={ranking ? undefined : [minimum, maximum > minimum ? maximum : 'auto']} width={ranking ? 90 : 48} tick={{fontSize: 11}} /><Tooltip /><Bar dataKey="value" name="Value" fill="#4f46e5" radius={[3, 3, 0, 0]} isAnimationActive={false} /></BarChart>}</ResponsiveContainer></div></>}
    {!groups.length ? <p className="text-sm text-slate-600">No matching data for this metric.</p> : <details open={rows.length === 0} className="min-w-0"><summary className="min-h-11 cursor-pointer py-2 text-sm font-medium">View exact values · {groups.length} {groups.length === 1 ? 'group' : 'groups'}</summary><div className="max-h-80 overflow-y-auto"><table className="w-full table-fixed text-left text-sm"><caption className="sr-only">{title} exact values</caption><thead><tr>{dimensions.map((d, i) => <th key={i} scope="col" className="break-words p-2">{d}</th>)}<th scope="col" className="p-2">Value</th></tr></thead><tbody>{groups.map((g, i) => <tr key={i} className="border-t border-slate-100">{g.dimensions.map((d, j) => <td key={j} className="break-words p-2 [overflow-wrap:anywhere]">{d === null ? '(null)' : String(d)}</td>)}<td className="break-words p-2">{formatValue(g.value, unit)}</td></tr>)}</tbody></table></div></details>}
    {(truncated || groups.length > 12 && !timeGrain) && <p className="text-xs text-slate-500">{truncated ? `Showing the first ${groups.length} of ${totalGroups} groups in ascending dimension order. ` : ''}{groups.length > 12 && !timeGrain ? 'Chart shows the first 12 supplied categories; the table includes every supplied group.' : ''}</p>}
  </div>
}



