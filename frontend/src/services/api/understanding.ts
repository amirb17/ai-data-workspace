import { backendId, request, ApiError } from './client'
export type SemanticRole = 'IDENTIFIER' | 'ENTITY_REFERENCE' | 'DIMENSION' | 'MEASURE' | 'TIME_DIMENSION' | 'STATUS' | 'CATEGORY' | 'CODE' | 'BOOLEAN_FLAG' | 'TEXT_ATTRIBUTE' | 'UNKNOWN'
export interface Candidate { label: string | null; confidence: number; rationale: string }
export interface Classification { primary: Candidate; alternatives: Candidate[] }
export interface Understanding {
  workspace_id: number; dataset_id: number; dataset_name: string
  status: 'NOT_GENERATED' | 'STALE' | 'GENERATING' | 'READY' | 'FAILED'
  profile_freshness: string; can_generate: boolean; failure_code: string | null
  source_profile_id: number | null; source_state_id: number | null
  suggestion: { suggestion_id: number; suggestion_version: number; source_profile_id: number; source_state_version: number
    semantic_model_version: number; provider: string; model: string; resolved_model: string | null
    completed_at: string; compact_mode: boolean; input_bytes: number
    reasoning: { domain: Classification; subdomain: Classification; entity: Classification; overall_confidence: number
      columns: { column_name: string; suggested_role: SemanticRole; secondary_hints: SemanticRole[]; business_meaning: string
        confidence: number; rationale: string; warnings: string[] }[]; warnings: string[]; unresolved_questions: string[] }
  } | null
  evidence: { original_name: string; canonical_type: string; null_ratio: number | null; distinct_ratio: number | null
    authoritative_business_key: boolean; business_key_position: number | null; authoritative_event_time: boolean; sensitivity_hints: unknown[] }[]
}
async function scoped(workspace: string, dataset: string, suffix: string, options: RequestInit = {}) {
  const data = await request<Understanding>(`/workspaces/${backendId(workspace)}/datasets/${backendId(dataset)}/semantic-understanding${suffix}`, options)
  if (data.workspace_id !== backendId(workspace) || data.dataset_id !== backendId(dataset)) throw new ApiError('Understanding belongs to another dataset.')
  return data
}
export const getUnderstanding = (workspace: string, dataset: string, signal?: AbortSignal) => scoped(workspace, dataset, '', {signal})
export const getUnderstandingReadiness = (workspace: string, dataset: string, signal?: AbortSignal) => scoped(workspace, dataset, '/readiness', {signal})
export const generateUnderstanding = (workspace: string, dataset: string) => scoped(workspace, dataset, '/generate', {method:'POST'})
