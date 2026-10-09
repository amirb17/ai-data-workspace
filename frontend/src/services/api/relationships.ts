import { backendId, request, ApiError } from './client'
export interface Endpoint { dataset_id: number; dataset_name: string; columns: string[]; normalized_columns: string[] }
export interface Statistics { rows: number; non_null_rows: number; distinct_keys: number; duplicate_rows: number; null_ratio: number | null; uniqueness_ratio: number | null }
export interface RelationshipEvidence {
  workspace_id: number; candidate_key: string; parent: Endpoint; child: Endpoint
  candidate_cardinality: 'ONE_TO_ONE' | 'ONE_TO_MANY' | 'MANY_TO_ONE' | 'MANY_TO_MANY_CANDIDATE' | 'UNKNOWN'
  deterministic_score: number; can_confirm: boolean; warnings: string[]
  source_pins: { dataset_id: number; state_version: number; profile_version: number }[]
  score_components: { name: string; points: number; explanation: string }[]
  signals: { datatype_compatible: boolean; parent_type: string; child_type: string; configured_parent_key: boolean
    parent: Statistics; child: Statistics; parent_semantic_role: string | null; child_semantic_role: string | null
    overlap: { method: string; child_non_null_distinct_keys: number; matched_distinct_keys: number; missing_distinct_keys: number; child_to_parent_coverage: number | null; parent_referenced_ratio: number | null } }
}
export interface Candidate { candidate_id: number; run_id: number; candidate_status: string; evidence: RelationshipEvidence; review_status: 'REVIEW_REQUIRED' | 'CONFIRMED' | 'REJECTED'; review_version: number }
export interface RelationshipsView {
  workspace_id: number; status: 'NOT_GENERATED' | 'STALE' | 'DISCOVERING' | 'READY' | 'FAILED'
  run_id: number | null; run_version: number | null; failure_code: string | null; can_discover: boolean; readiness_message: string
  coverage: { datasets_total: number; datasets_analyzed: number; datasets_excluded: number; partial: boolean; excluded: {dataset_id: number; dataset_name: string; reason: string}[] }
  semantic_context: { workspace_semantics: string; workspace_domain: string | null; dataset_semantics_available: number; ai_ranking: string }
  limits: Record<string, number>; candidates: Candidate[]; source_pins: Record<string, number | null>[]; review_revision: number
  reviewed_relationships: { candidate_id: number; relationship_version: number; status: string; reviewed_at: string; evidence: RelationshipEvidence; structural_status: string; verification_status: string }[]
}
async function scoped(workspace: string, suffix: string, options: RequestInit = {}) {
  const data = await request<RelationshipsView>(`/workspaces/${backendId(workspace)}/relationships${suffix}`,options)
  if(data.workspace_id !== backendId(workspace)) throw new ApiError('Relationships belong to another workspace.')
  return data
}
export const getRelationships = (workspace: string, signal?: AbortSignal) => scoped(workspace,'',{signal})
export const getRelationshipReadiness = (workspace: string, signal?: AbortSignal) => scoped(workspace,'/readiness',{signal})
export const discoverRelationships = (workspace: string, signal?: AbortSignal) => scoped(workspace,'/discover',{method:'POST',signal})
export const reviewRelationship = (workspace: string, candidate: Candidate, action: 'confirm' | 'reject', signal?: AbortSignal) => scoped(workspace,`/candidates/${candidate.candidate_id}/${action}`,{method:'POST',body:JSON.stringify({expected_review_version:candidate.review_version}),signal})
