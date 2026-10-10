import { backendId, request, ApiError } from './client'
import type { MetricDefinition } from './metrics'

export interface MetricResult {
  candidate_id: number
  definition_version: number
  definition: MetricDefinition
  decision: 'AUTO_ACCEPT' | 'REVIEW_RECOMMENDED' | 'REVIEW_REQUIRED'
  required_datasets: number[]
  source_names?: {dataset_id: number; dataset_name: string}[]
  status: 'NOT_COMPUTED' | 'STALE' | 'COMPUTING' | 'FRESH' | 'FAILED' | 'BLOCKED'
  reason: string | null
  review_reasons: string[]
  result_id: number | null
  computed_at: string | null
  can_retry: boolean
  output: {kind: 'scalar' | 'grouped'; value: number | string | null; groups: {dimensions: (number | string | boolean | null)[]; value: number | string | null}[]; input_rows: number; total_groups: number; truncated: boolean; display_hint: string} | null
}
export interface WorkspaceAnalytics {
  workspace_id: number
  status: string
  metrics_total: number
  metrics_available: number
  metrics_fresh: number
  metrics_stale: number
  metrics_failed: number
  metrics_blocked: number
  last_refreshed: string | null
  can_refresh: boolean
  metrics: MetricResult[]
}
async function scoped(workspace: string, suffix: string, options: RequestInit = {}) {
  const data = await request<WorkspaceAnalytics>(`/workspaces/${backendId(workspace)}/analytics${suffix}`, options)
  if (data.workspace_id !== backendId(workspace)) throw new ApiError('Analytics belong to another workspace.')
  return data
}
export const getWorkspaceAnalytics = (workspace: string, signal?: AbortSignal) => scoped(workspace, '', {signal})
export const refreshWorkspaceAnalytics = (workspace: string, signal?: AbortSignal) => scoped(workspace, '/refresh', {method: 'POST', signal})
export const retryWorkspaceMetric = (workspace: string, candidate: number, signal?: AbortSignal) => scoped(workspace, `/metrics/${candidate}/retry`, {method: 'POST', signal})
