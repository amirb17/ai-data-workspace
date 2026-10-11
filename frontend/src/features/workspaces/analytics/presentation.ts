import type { MetricResult } from '../../../services/api/workspaceAnalytics'

export const metricStatus = {NOT_COMPUTED: 'Not calculated yet', STALE: 'Refresh required', COMPUTING: 'Calculating…', FRESH: 'Current', FAILED: 'Could not be calculated', BLOCKED: 'Needs definition'}
export const readinessLabel = {NO_DATA: 'No trusted data yet', NOT_READY: 'Analytics setup in progress', PARTIAL: 'Some analytics are available', STALE: 'Refresh required', REFRESHING: 'Refreshing analytics…', READY: 'Analytics are current', NEEDS_ATTENTION: 'Some metrics need attention', FAILED: 'Analytics could not be refreshed'}
export function formatValue(value: number | string | null, unit = 'NUMBER') {
  if (value === null) return 'Unavailable'
  const text = typeof value === 'number' ? value.toLocaleString(undefined, {maximumFractionDigits: unit === 'PERCENTAGE' ? 1 : 2}) : /^-?\d+$/.test(value) ? BigInt(value).toLocaleString() : value
  return text + (unit === 'PERCENTAGE' ? '%' : '')
}
export function selectMetrics(metrics: MetricResult[]) {
  const safe = metrics.filter(m => m.status === 'FRESH' && m.output && m.decision !== 'REVIEW_REQUIRED')
    .sort((a, b) => Number(a.decision !== 'AUTO_ACCEPT') - Number(b.decision !== 'AUTO_ACCEPT') || (b.definition.confidence || 0) - (a.definition.confidence || 0) || a.candidate_id - b.candidate_id)
  return {kpis: safe.filter(m => m.output?.kind === 'scalar').slice(0, 6), charts: safe.filter(m => m.output?.kind === 'grouped').slice(0, 4), attention: metrics.filter(m => ['STALE', 'FAILED', 'BLOCKED'].includes(m.status) || m.decision === 'REVIEW_REQUIRED')}
}
export type ResultGroup = {dimensions: (string | number | boolean | null)[]; value: number | string | null}
export function plotRows(groups: ResultGroup[], timeGrain?: string) {
  const rows = groups.map(g => ({name: g.dimensions.map(d => d === null ? '(null)' : String(d)).join(' · '), value: g.value as number | null}))
  if (!timeGrain) return rows.slice(0, 12)
  if (rows.some(r => !/^\d{4}-\d{2}-\d{2}/.test(r.name) || !Number.isFinite(Date.parse(r.name.slice(0, 10) + 'T00:00:00Z')))) return []
  rows.sort((a, b) => a.name.localeCompare(b.name))
  const result: typeof rows = []
  rows.forEach((row, i) => {
    if (i) {
      const previous = new Date(rows[i - 1].name.slice(0, 10) + 'T00:00:00Z')
      if (timeGrain === 'DAY' || timeGrain === 'WEEK') previous.setUTCDate(previous.getUTCDate() + (timeGrain === 'DAY' ? 1 : 7))
      else previous.setUTCMonth(previous.getUTCMonth() + ({MONTH: 1, QUARTER: 3, YEAR: 12}[timeGrain] || 1))
      if (previous.toISOString().slice(0, 10) < row.name.slice(0, 10)) result.push({name: 'Missing bucket', value: null})
    }
    result.push(row)
  })
  return result
}
