import type { ColumnEvidence } from '../../../services/api/profile'
const ratio = (value: number | null) => value == null ? '—' : `${(100*value).toLocaleString(undefined,{maximumFractionDigits:2})}%`
export function ColumnEvidenceCard({column:c}: {column: ColumnEvidence}) {
  return <article className="min-w-0 space-y-3 rounded-xl border border-slate-200 bg-white p-4 [overflow-wrap:anywhere]">
    <h3 className="font-semibold text-slate-950">{c.original_name}</h3>
    <dl className="grid grid-cols-2 gap-3 text-sm"><div><dt className="text-slate-500">Type</dt><dd>{c.canonical_type === 'DATETIME' ? 'Date/time' : c.canonical_type.toLowerCase()}</dd></div><div><dt className="text-slate-500">Unique non-missing values</dt><dd>{ratio(c.distinct_ratio)}</dd></div><div><dt className="text-slate-500">Missing</dt><dd>{ratio(c.null_ratio)} ({c.null_count.toLocaleString()})</dd></div><div><dt className="text-slate-500">Distinct values</dt><dd>{c.distinct_count.toLocaleString()}</dd></div></dl>
    <div className="flex flex-wrap gap-2 text-xs text-indigo-800">{c.authoritative_business_key && <span>Configured business key · position {c.business_key_position}</span>}{c.authoritative_event_time && <span>Configured event time</span>}{c.approved_required && <span>Approved required field</span>}{c.identifier_candidate && <span>Potential identifier</span>}</div>
    {c.min_value != null && <p className="text-sm">Range: {String(c.min_value)} – {String(c.max_value)}</p>}
    {c.numeric_statistics && <p className="text-sm">Mean: {c.numeric_statistics.mean?.toLocaleString() ?? '—'} · Median: {c.numeric_statistics.median?.toLocaleString() ?? '—'} · Zero: {c.numeric_statistics.zero_count} · Negative: {c.numeric_statistics.negative_count}</p>}
    {c.datetime_statistics && <p className="text-sm">Timezone: {c.datetime_statistics.timezone ?? 'Not available; no timezone inferred'}</p>}
    {c.pattern_hints.length>0 && <p className="text-sm text-slate-600">Detected patterns: {c.pattern_hints.map(p=>`${p.pattern.replaceAll('_',' ')} (${p.count})`).join(', ')}</p>}
    {c.categorical_statistics && <p className="text-sm text-slate-600">Top category frequencies: {c.categorical_statistics.top_values.map(v=>`${v.count} (${v.percentage.toFixed(1)}%)`).join(', ') || 'No values'}. Labels are withheld for privacy.</p>}
    {c.sensitivity_hints.length>0 && <div className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900"><p className="font-medium">Potential sensitive field</p><p>DataRise detected a pattern or name hint that may represent sensitive information. Review before using this field in AI-assisted features.</p><p>{c.sensitivity_hints.map(h=>`${h.hint.replaceAll('_',' ')} (${h.confidence==='LOW' ? 'low confidence, name only' : 'pattern evidence only'})`).join(', ')}</p></div>}
  </article>
}
