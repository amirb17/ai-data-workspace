import { backendId, request, ApiError } from './client'
export interface ColumnEvidence {
  original_name: string; normalized_name: string; tokens: string[]; canonical_type: string
  nullable: boolean | null; approved_required: boolean; row_count: number; null_count: number; non_null_count: number
  null_ratio: number | null; distinct_count: number; distinct_ratio: number | null
  identifier_candidate: boolean; authoritative_business_key: boolean; business_key_position: number | null
  authoritative_event_time: boolean; datetime_candidate: boolean
  min_value: number | string | null; max_value: number | string | null
  numeric_statistics: { mean: number | null; median: number | null; standard_deviation: number | null; zero_count: number; negative_count: number } | null
  datetime_statistics: { min: string | null; max: string | null; timezone: string | null; timezone_aware: boolean | null } | null
  pattern_hints: { pattern: string; count: number; ratio: number }[]
  sensitivity_hints: { hint: string; confidence: string; basis: string }[]
  categorical_statistics: { values_redacted: boolean; top_values: { rank: number; value: null; count: number; percentage: number }[] } | null
}
export interface DatasetProfile {
  workspace_id: number; dataset_id: number; dataset_name: string
  freshness: 'NOT_PROFILED' | 'STALE' | 'PROFILING' | 'READY' | 'FAILED'; profile_ready: boolean
  current_state_id: number | null; state_version: number | null; schema_version: number | null
  profile_id: number | null; profile_version: number | null; algorithm_version: number; built_algorithm_version: number | null
  profile_timestamp: string | null; can_refresh: boolean
  summary: { row_count: number; column_count: number; trusted_rows: number; active_rows: number | null; inactive_rows: number | null
    load_strategy: string; business_key: string[]; event_time_column: string | null; snapshot_effective_at: string | null
    identifier_candidates: string[]; datetime_candidates: string[]; numeric_columns: string[]; potential_sensitive_fields: string[] } | null
  columns: ColumnEvidence[]
}
async function scoped(workspace: string,dataset: string,suffix: string,options: RequestInit = {}) {
  const data = await request<DatasetProfile>(`/workspaces/${backendId(workspace)}/datasets/${backendId(dataset)}/profile${suffix}`,options)
  if (data.workspace_id !== backendId(workspace) || data.dataset_id !== backendId(dataset)) throw new ApiError('Profile belongs to another dataset.')
  return data
}
export const getDatasetProfile = (workspace: string,dataset: string,signal?: AbortSignal) => scoped(workspace,dataset,'',{signal})
export const getProfileReadiness = (workspace: string,dataset: string,signal?: AbortSignal) => scoped(workspace,dataset,'/readiness',{signal})
export const refreshDatasetProfile = (workspace: string,dataset: string) => scoped(workspace,dataset,'/refresh',{method:'POST'})
