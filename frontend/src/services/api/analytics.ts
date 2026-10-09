import { backendId, request, ApiError } from './client'
export interface DatasetAnalytics {
  workspace_id: number; dataset_id: number; analytics_ready: boolean
  freshness: 'NOT_READY' | 'STALE' | 'REFRESHING' | 'FRESH' | 'FAILED'
  current_state_id: number | null; state_version: number | null; trusted_rows: number | null
  active_rows: number | null; analytics_rows: number | null; load_strategy: string | null
  latest_source_file_name: string | null; state_updated_at: string | null; analytics_built_at: string | null
  built_from_state_id: number | null; gold_run_id: number | null; failure_code: string | null; can_refresh: boolean
  kpis?: { kpi_name: string; label: string; value: number | null }[]
  suggested_questions?: { question: string }[]
}
function path(workspace: string, dataset: string) { return `/workspaces/${backendId(workspace)}/datasets/${backendId(dataset)}/analytics` }
async function scoped(workspace: string, dataset: string, suffix: string, options: RequestInit = {}) {
  const data = await request<DatasetAnalytics>(path(workspace,dataset)+suffix, options)
  if (data.workspace_id !== backendId(workspace) || data.dataset_id !== backendId(dataset)) throw new ApiError('Analytics belongs to another dataset.')
  return data
}
export const getDatasetAnalytics = (workspace: string, dataset: string, signal?: AbortSignal) => scoped(workspace,dataset,'',{signal})
export const getAnalyticsReadiness = (workspace: string, dataset: string, signal?: AbortSignal) => scoped(workspace,dataset,'/readiness',{signal})
export const refreshDatasetAnalytics = (workspace: string, dataset: string) => scoped(workspace,dataset,'/refresh',{method:'POST'})
