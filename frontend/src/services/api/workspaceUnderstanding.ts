import { backendId, request, ApiError } from './client'
import type { Classification } from './understanding'
export type WorkspaceDatasetRole = 'ENTITY_MASTER' | 'TRANSACTION' | 'EVENT' | 'REFERENCE' | 'SNAPSHOT' | 'BRIDGE_CANDIDATE' | 'AGGREGATE' | 'UNKNOWN'
export interface WorkspaceUnderstanding {
  workspace_id: number; workspace_name: string
  status: 'NOT_GENERATED' | 'STALE' | 'GENERATING' | 'READY' | 'FAILED'
  can_generate: boolean; failure_code: string | null; readiness_message: string
  coverage: { datasets_total: number; datasets_analyzed: number; datasets_excluded: number; partial: boolean; excluded: {dataset_id: number; dataset_name: string; reason: string}[] }
  source_pins: {dataset_id: number; dataset_name: string; profile_id: number; state_id: number; suggestion_id: number}[]
  suggestion: { suggestion_id: number; suggestion_version: number; algorithm_version: number; completed_at: string; provider: string; model: string
    reasoning: {domain: Classification; subdomain: Classification; overall_confidence: number
      business_processes: {name: string; confidence: number; contributing_datasets: number[]; rationale: string}[]
      entities: {canonical_name: string; confidence: number; contributing_datasets: number[]; rationale: string}[]
      dataset_roles: {dataset_id: number; role: WorkspaceDatasetRole; confidence: number; rationale: string}[]
      warnings: string[]; unresolved_questions: string[]}
  } | null
}
async function scoped(workspace: string, suffix: string, options: RequestInit = {}) {
  const data = await request<WorkspaceUnderstanding>(`/workspaces/${backendId(workspace)}/semantic-understanding${suffix}`,options)
  if (data.workspace_id !== backendId(workspace)) throw new ApiError('Understanding belongs to another workspace.')
  return data
}
export const getWorkspaceUnderstanding = (workspace: string, signal?: AbortSignal) => scoped(workspace,'',{signal})
export const getWorkspaceReadiness = (workspace: string, signal?: AbortSignal) => scoped(workspace,'/readiness',{signal})
export const generateWorkspaceUnderstanding = (workspace: string, signal?: AbortSignal) => scoped(workspace,'/generate',{method:'POST',signal})
